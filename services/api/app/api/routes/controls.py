from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.routing import Router
from app.core.config import settings
from app.db.models import User
from app.db.session import get_db
from app.security.permissions import has_permission
from app.security.roles import is_effective_admin
from app.services import controls as service

router = Router()

EVENTS_READ_PERMISSION = "events.read"


def _require_events_read(db: Session, request: Request) -> User:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not is_effective_admin(db, user) and not has_permission(
        db, user, EVENTS_READ_PERMISSION
    ):
        raise HTTPException(status_code=403, detail="events.read permission required")
    return user


@router.get("/v1/controls")
def list_controls(
    framework: str = settings.default_framework_slug,
    limit: int = 500,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    return service.list_controls(framework=framework, limit=limit, offset=offset, db=db)


@router.get("/v1/controls/{control_id}")
def get_control(control_id: str, db: Session = Depends(get_db)):
    return service.get_control(control_id=control_id, db=db)


@router.get("/v1/controls/{control_id}/changelog")
def control_changelog(
    control_id: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    return service.control_changelog(
        control_id=control_id, limit=limit, offset=offset, db=db
    )


@router.get("/v1/controls/{control_id}/evidence")
def control_evidence(
    request: Request,
    control_id: str,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    user = _require_events_read(db, request)
    return service.control_evidence(
        user=user, control_id=control_id, limit=limit, offset=offset, db=db
    )


@router.get("/v1/controls/{control_id}/evidence/summary")
def control_evidence_summary(
    request: Request, control_id: str, db: Session = Depends(get_db)
):
    user = _require_events_read(db, request)
    return service.control_evidence_summary(control_id=control_id, db=db, user=user)
