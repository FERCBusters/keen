"""HTTP adapters for ISMS effectiveness."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_manage, require_isms_read
from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.services.context import ChangeContext
from app.services.isms import effectiveness as service
from app.services.isms.schemas import (
    EffectivenessMeasurePayload,
    EffectivenessMetricEntryPayload,
)

router = Router()


@router.post("/v1/isms/effectiveness-measures")
def create_effectiveness_measure(
    payload: EffectivenessMeasurePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_effectiveness_measure(
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.get("/v1/isms/effectiveness-measures")
def list_effectiveness_measures(
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.list_effectiveness_measures(framework=framework, user=user, db=db)


@router.get("/v1/isms/effectiveness-measures/{measure_id}")
def get_effectiveness_measure(
    measure_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.get_effectiveness_measure(
        measure_id=measure_id, framework=framework, user=user, db=db
    )


@router.patch("/v1/isms/effectiveness-measures/{measure_id}")
def update_effectiveness_measure(
    measure_id: str,
    payload: EffectivenessMeasurePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.update_effectiveness_measure(
        measure_id=measure_id,
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.delete("/v1/isms/effectiveness-measures/{measure_id}")
def delete_effectiveness_measure(
    measure_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_effectiveness_measure(
        measure_id=measure_id,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.post("/v1/isms/effectiveness-measures/{measure_id}/metrics")
def create_effectiveness_metric_entry(
    measure_id: str,
    payload: EffectivenessMetricEntryPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_effectiveness_metric_entry(
        measure_id=measure_id,
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.get("/v1/isms/effectiveness-measures/{measure_id}/metrics")
def list_effectiveness_metric_entries(
    measure_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.list_effectiveness_metric_entries(
        measure_id=measure_id, framework=framework, user=user, db=db
    )


@router.get("/v1/isms/effectiveness-metrics/{entry_id}")
def get_effectiveness_metric_entry(
    entry_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.get_effectiveness_metric_entry(
        entry_id=entry_id, framework=framework, user=user, db=db
    )


@router.patch("/v1/isms/effectiveness-metrics/{entry_id}")
def update_effectiveness_metric_entry(
    entry_id: str,
    payload: EffectivenessMetricEntryPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.update_effectiveness_metric_entry(
        entry_id=entry_id,
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.delete("/v1/isms/effectiveness-metrics/{entry_id}")
def delete_effectiveness_metric_entry(
    entry_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_effectiveness_metric_entry(
        entry_id=entry_id,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )
