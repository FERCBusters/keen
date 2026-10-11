"""Administrator-owned connections, draft/live collectors and asynchronous previews."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.security.auth import require_admin
from app.services import integrations as service
from app.services.integrations import (
    Action,
    CollectorInput,
    ConnectionInput,
    SampleInput,
)


def integration_demo_guard(request: Request):
    if settings.demo_mode and request.method not in ("GET", "HEAD", "OPTIONS"):
        raise HTTPException(
            403,
            "You are running in a demo environment. New API integrations cannot be configured or run here.",
        )


router = Router(
    prefix="/v1/admin/integrations",
    dependencies=[Depends(require_admin), Depends(integration_demo_guard)],
)


@router.get("")
def overview(db: Session = Depends(get_db)):
    return service.overview(db=db)


@router.post("/connections")
def create_connection(body: ConnectionInput, db: Session = Depends(get_db)):
    return service.create_connection(body=body, db=db)


@router.put("/connections/{key}")
def save_connection(key: str, body: ConnectionInput, db: Session = Depends(get_db)):
    return service.save_connection(key=key, body=body, db=db)


@router.post("/collectors")
def create_collector(body: CollectorInput, db: Session = Depends(get_db)):
    return service.create_collector(body=body, db=db)


@router.put("/collectors/{key}")
def save_collector(key: str, body: CollectorInput, db: Session = Depends(get_db)):
    return service.save_collector(key=key, body=body, db=db)


@router.get("/collectors/{key}/revisions")
def revisions(key: str, db: Session = Depends(get_db)):
    return service.revisions(key=key, db=db)


@router.post("/collectors/{key}/{action}")
def action_collector(
    key: str, action: str, body: Action, db: Session = Depends(get_db)
):
    return service.action_collector(key=key, action=action, body=body, db=db)


@router.get("/runs/{key}")
def read_run(key: str, db: Session = Depends(get_db)):
    return service.read_run(key=key, db=db)


@router.post("/preview-sample")
def preview_sample(body: SampleInput, db: Session = Depends(get_db)):
    return service.preview_sample(body=body, db=db)
