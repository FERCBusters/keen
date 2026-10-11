"""HTTP adapters for ISMS relationships."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_read
from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.services.isms import relationships as service

router = Router()


@router.get("/v1/isms/{entity_type}/{entity_id}/changelog")
def isms_entity_changelog(
    entity_type: str,
    entity_id: str,
    limit: int = 50,
    offset: int = 0,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.isms_entity_changelog(
        entity_type=entity_type,
        entity_id=entity_id,
        limit=limit,
        offset=offset,
        user=user,
        db=db,
    )


@router.get("/v1/controls/{control_id}/effectiveness-measures")
def control_effectiveness_measures(
    control_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.control_effectiveness_measures(
        control_id=control_id, framework=framework, user=user, db=db
    )


@router.get("/v1/controls/{control_id}/isms")
def control_isms(
    control_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.control_isms(
        control_id=control_id, framework=framework, user=user, db=db
    )


@router.get("/v1/clauses/{clause_id}/isms")
def clause_isms(
    clause_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.clause_isms(
        clause_id=clause_id, framework=framework, user=user, db=db
    )
