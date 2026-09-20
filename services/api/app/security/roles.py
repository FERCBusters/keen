from __future__ import annotations

"""Role resolution helpers.

Keen historically used a simple per-user role model:
  - admin
  - normal

To support organising users into groups, we allow roles to be assigned to
groups as well. Users can either:
  - have an explicit role (admin/normal), or
  - set role='inherit' and have their effective role resolved from group roles.

If a user inherits, 'admin' wins over 'normal' when multiple group roles
are present.
"""

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db.models import Group, User, user_groups
from app.security.auth import ROLE_ADMIN, ROLE_INHERIT, ROLE_NORMAL


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
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return ROLE_NORMAL

    explicit = normalize_role(getattr(user, "role", None)) or ROLE_NORMAL
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
    try:
        setattr(user, "effective_role", eff)
    except Exception:
        pass
    return eff


def is_effective_admin(db: Session, user: User) -> bool:
    eff = getattr(user, "effective_role", None)
    if eff:
        return (eff or "") == ROLE_ADMIN
    return compute_effective_role(db, user) == ROLE_ADMIN


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
