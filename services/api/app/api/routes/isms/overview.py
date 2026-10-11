"""HTTP adapters for ISMS overview."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_read
from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.services.isms import overview as service

router = Router()


@router.get("/v1/isms/meta")
def isms_meta(
    framework: str = settings.default_framework_slug,
    section: str | None = None,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.isms_meta(framework=framework, section=section, user=user, db=db)


@router.get("/v1/isms/summary")
def isms_summary(
    framework: str = settings.default_framework_slug,
    section: str | None = None,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.isms_summary(framework=framework, section=section, user=user, db=db)
