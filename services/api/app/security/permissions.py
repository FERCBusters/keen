from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.valkey import get_valkey
from app.db.models import (
    Permission,
    User,
    group_permissions,
    user_groups,
    user_permissions,
)
from app.security.auth import ROLE_ADMIN
from app.security.roles import compute_effective_role
from app.security.sessions import update_session_authz


def _normalise_cached_permission_codes(raw) -> set[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return {raw} if raw else set()
    try:
        return {str(c) for c in raw if c}
    except Exception:
        return None


def _attach_permission_snapshot(user: User, *, role: str, codes: set[str]) -> None:
    try:
        setattr(user, "effective_role", role)
        setattr(user, "effective_permission_codes", set(codes or set()))
    except Exception:
        pass


def get_effective_permission_codes(
    db: Session, user: User, *, use_cache: bool = True
) -> set[str]:
    """Return the set of permission codes the user has (direct + via groups).

    Admin role implicitly has all permissions.
    """
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return set()

    if use_cache:
        cached = _normalise_cached_permission_codes(
            getattr(user, "effective_permission_codes", None)
        )
        if cached is not None:
            return cached

    eff = getattr(user, "effective_role", None) if use_cache else None
    if not eff:
        eff = compute_effective_role(db, user)
        try:
            setattr(user, "effective_role", eff)
        except Exception:
            pass

    if (eff or "") == ROLE_ADMIN:
        # Admin is treated as 'allow all'. Return a sentinel-ish set.
        return {"*"}

    uid = user.id

    direct = (
        db.query(Permission.code)
        .join(user_permissions, Permission.id == user_permissions.c.permission_id)
        .filter(user_permissions.c.user_id == uid)
        .all()
    )
    direct_codes = {c for (c,) in direct if c}

    group_codes = (
        db.query(Permission.code)
        .join(group_permissions, Permission.id == group_permissions.c.permission_id)
        .join(user_groups, user_groups.c.group_id == group_permissions.c.group_id)
        .filter(user_groups.c.user_id == uid)
        .all()
    )
    via_group = {c for (c,) in group_codes if c}

    return direct_codes | via_group


def ensure_session_authorization_current(db: Session, user: User) -> None:
    """Keep the per-session permission snapshot current without DB work per page.

    Local/OIDC logins store the effective role + permission codes in the Valkey
    session at login time. RBAC admin changes bump a cheap per-user authz version.
    On normal requests we compare that Valkey integer; if it has not changed,
    has_permission() can use the attached snapshot and avoid querying permission
    tables repeatedly while rendering pages and nav. When the version changes, we
    recompute once from the DB and refresh the session snapshot.

    Trusted REMOTE_USER deployments do not have a KEEN session to cache into, so
    they fall back to the existing DB-backed behaviour.
    """
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return

    sid = getattr(user, "session_id", None)
    cached_codes = _normalise_cached_permission_codes(
        getattr(user, "effective_permission_codes", None)
    )
    cached_role = getattr(user, "effective_role", None)
    cached_version = getattr(user, "session_authz_version", None)

    if not sid:
        # No KEEN-owned session (for example trusted remote-user auth). Compute the
        # role for this request and let has_permission() query the DB as before.
        if not cached_role:
            try:
                setattr(user, "effective_role", compute_effective_role(db, user))
            except Exception:
                pass
        return

    try:
        current_version = int(getattr(user, "authz_version", 0) or 0)
    except Exception:
        current_version = None

    if (
        current_version is not None
        and cached_version is not None
        and int(cached_version) == int(current_version)
        and cached_role
        and cached_codes is not None
    ):
        return

    # Cache miss/stale snapshot: recompute once from the DB and write the refreshed
    # result back into the server-side session.
    role = compute_effective_role(db, user)
    try:
        setattr(user, "effective_role", role)
    except Exception:
        pass
    codes = get_effective_permission_codes(db, user, use_cache=False)
    _attach_permission_snapshot(user, role=role, codes=codes)

    if current_version is None:
        return
    try:
        r = get_valkey()
        setattr(user, "session_authz_version", int(current_version))
        update_session_authz(
            r,
            str(sid),
            effective_role=role,
            permission_codes=codes,
            authz_version=int(current_version),
            ttl_seconds=int(settings.session_ttl_seconds),
        )
    except Exception:
        pass


def has_permission(db: Session, user: User, code: str) -> bool:
    """Check whether a user has a given permission code."""
    c = (code or "").strip()
    if not c:
        return False
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False

    cached = _normalise_cached_permission_codes(
        getattr(user, "effective_permission_codes", None)
    )
    if cached is not None:
        return "*" in cached or c in cached

    eff = getattr(user, "effective_role", None)
    if not eff:
        eff = compute_effective_role(db, user)
        try:
            setattr(user, "effective_role", eff)
        except Exception:
            pass

    if (eff or "") == ROLE_ADMIN:
        return True

    uid = user.id

    # Direct
    hit = (
        db.query(Permission.id)
        .join(user_permissions, Permission.id == user_permissions.c.permission_id)
        .filter(user_permissions.c.user_id == uid)
        .filter(Permission.code == c)
        .first()
    )
    if hit:
        return True

    # Via group
    hit2 = (
        db.query(Permission.id)
        .join(group_permissions, Permission.id == group_permissions.c.permission_id)
        .join(user_groups, user_groups.c.group_id == group_permissions.c.group_id)
        .filter(user_groups.c.user_id == uid)
        .filter(Permission.code == c)
        .first()
    )
    return bool(hit2)


def normalize_permission_codes(codes: list[str] | None) -> list[str]:
    """Normalize permission codes: strip, de-dupe, stable sort."""
    if not codes:
        return []
    out: list[str] = []
    seen = set()
    for raw in codes:
        c = (raw or "").strip()
        if not c:
            continue
        if len(c) > 128:
            continue
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    out.sort()
    return out


def normalize_group_name(name: str) -> str:
    return (name or "").strip()


def normalize_group_description(desc: str | None) -> str | None:
    d = (desc or "").strip()
    return d or None
