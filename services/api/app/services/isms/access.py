"""ISMS permission predicates shared by entry points."""

from sqlalchemy.orm import Session

from app.db.models import User
from app.security.permissions import has_any_permission

from .constants import (
    ISMS_MANAGE_PERMISSION,
    ISMS_READ_PERMISSION,
    RISK_MANAGE_PERMISSION,
    RISK_READ_PERMISSION,
)


def can_read_isms(db: Session, user: User | None) -> bool:
    return has_any_permission(
        db,
        user,
        ISMS_READ_PERMISSION,
        ISMS_MANAGE_PERMISSION,
        RISK_READ_PERMISSION,
        RISK_MANAGE_PERMISSION,
    )


def can_manage_isms(db: Session, user: User | None) -> bool:
    return has_any_permission(db, user, ISMS_MANAGE_PERMISSION, RISK_MANAGE_PERMISSION)
