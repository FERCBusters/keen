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
from app.security.roles import (
    compute_effective_role,
    is_active_user,
    is_effective_admin,
)
from app.security.sessions import update_session_authz

EVENTS_READ_PERMISSION = "events.read"
ISMS_READ_PERMISSION = "isms.read"

# Permissions that include others. Whoever samples evidence into an audit is an
# auditor, who reads the events and ISMS records an audit examines. Implied
# permissions imply nothing further.
PERMISSION_IMPLICATIONS: dict[str, frozenset[str]] = {
    "audits.manage": frozenset({EVENTS_READ_PERMISSION, ISMS_READ_PERMISSION}),
    "audits.admin": frozenset({EVENTS_READ_PERMISSION, ISMS_READ_PERMISSION}),
}


def with_implied_permissions(codes) -> set[str]:
    """The codes held, together with the codes they imply."""
    held = {str(c) for c in codes if c}
    implied = set(held)
    for code in held:
        implied |= PERMISSION_IMPLICATIONS.get(code, frozenset())
    return implied


def _granting_codes(code: str) -> set[str]:
    """The codes that grant ``code``: the code itself and any that imply it."""
    return {code} | {
        holder for holder, implied in PERMISSION_IMPLICATIONS.items() if code in implied
    }


def _normalise_cached_permission_codes(raw) -> set[str] | None:
    # Sessions attach a set of codes; None means no snapshot is attached.
    if raw is None:
        return None
    return {str(c) for c in raw if c}


def _attach_permission_snapshot(user: User, *, role: str, codes: set[str]) -> None:
    user.effective_role = role
    user.effective_permission_codes = set(codes or set())


def get_effective_permission_codes(
    db: Session, user: User, *, use_cache: bool = True
) -> set[str]:
    """Return the set of permission codes the user has (direct + via groups),
    with the codes they imply.

    Admin role implicitly has all permissions.
    """
    if not is_active_user(user):
        return set()

    if use_cache:
        cached = _normalise_cached_permission_codes(user.effective_permission_codes)
        if cached is not None:
            return with_implied_permissions(cached)

    eff = user.effective_role if use_cache else None
    if not eff:
        eff = compute_effective_role(db, user)
        user.effective_role = eff

    if eff == ROLE_ADMIN:
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

    return with_implied_permissions(direct_codes | via_group)


def ensure_session_authorization_current(db: Session, user: User) -> None:
    """Keep the per-session permission snapshot current without DB work per page.

    Local/OIDC logins store the effective role + permission codes in the Valkey
    session at login time. RBAC admin changes bump a cheap per-user authz version.
    On normal requests we compare that Valkey integer; if it has not changed,
    has_permission() can use the attached snapshot and avoid querying permission
    tables repeatedly while rendering pages and nav. When the version changes, we
    recompute once from the DB and refresh the session snapshot.

    Trusted REMOTE_USER deployments do not have a Keen session to cache into, so
    they fall back to the existing DB-backed behaviour.
    """
    if not is_active_user(user):
        return

    sid = user.session_id
    cached_codes = _normalise_cached_permission_codes(user.effective_permission_codes)
    cached_role = user.effective_role
    cached_version = user.session_authz_version

    if not sid:
        # No Keen-owned session (for example trusted remote-user auth). Compute the
        # role for this request and let has_permission() query the DB as before.
        if not cached_role:
            user.effective_role = compute_effective_role(db, user)
        return

    current_version = user.authz_version
    if (
        cached_version is not None
        and int(cached_version) == current_version
        and cached_role
        and cached_codes is not None
    ):
        return

    # Cache miss/stale snapshot: recompute once from the DB and write the refreshed
    # result back into the server-side session.
    role = compute_effective_role(db, user)
    codes = get_effective_permission_codes(db, user, use_cache=False)
    _attach_permission_snapshot(user, role=role, codes=codes)
    try:
        r = get_valkey()
        user.session_authz_version = current_version
        update_session_authz(
            r,
            str(sid),
            effective_role=role,
            permission_codes=codes,
            authz_version=current_version,
            ttl_seconds=settings.session_ttl_seconds,
            max_age_seconds=settings.session_max_age_seconds,
        )
    except Exception:
        pass


def has_permission(db: Session, user: User | None, code: str) -> bool:
    """Check whether a user has a given permission code, directly, through a
    group or through a permission that implies it."""
    c = code.strip()
    if not c:
        return False
    if not is_active_user(user):
        return False

    granting = _granting_codes(c)
    cached = _normalise_cached_permission_codes(user.effective_permission_codes)
    if cached is not None:
        # Snapshots made before an implication was added still resolve it.
        return "*" in cached or not cached.isdisjoint(granting)

    eff = user.effective_role
    if not eff:
        eff = compute_effective_role(db, user)
        user.effective_role = eff

    if eff == ROLE_ADMIN:
        return True

    uid = user.id

    # Direct
    hit = (
        db.query(Permission.id)
        .join(user_permissions, Permission.id == user_permissions.c.permission_id)
        .filter(user_permissions.c.user_id == uid)
        .filter(Permission.code.in_(sorted(granting)))
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
        .filter(Permission.code.in_(sorted(granting)))
        .first()
    )
    return bool(hit2)


def has_any_permission(db: Session, user: User | None, *codes: str) -> bool:
    """Whether an active user holds at least one of ``codes`` (admins hold all)."""
    return any(has_permission(db, user, code) for code in codes)


def has_shared_permission(db: Session, user: User | None, *codes: str) -> bool:
    """Whether a person holds one of ``codes`` where it stands in for another
    domain's permission.

    In Keen a risk reader also reads the ISMS registers and PESTLE(E)
    factors, and a risk manager manages them. That rule is for a person
    acting as their account. An MCP connection acts with the scopes its person
    approved, and a scope counts for its own domain only: the consent a person
    gave an app names what the app reads and changes. So a connection is never
    admitted here, on any route, whatever its scopes.
    """
    return (
        user is not None
        and not user.scope_limited
        and has_any_permission(db, user, *codes)
    )


def can_read_events(db: Session, user: User | None) -> bool:
    """Evidence records, and aggregates derived from them, require events.read."""
    if not is_active_user(user):
        return False
    return is_effective_admin(db, user) or has_permission(
        db, user, EVENTS_READ_PERMISSION
    )


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
