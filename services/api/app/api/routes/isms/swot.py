"""SWOT HTTP adapters; all routes use the application's protected middleware."""

from typing import Literal
from uuid import UUID

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.dependencies.isms import require_isms_manage, require_isms_read
from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.services.isms import swot as service

router = Router()


@router.get("/v1/isms/swot")
def list_swot(
    q: str = Query("", max_length=256),
    kind: str = "",
    status: str = "",
    significance: int | None = Query(None, ge=1, le=5),
    parent_id: UUID | None = None,
    target_type: str = "",
    target_id: UUID | None = None,
    control: str = Query("", max_length=128),
    clause: str = Query("", max_length=128),
    framework: str = settings.default_framework_slug,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.list_items(
        user=user,
        db=db,
        q=q,
        kind=kind,
        status=status,
        significance=significance,
        parent_id=parent_id,
        target_type=target_type,
        target_id=target_id,
        limit=limit,
        offset=offset,
        control=control,
        clause=clause,
        framework=framework,
    )


@router.get("/v1/isms/swot-choices/{target_type}")
def choices(
    target_type: str,
    q: str = Query("", max_length=256),
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.choices(target_type=target_type, q=q, user=user, db=db)


@router.post("/v1/isms/swot")
def create_swot(
    payload: service.SwotPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.save_item(
        payload=payload,
        framework=framework,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.get("/v1/isms/swot/{item_id}")
def get_swot(
    item_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.get_item(item_id=item_id, framework=framework, user=user, db=db)


@router.patch("/v1/isms/swot/{item_id}")
def update_swot(
    item_id: str,
    payload: service.SwotPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.save_item(
        item_id=item_id,
        payload=payload,
        framework=framework,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.delete("/v1/isms/swot/{item_id}")
def delete_swot(
    item_id: str,
    request: Request,
    expected_version: int = Query(..., ge=1),
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_item(
        item_id=item_id,
        expected_version=expected_version,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.post("/v1/isms/swot/{item_id}/links")
def add_link(
    item_id: str,
    payload: service.LinkPayload,
    request: Request,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.change_link(
        item_id=item_id,
        payload=payload,
        remove=False,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.delete("/v1/isms/swot/{item_id}/links/{target_type}/{target_id}")
def remove_link(
    item_id: str,
    target_type: Literal["objective", "document", "asset", "person", "vendor", "risk"],
    target_id: UUID,
    request: Request,
    expected_version: int = Query(..., ge=1),
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.change_link(
        item_id=item_id,
        payload=service.LinkPayload(
            target_type=target_type,
            target_id=target_id,
            expected_version=expected_version,
        ),
        remove=True,
        user=user,
        db=db,
        context=change_context(request),
    )
