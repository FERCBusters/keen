from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.valkey import get_valkey
from app.db.models import Event, IsmsEffectivenessMeasure, IsmsEffectivenessMetricEntry
from app.db.session import get_db
from app.services.entity_changelog import record_entity_changelog
from app.ingest.webhooks import ingest_webhook, verify_secret
from app.security.rate_limit import fixed_window_allow

router = APIRouter()


def _webhook_parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid date value: {value}")


def _webhook_parse_uuid(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid UUID value: {value}")


def _webhook_parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid datetime value: {value}")


def _webhook_clean_text(value: Any, limit: int) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) > limit:
        raise HTTPException(status_code=400, detail="Payload text field is too long")
    return text


def _webhook_metric_value(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        raise HTTPException(status_code=400, detail="metric_value must be numeric")


def _webhook_actual_source_names(db: Session) -> list[str]:
    rows = (
        db.query(Event.source)
        .filter(Event.source.isnot(None), func.length(func.trim(Event.source)) > 0)
        .group_by(Event.source)
        .all()
    )
    return [str(src).strip() for (src,) in rows if str(src or "").strip()]


def _webhook_coerce_metric_source_type(db: Session, raw: Any) -> str:
    source_type = _webhook_clean_text(raw or "other", 64)
    if not source_type or source_type.lower() == "other":
        return "other"
    sources = _webhook_actual_source_names(db)
    for source in sources:
        if source_type == source:
            return source
    source_key = source_type.lower().replace("-", "_")
    for source in sources:
        if source.lower().replace("-", "_") == source_key:
            return source
    raise HTTPException(
        status_code=400,
        detail="Invalid source_type; choose an existing KEEN source or other",
    )


def _webhook_measure_by_key(
    db: Session, *, metric_key: str, framework: str
) -> IsmsEffectivenessMeasure:
    cleaned = str(metric_key or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="metric_key is required")
    mid = None
    try:
        mid = uuid.UUID(cleaned)
    except Exception:
        mid = None
    qry = db.query(IsmsEffectivenessMeasure).filter(
        IsmsEffectivenessMeasure.framework_slug == framework
    )
    row = (
        qry.filter(IsmsEffectivenessMeasure.id == mid).one_or_none()
        if mid
        else qry.filter(
            func.lower(IsmsEffectivenessMeasure.metric_key) == cleaned.lower()
        ).one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Effectiveness measure not found")
    return row


@router.post("/v1/webhooks/isms/effectiveness-metrics/{metric_key}")
async def isms_effectiveness_metric_webhook(
    metric_key: str, request: Request, db: Session = Depends(get_db)
):
    """Record an ISMS effectiveness metric entry from an external scheduler/tool.

    Configure provider ``isms_metrics`` in config/webhooks.yml. ``source_type``
    must be an existing KEEN evidence source, or ``other``. A typical payload is:
    {
      "value": 42,
      "unit": "requests",
      "period_start": "2026-05-01",
      "period_end": "2026-05-31",
      "source_type": "loki",
      "source_title": "ossec-reportd May 2026",
      "source_url": "https://grafana.example/..."
    }
    """
    headers = {k: v for k, v in request.headers.items()}
    if not verify_secret("isms_metrics", headers):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Expected JSON payload")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object payload")
    framework = _webhook_clean_text(
        payload.get("framework")
        or payload.get("framework_slug")
        or settings.default_framework_slug,
        64,
    )
    measure = _webhook_measure_by_key(db, metric_key=metric_key, framework=framework)
    metric_value = _webhook_metric_value(
        payload.get("metric_value", payload.get("value"))
    )
    qualitative_value = _webhook_clean_text(
        payload.get("qualitative_value")
        or payload.get("text")
        or payload.get("status"),
        20000,
    )
    if metric_value is None and not qualitative_value:
        raise HTTPException(
            status_code=400,
            detail="metric_value/value or qualitative_value/text is required",
        )
    period_start = _webhook_parse_date(payload.get("period_start"))
    period_end = _webhook_parse_date(payload.get("period_end"))
    if period_start and period_end and period_end < period_start:
        raise HTTPException(
            status_code=400, detail="period_end must be after period_start"
        )
    source_type = _webhook_coerce_metric_source_type(db, payload.get("source_type"))
    source_event_id = _webhook_parse_uuid(
        payload.get("source_event_id") or payload.get("event_id")
    )
    if (
        source_event_id
        and not db.query(Event.id).filter(Event.id == source_event_id).first()
    ):
        raise HTTPException(status_code=400, detail="Unknown source_event_id")
    row = IsmsEffectivenessMetricEntry(
        measure_id=measure.id,
        recorded_at=_webhook_parse_datetime(payload.get("recorded_at"))
        or datetime.utcnow(),
        period_start=period_start,
        period_end=period_end,
        metric_value=metric_value,
        metric_unit=_webhook_clean_text(
            payload.get(
                "metric_unit", payload.get("unit") or measure.target_unit or ""
            ),
            64,
        ),
        qualitative_value=qualitative_value,
        source_type=source_type,
        source_title=_webhook_clean_text(
            payload.get("source_title") or payload.get("title") or "", 256
        ),
        source_url=_webhook_clean_text(
            payload.get("source_url") or payload.get("url"), 2048
        )
        or None,
        source_reference=_webhook_clean_text(
            payload.get("source_reference") or payload.get("reference") or "", 256
        ),
        source_event_id=source_event_id,
        notes=_webhook_clean_text(payload.get("notes"), 12000),
        raw_payload=payload,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    measure.updated_at = datetime.utcnow()
    db.add(measure)
    db.flush()
    out = {
        "id": str(row.id),
        "measure_id": str(measure.id),
        "metric_key": measure.metric_key,
        "metric_value": row.metric_value,
        "metric_unit": row.metric_unit,
        "qualitative_value": row.qualitative_value,
        "source_type": row.source_type,
        "source_title": row.source_title,
        "source_url": row.source_url,
        "source_reference": row.source_reference,
        "source_event_id": str(row.source_event_id) if row.source_event_id else None,
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        "period_start": row.period_start.isoformat() if row.period_start else None,
        "period_end": row.period_end.isoformat() if row.period_end else None,
    }
    record_entity_changelog(
        db,
        entity_type="isms_effectiveness_metric",
        entity_id=row.id,
        action="created",
        before=None,
        after=out,
        user=None,
        request_method=request.method,
        request_path=request.url.path,
    )
    db.commit()
    return {"ok": True, "metric_entry": out}


@router.post("/v1/webhooks/{provider}/{event_type}")
async def webhook_ingest(
    provider: str, event_type: str, request: Request, db: Session = Depends(get_db)
):
    # Rate limiting: 100 requests per minute per IP
    # Fail closed to prevent DoS during Redis outages
    ip = request.client.host if request.client else "unknown"
    r = get_valkey()
    ok, retry = fixed_window_allow(
        r, f"keen:rl:webhook:{provider}:ip:{ip}", 100, 60, fail_closed=True
    )
    if not ok:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {retry} seconds.",
            headers={"Retry-After": str(retry)},
        )

    headers = {k: v for k, v in request.headers.items()}
    if not verify_secret(provider, headers):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    body = await request.body()
    try:
        return ingest_webhook(
            db, provider=provider, event_type=event_type, body=body, headers=headers
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
