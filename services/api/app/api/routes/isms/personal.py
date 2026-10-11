"""HTTP adapters for ISMS personal."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.services.isms import personal as service

router = Router()


@router.get("/v1/me/isms-objectives")
def my_isms_objectives(
    framework: str = settings.default_framework_slug,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    return service.my_isms_objectives(framework=framework, user=user, db=db)


@router.get("/v1/me/isms-role")
def my_isms_role(user=Depends(require_authenticated), db: Session = Depends(get_db)):
    return service.my_isms_role(user=user, db=db)


@router.get("/v1/me/isms-meetings")
def my_isms_meetings(
    framework: str = settings.default_framework_slug,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    return service.my_isms_meetings(framework=framework, user=user, db=db)


@router.get("/v1/me/isms-assets")
def my_isms_assets(
    framework: str = settings.default_framework_slug,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    return service.my_isms_assets(framework=framework, user=user, db=db)
