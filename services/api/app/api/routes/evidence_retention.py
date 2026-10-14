"""Administrator-only workspace-wide event retention and purge controls."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.routing import Router
from app.db.session import get_db
from app.security.auth import require_admin
from app.services import retention_management as service
from app.services.retention_management import (
    SettingsPayload,
    PreviewPayload,
    PurgePayload,
)

router = Router(
    prefix="/v1/admin/evidence-retention",
    dependencies=[Depends(require_admin)],
)


@router.get("")
def get_status(db: Session = Depends(get_db)):
    return service.get_status(db=db)


@router.post("/preview")
def preview(payload: PreviewPayload, db: Session = Depends(get_db)):
    return service.preview(payload=payload, db=db)


@router.post("/preview-all")
def preview_all(db: Session = Depends(get_db)):
    return service.preview_all(db=db)


@router.put("")
def set_policy(
    payload: SettingsPayload, db: Session = Depends(get_db), user=Depends(require_admin)
):
    return service.set_policy(payload=payload, db=db, user=user)


@router.post("/purge", status_code=202)
def purge(
    payload: PurgePayload, db: Session = Depends(get_db), user=Depends(require_admin)
):
    return service.purge(payload=payload, db=db, user=user)


@router.post("/cancel")
def cancel(db: Session = Depends(get_db)):
    return service.cancel(db=db)


@router.post("/retry-storage")
def retry_storage(db: Session = Depends(get_db)):
    return service.retry_storage(db=db)
