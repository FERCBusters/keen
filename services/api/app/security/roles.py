"""Role resolution helpers.

Each user holds an explicit role (admin or normal) or ``inherit``, which takes
the effective role from the roles of the user's groups. Among several group
roles, admin wins over normal.
"""

from __future__ import annotations

from typing import TypeGuard

from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from app.db.models import Group, User, user_groups
from app.security.auth import ROLE_ADMIN, ROLE_INHERIT, ROLE_NORMAL


def is_active_user(user: object) -> TypeGuard[User]:
    """Whether ``user`` is an active account (and not an anonymous request)."""
    return isinstance(user, User) and user.is_active


def normalize_role(raw: str | None) -> str | None:
    r = (raw or "").strip().lower()
    if not r:
        return None
    if r in (ROLE_ADMIN, ROLE_NORMAL, ROLE_INHERIT):
        return r
    return None


def compute_effective_role(db: Session, user: User) -> str:
    """Return the user's effective role (admin/normal).

    If the user has role='inherit', resolve from group roles.
    """
    if not is_active_user(user):
        return ROLE_NORMAL

    explicit = normalize_role(user.role) or ROLE_NORMAL
    if explicit != ROLE_INHERIT:
        # Explicit admin/normal
        return ROLE_ADMIN if explicit == ROLE_ADMIN else ROLE_NORMAL

    # Inherit from groups
    uid = user.id
    roles = (
        db.query(Group.role)
        .join(user_groups, user_groups.c.group_id == Group.id)
        .filter(user_groups.c.user_id == uid)
        .all()
    )

    norm = {normalize_role(r) for (r,) in roles if r}
    if ROLE_ADMIN in norm:
        return ROLE_ADMIN
    if ROLE_NORMAL in norm:
        return ROLE_NORMAL
    # Default for inherit with no group role set
    return ROLE_NORMAL


def attach_effective_role(db: Session, user: User) -> str:
    """Compute and attach user.effective_role (non-persisted attribute)."""
    eff = compute_effective_role(db, user)
    user.effective_role = eff
    return eff


def is_effective_admin(db: Session, user: User) -> bool:
    if user.effective_role:
        return user.effective_role == ROLE_ADMIN
    return compute_effective_role(db, user) == ROLE_ADMIN


_ADMIN_CHANGE_LOCK = 75431010


def lock_admin_changes(db: Session) -> None:
    """Serialise changes that can remove effective administrators.

    Call before reading the state a lockout guard depends on. The transaction
    lock is released on commit or rollback, so concurrent demotions are counted
    one after another and cannot jointly remove the last administrator.
    """
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADMIN_CHANGE_LOCK}
        )


def count_effective_admins(db: Session, *, excluding_user_id=None) -> int:
    """Count active users whose *effective* role is admin.

    Used to prevent lockout (removing the last admin).
    """
    q = db.query(User.id).filter(User.is_active.is_(True))
    if excluding_user_id is not None:
        q = q.filter(User.id != excluding_user_id)

    # Explicit admins
    explicit_admin = User.role == ROLE_ADMIN

    # Inherited admins: role='inherit' and member of a group with role='admin'
    inherited_admin = (User.role == ROLE_INHERIT) & exists(
        select(1)
        .select_from(user_groups.join(Group, Group.id == user_groups.c.group_id))
        .where(user_groups.c.user_id == User.id)
        .where(Group.role == ROLE_ADMIN)
    )

    q = q.filter(explicit_admin | inherited_admin)
    return int(q.count())
