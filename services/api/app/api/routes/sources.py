from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_read
from app.api.routing import Router
from app.core.config import settings
from app.db.models import User
from app.db.session import get_db
from app.security.permissions import has_permission
from app.security.roles import is_effective_admin
from app.services import source_insights as insights
from app.services import sources as service

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


@router.get("/v1/sources")
def list_sources(
    request: Request,
    framework: str = settings.default_framework_slug,
    limit: int = 5000,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    user = _require_events_read(db, request)
    return service.list_sources(
        user=user, framework=framework, limit=limit, offset=offset, db=db
    )


@router.get("/v1/sources/meta")
def list_source_meta(request: Request, db: Session = Depends(get_db)):
    user = _require_events_read(db, request)
    return service.list_source_meta(user=user, db=db)


@router.get("/v1/sources/{source_name}/insights")
def source_insights(
    source_name: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    days: int = insights.DEFAULT_DAYS,
    db: Session = Depends(get_db),
):
    "Describe a source's recent activity relative to its own history."
    user = _require_events_read(db, request)
    return insights.source_insights(
        source_name=source_name, framework=framework, days=days, db=db, user=user
    )


@router.get("/v1/sources/{source_name}/effectiveness-measures")
def source_effectiveness_measures(
    source_name: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    "Return effectiveness measures with metric entries recorded against a Keen source."
    return service.source_effectiveness_measures(
        source_name=source_name, framework=framework, user=user, db=db
    )
