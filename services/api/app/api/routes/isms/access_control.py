"""HTTP adapters for ISMS access control."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_manage, require_isms_read
from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.services.context import ChangeContext
from app.services.isms import access_control as service
from app.services.isms.schemas import AccessControlMatrixPayload

router = Router()


@router.post("/v1/isms/access-control-matrix")
def create_access_control_matrix_entry(
    payload: AccessControlMatrixPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_access_control_matrix_entry(
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.get("/v1/isms/access-control-matrix")
def list_access_control_matrix(
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.list_access_control_matrix(framework=framework, user=user, db=db)


@router.get("/v1/isms/access-control-matrix/{entry_id}")
def get_access_control_matrix_entry(
    entry_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.get_access_control_matrix_entry(
        entry_id=entry_id, framework=framework, user=user, db=db
    )


@router.patch("/v1/isms/access-control-matrix/{entry_id}")
def update_access_control_matrix_entry(
    entry_id: str,
    payload: AccessControlMatrixPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.update_access_control_matrix_entry(
        entry_id=entry_id,
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.delete("/v1/isms/access-control-matrix/{entry_id}")
def delete_access_control_matrix_entry(
    entry_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_access_control_matrix_entry(
        entry_id=entry_id,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )
