"""ISMS permission predicates shared by entry points."""

from sqlalchemy.orm import Session

from app.db.models import User
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
