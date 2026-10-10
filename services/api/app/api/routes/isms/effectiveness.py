"""ISMS effectiveness; see docs/maintainability-review.md for module boundaries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import IsmsEffectivenessMeasure, IsmsEffectivenessMetricEntry
from app.db.session import get_db
from app.services.entity_changelog import record_entity_changelog

from .access import (
    require_isms_manage,
    require_isms_read,
)
from .common import (
    _by_id_or_404,
    _clean_framework,
    _record,
    _utcnow,
)
from .metric_values import (
    _apply_effectiveness_measure_payload,
    _apply_metric_entry_payload,
    _validate_effectiveness_measure_payload,
    _validate_metric_entry_payload,
)
from .mutations import (
    _apply_effectiveness_links,
    _delete_entity_links,
)
from .schemas import (
    EffectivenessMeasurePayload,
    EffectivenessMetricEntryPayload,
)
from .serializers import (
    _effectiveness_measure_out,
    _metric_entry_out,
)

router = APIRouter()


@router.post("/v1/isms/effectiveness-measures")
def create_effectiveness_measure(
    payload: EffectivenessMeasurePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    _validate_effectiveness_measure_payload(db, payload)
    row = IsmsEffectivenessMeasure(
        framework_slug=fw,
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_effectiveness_measure_payload(row, payload)
    db.add(row)
    db.flush()
    _apply_effectiveness_links(db, row.id, fw, payload, user)
    db.flush()
    after = _effectiveness_measure_out(db, row, fw)
    _record(db, "effectiveness_measure", row, "created", None, after, user, request)
    db.commit()
    return _effectiveness_measure_out(db, row, fw)


@router.get("/v1/isms/effectiveness-measures")
def list_effectiveness_measures(
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
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


@router.get("/v1/isms/effectiveness-measures/{measure_id}")
def get_effectiveness_measure(
    measure_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if row.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness measure not found")
    return _effectiveness_measure_out(db, row, fw)


@router.patch("/v1/isms/effectiveness-measures/{measure_id}")
def update_effectiveness_measure(
    measure_id: str,
    payload: EffectivenessMeasurePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if row.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness measure not found")
    before = _effectiveness_measure_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    _validate_effectiveness_measure_payload(db, payload, fields=fields, row_id=row.id)
    _apply_effectiveness_measure_payload(row, payload, partial=True)
    _apply_effectiveness_links(db, row.id, fw, payload, user)
    db.add(row)
    db.flush()
    after = _effectiveness_measure_out(db, row, fw)
    _record(db, "effectiveness_measure", row, "updated", before, after, user, request)
    db.commit()
    return _effectiveness_measure_out(db, row, fw)


@router.delete("/v1/isms/effectiveness-measures/{measure_id}")
def delete_effectiveness_measure(
    measure_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if row.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness measure not found")
    before = _effectiveness_measure_out(db, row, fw)
    _record(db, "effectiveness_measure", row, "deleted", before, None, user, request)
    _delete_entity_links(db, "effectiveness_measure", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/v1/isms/effectiveness-measures/{measure_id}/metrics")
def create_effectiveness_metric_entry(
    measure_id: str,
    payload: EffectivenessMetricEntryPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    measure = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if measure.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness measure not found")
    _validate_metric_entry_payload(db, payload)
    row = IsmsEffectivenessMetricEntry(
        measure_id=measure.id,
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_metric_entry_payload(row, payload, db)
    if not row.metric_unit:
        row.metric_unit = measure.target_unit or ""
    db.add(row)
    measure.updated_at = _utcnow()
    db.add(measure)
    db.flush()
    after = _metric_entry_out(row)
    record_entity_changelog(
        db,
        entity_type="isms_effectiveness_metric",
        entity_id=row.id,
        action="created",
        before=None,
        after=after,
        user=user,
        request_method=request.method,
        request_path=request.url.path,
    )
    db.commit()
    return _metric_entry_out(row)


@router.get("/v1/isms/effectiveness-measures/{measure_id}/metrics")
def list_effectiveness_metric_entries(
    measure_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    measure = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if measure.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness measure not found")
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


@router.get("/v1/isms/effectiveness-metrics/{entry_id}")
def get_effectiveness_metric_entry(
    entry_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMetricEntry, entry_id, "Effectiveness metric"
    )
    if not row.measure or row.measure.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness metric not found")
    return _metric_entry_detail_out(db, row, fw)


@router.patch("/v1/isms/effectiveness-metrics/{entry_id}")
def update_effectiveness_metric_entry(
    entry_id: str,
    payload: EffectivenessMetricEntryPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMetricEntry, entry_id, "Effectiveness metric"
    )
    if not row.measure or row.measure.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness metric not found")
    before = _metric_entry_out(row)
    _validate_metric_entry_payload(db, payload, partial=True)
    _apply_metric_entry_payload(row, payload, db, partial=True)
    row.measure.updated_at = _utcnow()
    db.add(row)
    db.add(row.measure)
    db.flush()
    after = _metric_entry_out(row)
    record_entity_changelog(
        db,
        entity_type="isms_effectiveness_metric",
        entity_id=row.id,
        action="updated",
        before=before,
        after=after,
        user=user,
        request_method=request.method,
        request_path=request.url.path,
    )
    db.commit()
    return _metric_entry_out(row)


@router.delete("/v1/isms/effectiveness-metrics/{entry_id}")
def delete_effectiveness_metric_entry(
    entry_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMetricEntry, entry_id, "Effectiveness metric"
    )
    if not row.measure or row.measure.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness metric not found")
    before = _metric_entry_out(row)
    measure = row.measure
    record_entity_changelog(
        db,
        entity_type="isms_effectiveness_metric",
        entity_id=row.id,
        action="deleted",
        before=before,
        after=None,
        user=user,
        request_method=request.method,
        request_path=request.url.path,
    )
    measure.updated_at = _utcnow()
    db.add(measure)
    db.delete(row)
    db.commit()
    return {"ok": True}
