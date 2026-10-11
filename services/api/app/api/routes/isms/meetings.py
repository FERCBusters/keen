"""HTTP adapters for ISMS meetings."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_manage, require_isms_read
from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.services.context import ChangeContext
from app.services.isms import meetings as service
from app.services.isms.schemas import MeetingPayload

router = Router()


@router.post("/v1/isms/meetings")
def create_meeting(
    payload: MeetingPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_meeting(
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.get("/v1/isms/meetings")
def list_meetings(
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.list_meetings(framework=framework, user=user, db=db)


@router.get("/v1/isms/meetings/{meeting_id}")
def get_meeting(
    meeting_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.get_meeting(
        meeting_id=meeting_id, framework=framework, user=user, db=db
    )


@router.patch("/v1/isms/meetings/{meeting_id}")
def update_meeting(
    meeting_id: str,
    payload: MeetingPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.update_meeting(
        meeting_id=meeting_id,
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.delete("/v1/isms/meetings/{meeting_id}")
def delete_meeting(
    meeting_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_meeting(
        meeting_id=meeting_id,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )
