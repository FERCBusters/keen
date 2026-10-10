"""ISMS metric values; see docs/maintainability-review.md for module boundaries."""

from __future__ import annotations

import re
import uuid

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.source_meta import apply_user_source_overrides, get_source_meta
from app.db.models import (
    Event,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    User,
)

from .common import (
    _clean_text,
    _utcnow,
)
from .constants import (
    EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE,
    EFFECTIVENESS_THRESHOLD_OPERATORS,
)
from .schemas import (
    EffectivenessMeasurePayload,
    EffectivenessMetricEntryPayload,
)


def _metric_key_from_payload(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if len(value) > 128 or not re.match(r"^[A-Za-z0-9._:-]+$", value):
        raise HTTPException(
            status_code=400,
            detail="metric_key may only contain letters, numbers, dot, underscore, colon and hyphen",
        )
    return value


def _validate_effectiveness_measure_payload(
    db: Session,
    payload: EffectivenessMeasurePayload,
    *,
    fields: set[str] | None = None,
    row_id: uuid.UUID | None = None,
) -> None:
    if (fields is None or "owner_user_id" in fields) and payload.owner_user_id:
        if (
            not db.query(User.id)
            .filter(User.id == payload.owner_user_id, User.is_active.is_(True))
            .first()
        ):
            raise HTTPException(status_code=400, detail="Unknown owner")
    if fields is None or "threshold_operator" in fields:
        op = (payload.threshold_operator or "").strip()
        if op not in EFFECTIVENESS_THRESHOLD_OPERATORS:
            raise HTTPException(status_code=400, detail="Invalid threshold operator")
    if fields is None or "metric_key" in fields:
        key = _metric_key_from_payload(payload.metric_key)
        if key:
            qry = db.query(IsmsEffectivenessMeasure.id).filter(
                func.lower(IsmsEffectivenessMeasure.metric_key) == key.lower()
            )
            if row_id:
                qry = qry.filter(IsmsEffectivenessMeasure.id != row_id)
            if qry.first():
                raise HTTPException(status_code=400, detail="metric_key already exists")
    if (fields is None or "clauses" in fields) and payload.clauses:
        raise HTTPException(
            status_code=400,
            detail="Effectiveness measures link controls only; clauses are inferred from those controls",
        )


def _apply_effectiveness_measure_payload(
    row: IsmsEffectivenessMeasure,
    payload: EffectivenessMeasurePayload,
    *,
    partial: bool = False,
) -> None:
    fields = (
        set(payload.model_fields_set or set())
        if partial
        else {
            "summary",
            "description",
            "effectiveness_measure",
            "metric",
            "metric_key",
            "target_value",
            "target_unit",
            "threshold_operator",
            "owner_user_id",
            "frequency",
            "notes",
        }
    )
    if "summary" in fields:
        row.summary = _clean_text(payload.summary, max_len=20000)
    if "description" in fields:
        row.description = _clean_text(payload.description, max_len=40000)
    if "effectiveness_measure" in fields:
        row.effectiveness_measure = _clean_text(
            payload.effectiveness_measure,
            max_len=20000,
            required=not partial,
            label="effectiveness measure",
        )
    if "metric" in fields:
        row.metric = _clean_text(
            payload.metric, max_len=20000, required=not partial, label="metric"
        )
    if "metric_key" in fields:
        row.metric_key = _metric_key_from_payload(payload.metric_key)
    if "target_value" in fields:
        row.target_value = payload.target_value
    if "target_unit" in fields:
        row.target_unit = _clean_text(payload.target_unit, max_len=64)
    if "threshold_operator" in fields:
        row.threshold_operator = (payload.threshold_operator or "").strip()
    if "owner_user_id" in fields:
        row.owner_user_id = payload.owner_user_id
    if "frequency" in fields:
        row.frequency = _clean_text(payload.frequency, max_len=64)
    if "notes" in fields:
        row.notes = _clean_text(payload.notes, max_len=12000)
    row.updated_at = _utcnow()


def _actual_effectiveness_metric_source_names(db: Session) -> list[str]:
    rows = (
        db.query(Event.source)
        .filter(Event.source.isnot(None), func.length(func.trim(Event.source)) > 0)
        .group_by(Event.source)
        .order_by(func.lower(Event.source).asc())
        .all()
    )
    return [str(src).strip() for (src,) in rows if str(src or "").strip()]


def _effectiveness_metric_sources_out(
    db: Session, user: User | None = None
) -> list[dict[str, str]]:
    overrides = (
        getattr(user, "pref_source_colors", None) if isinstance(user, User) else None
    )
    items: list[dict[str, str]] = []
    for source in _actual_effectiveness_metric_source_names(db):
        meta = apply_user_source_overrides(get_source_meta(source), overrides)
        items.append(
            {
                "id": source,
                "source": source,
                "label": meta.label,
                "color": meta.color,
            }
        )
    items.append(
        {
            "id": EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE,
            "source": EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE,
            "label": "Other",
            "color": "",
        }
    )
    return items


def _coerce_metric_source_type(db: Session, raw: str | None) -> str:
    source_type = _clean_text(raw, max_len=64).strip()
    if not source_type:
        return EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE
    if source_type.lower() == EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE:
        return EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE
    sources = _actual_effectiveness_metric_source_names(db)
    for source in sources:
        if source_type == source:
            return source
    source_key = source_type.lower().replace("-", "_")
    for source in sources:
        if source.lower().replace("-", "_") == source_key:
            return source
    raise HTTPException(
        status_code=400,
        detail="Invalid metric source type; choose an existing KEEN source or other",
    )


def _validate_metric_entry_payload(
    db: Session, payload: EffectivenessMetricEntryPayload, *, partial: bool = False
) -> None:
    fields = set(payload.model_fields_set or set()) if partial else set()
    qualitative = _clean_text(payload.qualitative_value, max_len=20000)
    if not partial or "metric_value" in fields or "qualitative_value" in fields:
        if payload.metric_value is None and not qualitative:
            raise HTTPException(
                status_code=400, detail="metric_value or qualitative_value is required"
            )
    if (
        payload.period_start
        and payload.period_end
        and payload.period_end < payload.period_start
    ):
        raise HTTPException(
            status_code=400, detail="period_end must be after period_start"
        )
    if not partial or "source_type" in fields:
        _coerce_metric_source_type(db, payload.source_type)
    if payload.source_event_id:
        if not db.query(Event.id).filter(Event.id == payload.source_event_id).first():
            raise HTTPException(status_code=400, detail="Unknown source event")


def _apply_metric_entry_payload(
    row: IsmsEffectivenessMetricEntry,
    payload: EffectivenessMetricEntryPayload,
    db: Session,
    *,
    partial: bool = False,
) -> None:
    fields = (
        set(payload.model_fields_set or set())
        if partial
        else {
            "recorded_at",
            "period_start",
            "period_end",
            "metric_value",
            "metric_unit",
            "qualitative_value",
            "source_type",
            "source_title",
            "source_url",
            "source_reference",
            "source_event_id",
            "notes",
            "raw_payload",
        }
    )
    if "recorded_at" in fields:
        row.recorded_at = payload.recorded_at or _utcnow()
    if "period_start" in fields:
        row.period_start = payload.period_start
    if "period_end" in fields:
        row.period_end = payload.period_end
    if "metric_value" in fields:
        row.metric_value = payload.metric_value
    if "metric_unit" in fields:
        row.metric_unit = _clean_text(payload.metric_unit, max_len=64)
    if "qualitative_value" in fields:
        row.qualitative_value = _clean_text(payload.qualitative_value, max_len=20000)
    if "source_type" in fields:
        row.source_type = _coerce_metric_source_type(db, payload.source_type)
    if "source_title" in fields:
        row.source_title = _clean_text(payload.source_title, max_len=256)
    if "source_url" in fields:
        row.source_url = _clean_text(payload.source_url, max_len=2048) or None
    if "source_reference" in fields:
        row.source_reference = _clean_text(payload.source_reference, max_len=256)
    if "source_event_id" in fields:
        row.source_event_id = payload.source_event_id
    if "notes" in fields:
        row.notes = _clean_text(payload.notes, max_len=12000)
    if "raw_payload" in fields:
        row.raw_payload = payload.raw_payload or {}
    row.updated_at = _utcnow()
