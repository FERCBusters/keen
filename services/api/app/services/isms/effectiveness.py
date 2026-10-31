"""ISMS effectiveness domain operations."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive as _utcnow
from app.db.models import (
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    Quiz,
    User,
)
from app.services.context import ChangeContext
from app.services.entity_changelog import record_entity_changelog
from app.services.errors import DomainError
from app.services.frameworks import framework_parameter

from .common import _by_id_or_404, _record
from .metric_values import (
    _apply_effectiveness_measure_payload,
    _apply_metric_entry_payload,
    _validate_effectiveness_measure_payload,
    _validate_metric_entry_payload,
    inherited_metric_source_type,
)
from .mutations import _apply_effectiveness_links, _delete_entity_links
from .schemas import EffectivenessMeasurePayload, EffectivenessMetricEntryPayload
from .serializers import _effectiveness_measure_out, _metric_entry_out


def measure_quiz(db: Session, measure_id) -> Quiz | None:
    """The quiz that owns a measure and records its results, if any."""
    return db.query(Quiz).filter(Quiz.measure_id == measure_id).one_or_none()


def _refuse_quiz_measure(db: Session, measure: IsmsEffectivenessMeasure) -> None:
    # A quiz keeps its measure's settings, links and results in step with the
    # quiz itself, so they change only through the quiz.
    if measure_quiz(db, measure.id) is not None:
        raise DomainError(
            status_code=409,
            detail="This measure belongs to a quiz; change it from the quiz",
        )


def add_effectiveness_measure(
    *,
    payload: EffectivenessMeasurePayload,
    context: ChangeContext | None,
    framework: str,
    user: User | None,
    db: Session,
) -> IsmsEffectivenessMeasure:
    """Create a measure and its control links in the caller's transaction."""
    fw = framework_parameter(framework)
    _validate_effectiveness_measure_payload(db, payload)
    row = IsmsEffectivenessMeasure(
        framework_slug=fw,
        created_by_user_id=user.id if user else None,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_effectiveness_measure_payload(db, row, payload)
    db.add(row)
    db.flush()
    _apply_effectiveness_links(db, row.id, fw, payload, user)
    db.flush()
    after = _effectiveness_measure_out(db, row, fw)
    _record(db, "effectiveness_measure", row, "created", None, after, user, context)
    return row


def revise_effectiveness_measure(
    *,
    row: IsmsEffectivenessMeasure,
    payload: EffectivenessMeasurePayload,
    context: ChangeContext | None,
    user: User | None,
    db: Session,
) -> None:
    """Apply the payload's fields to a measure in the caller's transaction."""
    fw = row.framework_slug
    before = _effectiveness_measure_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    _validate_effectiveness_measure_payload(db, payload, fields=fields, row_id=row.id)
    _apply_effectiveness_measure_payload(db, row, payload, partial=True)
    _apply_effectiveness_links(db, row.id, fw, payload, user)
    db.add(row)
    db.flush()
    after = _effectiveness_measure_out(db, row, fw)
    _record(db, "effectiveness_measure", row, "updated", before, after, user, context)


def remove_effectiveness_measure(
    *,
    row: IsmsEffectivenessMeasure,
    context: ChangeContext | None,
    user: User | None,
    db: Session,
) -> None:
    """Delete a measure, its links and its results in the caller's transaction."""
    fw = row.framework_slug
    before = _effectiveness_measure_out(db, row, fw)
    _record(db, "effectiveness_measure", row, "deleted", before, None, user, context)
    _delete_entity_links(db, "effectiveness_measure", row.id, fw)
    db.delete(row)


def create_effectiveness_measure(
    *,
    payload: EffectivenessMeasurePayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    row = add_effectiveness_measure(
        payload=payload, context=context, framework=framework, user=user, db=db
    )
    db.commit()
    return _effectiveness_measure_out(db, row, row.framework_slug)


def list_effectiveness_measures(*, framework: str, user: User, db: Session):
    fw = framework_parameter(framework)
    rows = (
        db.query(IsmsEffectivenessMeasure)
        .filter(IsmsEffectivenessMeasure.framework_slug == fw)
        .order_by(IsmsEffectivenessMeasure.updated_at.desc())
        .all()
    )
    return {
        "framework": fw,
        "items": [_effectiveness_measure_out(db, row, fw) for row in rows],
    }


def get_effectiveness_measure(
    *, measure_id: str, framework: str, user: User, db: Session
):
    fw = framework_parameter(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if row.framework_slug != fw:
        raise DomainError(status_code=404, detail="Effectiveness measure not found")
    return _effectiveness_measure_out(db, row, fw)


def update_effectiveness_measure(
    *,
    measure_id: str,
    payload: EffectivenessMeasurePayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = framework_parameter(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if row.framework_slug != fw:
        raise DomainError(status_code=404, detail="Effectiveness measure not found")
    _refuse_quiz_measure(db, row)
    revise_effectiveness_measure(
        row=row, payload=payload, context=context, user=user, db=db
    )
    db.commit()
    return _effectiveness_measure_out(db, row, fw)


def delete_effectiveness_measure(
    *, measure_id: str, context: ChangeContext, framework: str, user: User, db: Session
):
    fw = framework_parameter(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if row.framework_slug != fw:
        raise DomainError(status_code=404, detail="Effectiveness measure not found")
    _refuse_quiz_measure(db, row)
    remove_effectiveness_measure(row=row, context=context, user=user, db=db)
    db.commit()
    return {"ok": True}


def _record_metric_change(
    db: Session,
    row: IsmsEffectivenessMetricEntry,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    user: User | None,
    context: ChangeContext | None,
) -> None:
    record_entity_changelog(
        db,
        entity_type="isms_effectiveness_metric",
        entity_id=row.id,
        action=action,
        before=before,
        after=after,
        user=user,
        request_method=context.method if context else None,
        request_path=context.path if context else None,
    )


def add_effectiveness_result(
    *,
    measure: IsmsEffectivenessMeasure,
    payload: EffectivenessMetricEntryPayload,
    context: ChangeContext | None,
    user: User | None,
    db: Session,
) -> IsmsEffectivenessMetricEntry:
    """Record a result on a measure in the caller's transaction."""
    _validate_metric_entry_payload(db, payload)
    row = IsmsEffectivenessMetricEntry(
        measure_id=measure.id,
        created_by_user_id=user.id if user else None,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_metric_entry_payload(row, payload, db)
    if not row.metric_unit:
        row.metric_unit = measure.target_unit or ""
    if "source_type" not in set(payload.model_fields_set or set()):
        row.source_type = inherited_metric_source_type(db, measure)
    db.add(row)
    measure.updated_at = _utcnow()
    db.add(measure)
    db.flush()
    _record_metric_change(
        db, row, "created", None, _metric_entry_out(row), user, context
    )
    return row


def revise_effectiveness_result(
    *,
    row: IsmsEffectivenessMetricEntry,
    payload: EffectivenessMetricEntryPayload,
    context: ChangeContext | None,
    user: User | None,
    db: Session,
) -> None:
    """Apply the payload's fields to a recorded result in the caller's transaction."""
    before = _metric_entry_out(row)
    _validate_metric_entry_payload(db, payload, partial=True)
    _apply_metric_entry_payload(row, payload, db, partial=True)
    row.measure.updated_at = _utcnow()
    db.add(row)
    db.add(row.measure)
    db.flush()
    _record_metric_change(
        db, row, "updated", before, _metric_entry_out(row), user, context
    )


def remove_effectiveness_result(
    *,
    row: IsmsEffectivenessMetricEntry,
    context: ChangeContext | None,
    user: User | None,
    db: Session,
) -> None:
    """Delete a recorded result in the caller's transaction."""
    _record_metric_change(
        db, row, "deleted", _metric_entry_out(row), None, user, context
    )
    measure = row.measure
    measure.updated_at = _utcnow()
    db.add(measure)
    db.delete(row)


def create_effectiveness_metric_entry(
    *,
    measure_id: str,
    payload: EffectivenessMetricEntryPayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = framework_parameter(framework)
    measure = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if measure.framework_slug != fw:
        raise DomainError(status_code=404, detail="Effectiveness measure not found")
    _refuse_quiz_measure(db, measure)
    row = add_effectiveness_result(
        measure=measure, payload=payload, context=context, user=user, db=db
    )
    db.commit()
    return _metric_entry_out(row)


def list_effectiveness_metric_entries(
    *, measure_id: str, framework: str, user: User, db: Session
):
    fw = framework_parameter(framework)
    measure = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if measure.framework_slug != fw:
        raise DomainError(status_code=404, detail="Effectiveness measure not found")
    rows = (
        db.query(IsmsEffectivenessMetricEntry)
        .filter(IsmsEffectivenessMetricEntry.measure_id == measure.id)
        .order_by(
            IsmsEffectivenessMetricEntry.recorded_at.desc(),
            IsmsEffectivenessMetricEntry.created_at.desc(),
        )
        .all()
    )
    return {"items": [_metric_entry_out(row) for row in rows]}


def _metric_entry_detail_out(
    db: Session, row: IsmsEffectivenessMetricEntry, framework: str
) -> dict[str, Any]:
    out = _metric_entry_out(row) or {}
    measure = row.measure
    if measure and measure.framework_slug == framework:
        out["framework"] = measure.framework_slug
        out["measure"] = _effectiveness_measure_out(
            db, measure, framework, include_entries=False
        )
    else:
        out["framework"] = framework
        out["measure"] = None
    return out


def get_effectiveness_metric_entry(
    *, entry_id: str, framework: str, user: User, db: Session
):
    fw = framework_parameter(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMetricEntry, entry_id, "Effectiveness metric"
    )
    if not row.measure or row.measure.framework_slug != fw:
        raise DomainError(status_code=404, detail="Effectiveness metric not found")
    return _metric_entry_detail_out(db, row, fw)


def update_effectiveness_metric_entry(
    *,
    entry_id: str,
    payload: EffectivenessMetricEntryPayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = framework_parameter(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMetricEntry, entry_id, "Effectiveness metric"
    )
    if not row.measure or row.measure.framework_slug != fw:
        raise DomainError(status_code=404, detail="Effectiveness metric not found")
    _refuse_quiz_measure(db, row.measure)
    revise_effectiveness_result(
        row=row, payload=payload, context=context, user=user, db=db
    )
    db.commit()
    return _metric_entry_out(row)


def delete_effectiveness_metric_entry(
    *, entry_id: str, context: ChangeContext, framework: str, user: User, db: Session
):
    fw = framework_parameter(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMetricEntry, entry_id, "Effectiveness metric"
    )
    if not row.measure or row.measure.framework_slug != fw:
        raise DomainError(status_code=404, detail="Effectiveness metric not found")
    _refuse_quiz_measure(db, row.measure)
    remove_effectiveness_result(row=row, context=context, user=user, db=db)
    db.commit()
    return {"ok": True}
