"""HTTP dependency for evidence (events.read) permissions."""

from __future__ import annotations

from fastapi import HTTPException, Request
from mosp.security.auth import require_authenticated
from sqlalchemy.orm import Session

from app.db.models import User
from app.security.permissions import can_read_events


def require_events_read(db: Session, request: Request) -> User:
    user = require_authenticated(request)
    if not can_read_events(db, user):
        raise HTTPException(status_code=403, detail="events.read permission required")
    return user
