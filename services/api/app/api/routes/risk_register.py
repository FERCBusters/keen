"""A conventional risk register layered on KEEN's existing scenario records."""

from __future__ import annotations

import uuid

from fastapi import Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.routes.risks import require_risk_manage, require_risk_read
from app.api.routing import Router
from app.db.session import get_db
from app.services import risk_register as service
from app.services.risk_register import LibraryInput, RegisterRating, RegisterThresholds

router = Router()


@router.get("/v1/risks/register/heatmap")
def register_heatmap(user=Depends(require_risk_read), db: Session = Depends(get_db)):
    return service.register_heatmap(user=user, db=db)


@router.get("/v1/risks/register")
def list_register(
    framework: str = "iso_27001_2022",
    limit: int = 200,
    offset: int = 0,
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    return service.list_register(
        framework=framework, limit=limit, offset=offset, user=user, db=db
    )


@router.patch("/v1/risks/register/settings")
def update_settings(
    payload: RegisterThresholds,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.update_settings(payload=payload, user=user, db=db)


@router.patch("/v1/risks/{risk_id}/register")
def rate_risk(
    risk_id: uuid.UUID,
    payload: RegisterRating,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.rate_risk(risk_id=risk_id, payload=payload, user=user, db=db)


@router.get("/v1/risks/register/export.csv")
def export_register(
    framework: str = "iso_27001_2022",
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    return StreamingResponse(
        service.export_register(framework=framework, user=user, db=db),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=keen-risk-register.csv"},
    )


@router.post("/v1/risks/register/import.csv")
async def import_register(
    file: UploadFile = File(...),
    framework: str = "iso_27001_2022",
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    raw = await file.read(2_000_001)
    return service.import_register(raw=raw, framework=framework, user=user, db=db)


@router.get("/v1/risks/library")
def list_library(user=Depends(require_risk_read), db: Session = Depends(get_db)):
    return service.list_library(user=user, db=db)


@router.post("/v1/risks/library", status_code=201)
def add_library(
    payload: LibraryInput,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.add_library(payload=payload, user=user, db=db)


@router.delete("/v1/risks/library/{item_id}", status_code=204)
def remove_library(
    item_id: uuid.UUID, user=Depends(require_risk_manage), db: Session = Depends(get_db)
):
    return service.remove_library(item_id=item_id, user=user, db=db)
