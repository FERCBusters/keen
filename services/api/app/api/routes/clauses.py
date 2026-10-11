from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.services import clauses as service

router = Router()


@router.get("/v1/clauses")
def list_clauses(
    framework: str = settings.default_framework_slug,
    limit: int = 5000,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    return service.list_clauses(framework=framework, limit=limit, offset=offset, db=db)


@router.get("/v1/clauses/{clause_id}")
def get_clause(clause_id: str, db: Session = Depends(get_db)):
    return service.get_clause(clause_id=clause_id, db=db)


@router.get("/v1/clauses/{clause_id}/changelog")
def clause_changelog(
    clause_id: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    return service.clause_changelog(
        clause_id=clause_id, limit=limit, offset=offset, db=db
    )


@router.get("/v1/clauses/{clause_id}/controls")
def clause_controls(
    clause_id: str,
    limit: int = 5000,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    return service.clause_controls(
        clause_id=clause_id, limit=limit, offset=offset, db=db
    )
