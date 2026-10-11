"""Administrator approved, directed control relationships across frameworks."""

from __future__ import annotations

import uuid

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.routing import Router
from app.db.models import User
from app.db.session import get_db
from app.security.auth import require_admin
from app.services import control_links as service
from app.services.control_links import LinkInput

router = Router()


@router.get("/v1/controls/{target_id}/cross-framework")
def list_control_links(target_id: uuid.UUID, db: Session = Depends(get_db)):
    return service.list_control_links(target_id=target_id, db=db)


@router.post("/v1/controls/{target_id}/cross-framework", status_code=201)
def create_control_link(
    target_id: uuid.UUID,
    payload: LinkInput,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.create_control_link(
        target_id=target_id, payload=payload, user=user, db=db
    )


@router.delete("/v1/controls/{target_id}/cross-framework/{link_id}", status_code=204)
def remove_control_link(
    target_id: uuid.UUID,
    link_id: uuid.UUID,
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return service.remove_control_link(
        target_id=target_id, link_id=link_id, user=user, db=db
    )
