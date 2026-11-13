from __future__ import annotations

import json
import math
import uuid
from datetime import date, datetime
from typing import Any, NoReturn

from cryptography.fernet import InvalidToken
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    Event,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMeasureSecret,
    IsmsEffectivenessMetricEntry,
)
from app.integrations.errors import IntegrationError
from app.integrations.transport import decrypt
from app.security import signed_webhooks
from app.security.rate_limit import address_window_allow
from app.security.redaction import (
    mask_event_data_obj,
    mask_event_data_str,
    redact_obj,
    redact_str,
    redact_url,
)
from app.security.request_limits import MAX_METRIC_SUBMISSION
from app.services import inbox
from app.services.context import ChangeContext
from app.services.entity_changelog import record_entity_changelog
from app.services.errors import DomainError
from app.services.ingestion_pause import require_receiving
from app.services.isms.metric_values import (
    inherited_metric_source_type,
    submitted_metric_source_type,
)

# Each client address may make this many requests a minute against each of the
# webhook allowances: verified deliveries per provider (see the webhook routes),
# verified metric submissions, and refused deliveries of every kind.
ADDRESS_ALLOWANCE = 100
METRIC_ALLOWANCE = "webhook:isms_metrics"
REFUSED_ALLOWANCE = "webhook:refused"


def spend_address_allowance(redis, scope: str, client_ip: str | None) -> None:
    ok, retry = address_window_allow(
        redis, scope, client_ip, ADDRESS_ALLOWANCE, 60, fail_closed=True
    )
    if not ok:
        raise DomainError(
            429, "Webhook rate limit exceeded", headers={"Retry-After": str(retry)}
        )


def refuse(redis, client_ip: str | None, detail: str) -> NoReturn:
    """Refuse an unverified request, counting it against its address.

    Refusals have their own allowance, so a sender without the secret cannot
    spend the allowance that verified deliveries from a shared address use.
    """
    spend_address_allowance(redis, REFUSED_ALLOWANCE, client_ip)
    raise DomainError(status_code=403, detail=detail)


def _webhook_parse_date(value: Any) -> date | None:
    # Values come from a decoded JSON body, which holds no date objects.
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        raise DomainError(status_code=400, detail=f"Invalid date value: {value}")


def _webhook_parse_uuid(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except Exception:
        raise DomainError(status_code=400, detail=f"Invalid UUID value: {value}")


def _webhook_parse_datetime(value: Any) -> datetime | None:
    # Values come from a decoded JSON body, which holds no datetime objects.
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        raise DomainError(status_code=400, detail=f"Invalid datetime value: {value}")


def _webhook_clean_text(value: Any, limit: int) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) > limit:
        raise DomainError(status_code=400, detail="Payload text field is too long")
    return text


def _webhook_metric_value(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError()
        return result
    except Exception:
        raise DomainError(
            status_code=400, detail="metric_value must be a finite number"
        )


def _webhook_coerce_metric_source_type(db: Session, raw: Any) -> str:
    source_type = _webhook_clean_text(raw or "other", 64)
    resolved = submitted_metric_source_type(db, source_type)
    if resolved is None:
        raise DomainError(
            status_code=400,
            detail="Invalid source_type; choose an existing Keen source or other",
        )
    return resolved


def _submission_target(
    db: Session, metric_key: str
) -> tuple[IsmsEffectivenessMeasure | None, str | None]:
    """Return the measure a submission names and its secret, without failing.

    Metric keys are unique across frameworks. An unknown measure, one without a
    secret and one whose secret cannot be read all leave nothing to sign with,
    so the caller refuses them alike.
    """
    cleaned = str(metric_key or "").strip()
    try:
        named = IsmsEffectivenessMeasure.id == uuid.UUID(cleaned)
    except ValueError:
        named = func.lower(IsmsEffectivenessMeasure.metric_key) == cleaned.lower()
    # One query whether or not the measure exists or has a secret.
    found = (
        db.query(IsmsEffectivenessMeasure, IsmsEffectivenessMeasureSecret)
        .outerjoin(
            IsmsEffectivenessMeasureSecret,
            IsmsEffectivenessMeasureSecret.measure_id == IsmsEffectivenessMeasure.id,
        )
        .filter(named)
        .one_or_none()
    )
    if found is None:
        return None, None
    measure, stored = found
    try:
        secret = decrypt(stored.secret_encrypted) if stored else None
    except (IntegrationError, InvalidToken, ValueError):
        secret = None
    return measure, secret if isinstance(secret, str) else None


def submit_signed_metric(
    *,
    db: Session,
    redis,
    metric_key: str,
    timestamps: list[str],
    signatures: list[str],
    body: bytes,
    context: ChangeContext,
    now: float | None = None,
):
    """Record a reporting job's signed submission once.

    The signature covers the timestamp and the exact body under the measure's
    own secret. Nothing about the measure, the ingestion state or the payload is
    revealed before it verifies.
    """
    try:
        timestamp, signature = signed_webhooks.presented(timestamps, signatures, now)
    except signed_webhooks.SignatureRefused as refused:
        refuse(redis, context.client_ip, str(refused))
    if len(body) > MAX_METRIC_SUBMISSION:
        raise DomainError(status_code=413, detail="Submissions are limited to 256 KiB")
    measure, secret = _submission_target(db, metric_key)
    # The comparison runs even without a secret, so refusals take as long.
    signed = signed_webhooks.matches(secret, timestamp, body, signature)
    if measure is None or not signed:
        refuse(redis, context.client_ip, "Invalid webhook signature")
    spend_address_allowance(redis, METRIC_ALLOWANCE, context.client_ip)
    require_receiving(db, "webhooks")
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except (ValueError, RecursionError):
        raise DomainError(status_code=400, detail="Expected JSON payload") from None
    scope = f"isms-metric:{measure.id}"
    try:
        claimed = signed_webhooks.claim(redis, scope, signature)
    except Exception:
        raise DomainError(
            503,
            "Submissions cannot be checked for replay right now; retry later.",
            headers={"Retry-After": "60"},
        ) from None
    if not claimed:
        raise DomainError(
            status_code=409, detail="This submission was already received"
        )
    try:
        return record_metric(db=db, measure=measure, payload=payload, context=context)
    except BaseException:
        # A submission that was not recorded leaves its retry acceptable.
        signed_webhooks.release(redis, scope, signature)
        raise


def record_metric(
    *, db, measure: IsmsEffectivenessMeasure, payload, context: ChangeContext
):
    from app.services.features import state

    state(db).require("effectiveness")
    if not isinstance(payload, dict):
        raise DomainError(status_code=400, detail="Expected JSON object payload")
    framework = _webhook_clean_text(
        payload.get("framework")
        or payload.get("framework_slug")
        or settings.default_framework_slug,
        64,
    )
    # A measure is reached within its own framework only.
    if measure.framework_slug != framework:
        raise DomainError(status_code=404, detail="Effectiveness measure not found")
    from app.services.isms.effectiveness import measure_quiz

    if measure_quiz(db, measure.id) is not None:
        raise DomainError(
            status_code=409,
            detail="This measure records its quiz's results; it takes no submissions",
        )
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
        raise DomainError(
            status_code=400,
            detail="metric_value/value or qualitative_value/text is required",
        )
    period_start = _webhook_parse_date(payload.get("period_start"))
    period_end = _webhook_parse_date(payload.get("period_end"))
    if period_start and period_end and period_end < period_start:
        raise DomainError(
            status_code=400, detail="period_end must be after period_start"
        )
    # A submission that names no source inherits the measure's expected source.
    source_type = (
        _webhook_coerce_metric_source_type(db, payload["source_type"])
        if payload.get("source_type")
        else inherited_metric_source_type(db, measure, None)
    )
    source_event_id = _webhook_parse_uuid(
        payload.get("source_event_id") or payload.get("event_id")
    )
    if (
        source_event_id
        and not db.query(Event.id).filter(Event.id == source_event_id).first()
    ):
        raise DomainError(status_code=400, detail="Unknown source_event_id")
    from app.security.urls import external_url

    try:
        source_url = external_url(payload.get("source_url") or payload.get("url"))
    except (ValueError, TypeError, AttributeError):
        raise DomainError(400, "Use an HTTP or HTTPS source URL") from None
    if source_url:
        # Validation rejects embedded credentials; recognised secret query and
        # fragment parameters are removed like other ingested URLs.
        source_url = redact_url(source_url)
    # Extracted columns, the payload copy and the changelog receive the same
    # protection as the shared ingestion path.
    mask = (settings.event_data_masking or "false") == "true"

    def protect(value: str) -> str:
        value = redact_str(value) or ""
        return (mask_event_data_str(value) or "") if mask else value

    raw_payload = redact_obj(payload) or {}
    if mask:
        raw_payload = mask_event_data_obj(raw_payload) or {}
    row = IsmsEffectivenessMetricEntry(
        measure_id=measure.id,
        recorded_at=_webhook_parse_datetime(payload.get("recorded_at"))
        or utc_now_naive(),
        period_start=period_start,
        period_end=period_end,
        metric_value=metric_value,
        metric_unit=protect(
            _webhook_clean_text(
                payload.get(
                    "metric_unit", payload.get("unit") or measure.target_unit or ""
                ),
                64,
            )
        ),
        qualitative_value=protect(qualitative_value),
        source_type=source_type,
        source_title=protect(
            _webhook_clean_text(
                payload.get("source_title") or payload.get("title") or "", 256
            )
        ),
        source_url=source_url,
        source_reference=protect(
            _webhook_clean_text(
                payload.get("source_reference") or payload.get("reference") or "",
                256,
            )
        ),
        source_event_id=source_event_id,
        notes=protect(_webhook_clean_text(payload.get("notes"), 12000)),
        raw_payload=raw_payload,
        created_at=utc_now_naive(),
        updated_at=utc_now_naive(),
    )
    db.add(row)
    measure.updated_at = utc_now_naive()
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
        request_method=context.method,
        request_path=context.path,
    )
    inbox.measure_result(db, measure, None)
    db.commit()
    return {"ok": True, "metric_entry": out}
