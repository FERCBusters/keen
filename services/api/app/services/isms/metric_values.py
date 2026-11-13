"""ISMS metric values domain operations."""

from __future__ import annotations

import math
import operator
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive as _utcnow
from app.core.source_meta import apply_user_source_overrides, get_source_meta
from app.db.models import (
    Event,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    User,
)
from app.security.permissions import can_read_events
from app.security.regex import scan_pattern
from app.services.errors import DomainError
from app.services.text import clean_text

from .constants import (
    EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE,
    EFFECTIVENESS_THRESHOLD_OPERATORS,
)
from .schemas import EffectivenessMeasurePayload, EffectivenessMetricEntryPayload


def _metric_key_from_payload(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if len(value) > 128 or not scan_pattern(r"^[A-Za-z0-9._:-]+$").match(value):
        raise DomainError(
            status_code=400,
            detail="metric_key may only contain letters, numbers, dot, underscore, colon and hyphen",
        )
    return value


def _validate_effectiveness_measure_payload(
    db: Session,
    payload: EffectivenessMeasurePayload,
    user: User | None,
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
            raise DomainError(status_code=400, detail="Unknown owner")
    if fields is None or "threshold_operator" in fields:
        op = (payload.threshold_operator or "").strip()
        if op not in EFFECTIVENESS_THRESHOLD_OPERATORS:
            raise DomainError(status_code=400, detail="Invalid threshold operator")
    if fields is None or "metric_key" in fields:
        key = _metric_key_from_payload(payload.metric_key)
        if key:
            qry = db.query(IsmsEffectivenessMeasure.id).filter(
                func.lower(IsmsEffectivenessMeasure.metric_key) == key.lower()
            )
            if row_id:
                qry = qry.filter(IsmsEffectivenessMeasure.id != row_id)
            if qry.first():
                raise DomainError(status_code=400, detail="metric_key already exists")
    if fields is None or "source_type" in fields:
        _coerce_metric_source_type(db, payload.source_type, user)
    if (fields is None or "clauses" in fields) and payload.clauses:
        raise DomainError(
            status_code=400,
            detail="Effectiveness measures link controls only; clauses are inferred from those controls",
        )


def _apply_effectiveness_measure_payload(
    db: Session,
    row: IsmsEffectivenessMeasure,
    payload: EffectivenessMeasurePayload,
    user: User | None,
    *,
    partial: bool = False,
) -> None:
    fields = (
        set(payload.model_fields_set or set())
        if partial
        else {
            "summary",
            "description",
            "metric_key",
            "source_type",
            "target_value",
            "target_unit",
            "threshold_operator",
            "owner_user_id",
            "frequency",
            "notes",
        }
    )
    if "summary" in fields:
        row.summary = clean_text(
            payload.summary, max_len=20000, required=not partial, label="summary"
        )
    if "description" in fields:
        row.description = clean_text(payload.description, max_len=40000)
    if "metric_key" in fields:
        row.metric_key = _metric_key_from_payload(payload.metric_key)
    if "source_type" in fields:
        row.source_type = _coerce_metric_source_type(db, payload.source_type, user)
    if "target_value" in fields:
        row.target_value = payload.target_value
    if "target_unit" in fields:
        row.target_unit = clean_text(payload.target_unit, max_len=64)
    if "threshold_operator" in fields:
        row.threshold_operator = (payload.threshold_operator or "").strip()
    if "owner_user_id" in fields:
        row.owner_user_id = payload.owner_user_id
    if "frequency" in fields:
        row.frequency = clean_text(payload.frequency, max_len=64)
    if "notes" in fields:
        row.notes = clean_text(payload.notes, max_len=12000)
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


def _listed_metric_source_names(db: Session, user: User | None) -> list[str]:
    """The evidence sources a person's measure form offers.

    Collected source names derive from events, so only event readers are
    offered them all; other ISMS readers are offered the sources measures
    already name. A source named in a save is checked against the same names,
    so saving cannot tell such a person whether a name they guess is collected.
    """
    if can_read_events(db, user):
        return _actual_effectiveness_metric_source_names(db)
    return sorted(
        {
            source
            for (source,) in db.query(IsmsEffectivenessMeasure.source_type)
            .distinct()
            .all()
            if source and source != EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE
        },
        key=str.lower,
    )


def _effectiveness_metric_sources_out(
    db: Session, user: User | None = None
) -> list[dict[str, str]]:
    overrides = (
        getattr(user, "pref_source_colors", None) if isinstance(user, User) else None
    )
    names = _listed_metric_source_names(db, user)
    items: list[dict[str, str]] = []
    for source in names:
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


def resolve_metric_source_type(raw: str | None, *, among: list[str]) -> str | None:
    """Return the source named by ``raw`` among those offered, "other", or None.

    Blank input means "other". Spelling variants (case, hyphen/underscore) name
    the same source. ``among`` holds the sources the saver's form offers; None
    means none of them has that name.
    """

    source_type = clean_text(raw, max_len=64).strip()
    if not source_type:
        return EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE
    if source_type.lower() == EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE:
        return EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE
    for source in among:
        if source_type == source:
            return source
    source_key = source_type.lower().replace("-", "_")
    for source in among:
        if source.lower().replace("-", "_") == source_key:
            return source
    return None


def _coerce_metric_source_type(db: Session, raw: str | None, user: User | None) -> str:
    """The source a person's save names, from those their form offers."""
    resolved = resolve_metric_source_type(
        raw, among=_listed_metric_source_names(db, user)
    )
    if resolved is None:
        raise DomainError(
            status_code=400,
            detail="Invalid metric source type; choose an existing Keen source or other",
        )
    return resolved


def inherited_metric_source_type(
    db: Session, measure: IsmsEffectivenessMeasure, user: User | None
) -> str:
    """The source a new entry starts with: the measure's expected source.

    For an event reader that is the expected source while it still collects
    evidence, otherwise "other". For anyone else, a signed submission
    included, it is the source as the measure stores it: whether events still
    carry it is not theirs to learn from the entry they save.
    """

    if not can_read_events(db, user):
        return measure.source_type
    return (
        resolve_metric_source_type(
            measure.source_type, among=_listed_metric_source_names(db, user)
        )
        or EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE
    )


def submitted_metric_source_type(db: Session, raw: str | None) -> str | None:
    """The source a signed submission names, or None when it may not name it.

    A submission secret carries no permission to read events, so its holder is
    offered what a measure form offers someone who cannot read them: the
    sources measures already name, and "other". A collected source outside
    that list is answered exactly as a source that does not exist.
    """

    return resolve_metric_source_type(raw, among=_listed_metric_source_names(db, None))


_THRESHOLD_OPERATIONS = {
    "lt": operator.lt,
    "lte": operator.le,
    "eq": operator.eq,
    "gte": operator.ge,
    "gt": operator.gt,
}


def threshold_status(
    value: float | None,
    target: float | None,
    comparison: str | None,
    unit: str | None = "",
    target_unit: str | None = "",
) -> str | None:
    """Compare a recorded value with a measure's target.

    Returns "met" or "outside" when the measure defines a numeric target with
    an operator and the value is numeric in the same unit, otherwise None: a
    qualitative entry, a measure without a target or a value recorded in a
    different unit cannot be judged against the threshold.
    """

    operation = _THRESHOLD_OPERATIONS.get((comparison or "").strip())
    if (
        operation is None
        or value is None
        or target is None
        or (unit or "").strip() != (target_unit or "").strip()
    ):
        return None
    if not math.isfinite(value) or not math.isfinite(target):
        return None
    return "met" if operation(value, target) else "outside"


def _validate_metric_entry_payload(
    db: Session,
    payload: EffectivenessMetricEntryPayload,
    user: User | None,
    *,
    partial: bool = False,
) -> None:
    fields = set(payload.model_fields_set or set()) if partial else set()
    qualitative = clean_text(payload.qualitative_value, max_len=20000)
    if not partial or "metric_value" in fields or "qualitative_value" in fields:
        if payload.metric_value is None and not qualitative:
            raise DomainError(
                status_code=400, detail="metric_value or qualitative_value is required"
            )
    if (
        payload.period_start
        and payload.period_end
        and payload.period_end < payload.period_start
    ):
        raise DomainError(
            status_code=400, detail="period_end must be after period_start"
        )
    if not partial or "source_type" in fields:
        _coerce_metric_source_type(db, payload.source_type, user)
    if payload.source_event_id:
        if not db.query(Event.id).filter(Event.id == payload.source_event_id).first():
            raise DomainError(status_code=400, detail="Unknown source event")


def _apply_metric_entry_payload(
    row: IsmsEffectivenessMetricEntry,
    payload: EffectivenessMetricEntryPayload,
    db: Session,
    user: User | None,
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
        row.metric_unit = clean_text(payload.metric_unit, max_len=64)
    if "qualitative_value" in fields:
        row.qualitative_value = clean_text(payload.qualitative_value, max_len=20000)
    if "source_type" in fields:
        row.source_type = _coerce_metric_source_type(db, payload.source_type, user)
    if "source_title" in fields:
        row.source_title = clean_text(payload.source_title, max_len=256)
    if "source_url" in fields:
        row.source_url = clean_text(payload.source_url, max_len=2048) or None
    if "source_reference" in fields:
        row.source_reference = clean_text(payload.source_reference, max_len=256)
    if "source_event_id" in fields:
        row.source_event_id = payload.source_event_id
    if "notes" in fields:
        row.notes = clean_text(payload.notes, max_len=12000)
    if "raw_payload" in fields:
        row.raw_payload = payload.raw_payload or {}
    row.updated_at = _utcnow()
