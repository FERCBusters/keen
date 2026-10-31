from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.routing import Router
from app.core.config import settings
from app.db.models import User
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.services import pestle as service
from app.services.pestle import (
    BusinessProcessLinksPayload,
    BusinessProcessPayload,
    ClauseLinksPayload,
    PestleItemPayload,
    can_manage_pestle,
    can_read_pestle,
)

router = Router()


def require_pestle_read(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not can_read_pestle(db, user):
        raise HTTPException(status_code=403, detail="pestle.read permission required")
    return user


def require_pestle_manage(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not can_manage_pestle(db, user):
        raise HTTPException(status_code=403, detail="pestle.manage permission required")
    return user


@router.get("/v1/pestle/meta")
def pestle_meta(user=Depends(require_pestle_read), db: Session = Depends(get_db)):
    return service.pestle_meta(user=user, db=db)


@router.get("/v1/pestle/items")
def list_pestle_items(
    framework: str = settings.default_framework_slug,
    q: str = "",
    type: str = "",
    lens: str = "",
    relevance_code: str = "",
    limit: int = 500,
    offset: int = 0,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    return service.list_pestle_items(
        framework=framework,
        q=q,
        type=type,
        lens=lens,
        relevance_code=relevance_code,
        limit=limit,
        offset=offset,
        user=user,
        db=db,
    )


@router.post("/v1/pestle/items")
def create_pestle_item(
    payload: PestleItemPayload,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    return service.create_pestle_item(payload=payload, user=user, db=db)


@router.get("/v1/pestle/items/{item_id}")
def get_pestle_item(
    item_id: str,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    return service.get_pestle_item(item_id=item_id, user=user, db=db)


@router.patch("/v1/pestle/items/{item_id}")
def update_pestle_item(
    item_id: str,
    payload: PestleItemPayload,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    return service.update_pestle_item(
        item_id=item_id, payload=payload, user=user, db=db
    )


@router.delete("/v1/pestle/items/{item_id}")
def delete_pestle_item(
    item_id: str,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    return service.delete_pestle_item(item_id=item_id, user=user, db=db)


@router.get("/v1/pestle/business-processes")
def list_business_processes(
    q: str = "",
    limit: int = 5000,
    offset: int = 0,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    return service.list_business_processes(
        q=q, limit=limit, offset=offset, user=user, db=db
    )


@router.post("/v1/pestle/business-processes")
def create_business_process(
    payload: BusinessProcessPayload,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    return service.create_business_process(payload=payload, user=user, db=db)


@router.patch("/v1/pestle/business-processes/{process_id}")
def update_business_process(
    process_id: str,
    payload: BusinessProcessPayload,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    return service.update_business_process(
        process_id=process_id, payload=payload, user=user, db=db
    )


@router.delete("/v1/pestle/business-processes/{process_id}")
def delete_business_process(
    process_id: str,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    return service.delete_business_process(process_id=process_id, user=user, db=db)


@router.get("/v1/pestle/items/{item_id}/business-processes")
def get_item_business_process_relevance(
    item_id: str,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    return service.get_item_business_process_relevance(
        item_id=item_id, user=user, db=db
    )


@router.patch("/v1/pestle/items/{item_id}/business-processes")
def replace_item_business_process_relevance(
    item_id: str,
    payload: BusinessProcessLinksPayload,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    return service.replace_item_business_process_relevance(
        item_id=item_id, payload=payload, user=user, db=db
    )


@router.get("/v1/pestle/items/{item_id}/clauses")
def get_item_clause_relevance(
    item_id: str,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    return service.get_item_clause_relevance(item_id=item_id, user=user, db=db)


@router.patch("/v1/pestle/items/{item_id}/clauses")
def replace_item_clause_relevance(
    item_id: str,
    payload: ClauseLinksPayload,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    return service.replace_item_clause_relevance(
        item_id=item_id, payload=payload, user=user, db=db
    )


@router.get("/v1/pestle/items/{item_id}/changelog")
def pestle_item_changelog(
    item_id: str,
    limit: int = 50,
    offset: int = 0,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    return service.pestle_item_changelog(
        item_id=item_id, limit=limit, offset=offset, user=user, db=db
    )


@router.get("/v1/pestle/business-processes/{process_id}/changelog")
def pestle_business_process_changelog(
    process_id: str,
    limit: int = 50,
    offset: int = 0,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    return service.pestle_business_process_changelog(
        process_id=process_id, limit=limit, offset=offset, user=user, db=db
    )


@router.get("/v1/pestle/visualisations")
def pestle_visualisations(
    framework: str = settings.default_framework_slug,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    return service.pestle_visualisations(framework=framework, user=user, db=db)


@router.get("/v1/clauses/{clause_id}/pestle")
def clause_pestle_relevance(
    clause_id: str,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    return service.clause_pestle_relevance(clause_id=clause_id, user=user, db=db)
