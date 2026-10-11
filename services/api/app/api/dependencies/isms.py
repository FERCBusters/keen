"""HTTP dependencies for ISMS permissions."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.services.isms.access import _can_manage_isms, _can_read_isms


def require_isms_read(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not _can_read_isms(db, user):
        raise HTTPException(status_code=403, detail="isms.read permission required")
    return user


def require_isms_manage(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not _can_manage_isms(db, user):
        raise HTTPException(status_code=403, detail="isms.manage permission required")
    return user
