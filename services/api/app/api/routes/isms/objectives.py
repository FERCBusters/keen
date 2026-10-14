"""HTTP adapters for ISMS objectives."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_manage, require_isms_read
from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.services.context import ChangeContext
from app.services.isms import objectives as service
from app.services.isms.schemas import ObjectivePayload

router = Router()


@router.post("/v1/isms/objectives")
def create_objective(
    payload: ObjectivePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_objective(
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.get("/v1/isms/objectives")
def list_objectives(
    q: str = "",
    status: str = "",
    framework: str = settings.default_framework_slug,
    limit: int = 200,
    offset: int = 0,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.list_objectives(
        q=q,
        status=status,
        framework=framework,
        limit=limit,
        offset=offset,
        user=user,
        db=db,
    )


@router.get("/v1/isms/objectives/{objective_id}")
def get_objective(
    objective_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.get_objective(
        objective_id=objective_id, framework=framework, user=user, db=db
    )


@router.patch("/v1/isms/objectives/{objective_id}")
def update_objective(
    objective_id: str,
    payload: ObjectivePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.update_objective(
        objective_id=objective_id,
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.delete("/v1/isms/objectives/{objective_id}")
def delete_objective(
    objective_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_objective(
        objective_id=objective_id,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )
