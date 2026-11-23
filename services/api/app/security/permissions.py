"""KEEN's permission catalogue and the checks that depend on it; mosp holds the
permission mechanism (``mosp.security.permissions``)."""

from __future__ import annotations

from mosp.security.permissions import has_any_permission, has_permission
from mosp.security.roles import is_active_user, is_effective_admin
from sqlalchemy.orm import Session

from app.db.models import User

EVENTS_READ_PERMISSION = "events.read"
ISMS_READ_PERMISSION = "isms.read"

# Permissions that include others. Whoever samples evidence into an audit is an
# auditor, who reads the events and ISMS records an audit examines. Implied
# permissions imply nothing further.
PERMISSION_IMPLICATIONS: dict[str, frozenset[str]] = {
    "audits.manage": frozenset({EVENTS_READ_PERMISSION, ISMS_READ_PERMISSION}),
    "audits.admin": frozenset({EVENTS_READ_PERMISSION, ISMS_READ_PERMISSION}),
}


def has_shared_permission(db: Session, user: User | None, *codes: str) -> bool:
    """Whether a person holds one of ``codes`` where it stands in for another
    domain's permission.

    In Keen a risk reader also reads the ISMS registers and PESTLE(E)
    items, and a risk manager manages them. That rule is for a person
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
