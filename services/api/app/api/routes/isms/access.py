"""ISMS access; see docs/maintainability-review.md for module boundaries."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.security.permissions import has_permission

from .constants import (
    ISMS_MANAGE_PERMISSION,
    ISMS_READ_PERMISSION,
    RISK_MANAGE_PERMISSION,
    RISK_READ_PERMISSION,
)


def _can_read_isms(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return bool(
        has_permission(db, user, ISMS_READ_PERMISSION)
        or has_permission(db, user, ISMS_MANAGE_PERMISSION)
        or has_permission(db, user, RISK_READ_PERMISSION)
        or has_permission(db, user, RISK_MANAGE_PERMISSION)
    )


def _can_manage_isms(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return bool(
        has_permission(db, user, ISMS_MANAGE_PERMISSION)
        or has_permission(db, user, RISK_MANAGE_PERMISSION)
    )


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
