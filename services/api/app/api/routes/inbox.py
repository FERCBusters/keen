"""HTTP adapters for each person's inbox."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.routing import Router
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.services import inbox as service

router = Router()


@router.get("/v1/me/inbox/summary")
def inbox_summary(user=Depends(require_authenticated), db: Session = Depends(get_db)):
    "How many notifications the signed-in person has not read."
    return service.inbox_summary(db=db, user=user)


@router.get("/v1/me/inbox")
def list_inbox(
    unread: bool = False,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    "The signed-in person's latest notifications, newest first."
    return service.list_inbox(unread=unread, db=db, user=user)


@router.post("/v1/me/inbox/read-all")
def mark_all_read(user=Depends(require_authenticated), db: Session = Depends(get_db)):
    "Mark every notification as read."
    return service.mark_all_read(db=db, user=user)


@router.post("/v1/me/inbox/{item_id}/read")
def mark_read(
    item_id: str, user=Depends(require_authenticated), db: Session = Depends(get_db)
):
    "Mark one of the signed-in person's notifications as read."
    return service.mark_read(item_id=item_id, db=db, user=user)
