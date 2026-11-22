"""Framework-aware home overview, with permission-scoped attention items."""

from __future__ import annotations

from fastapi import Depends, Request
from myf.api.context import change_context
from myf.security.auth import require_authenticated
from sqlalchemy.orm import Session

from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.services import home as service

router = Router()


@router.get("/v1/home")
def home_overview(
    request: Request,
    framework: str = settings.default_framework_slug,
    db: Session = Depends(get_db),
    user=Depends(require_authenticated),
):
    return service.home_overview(
        context=change_context(request), framework=framework, db=db, user=user
    )
