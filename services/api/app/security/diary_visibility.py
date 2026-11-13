"""Visibility of diary events.

Diary entries are visible to every active authenticated user who can read
events. Automatic framework-link entries describe risk and ISMS records, whose
read access is granted separately from evidence access, so each entry follows
the read rule of the record type it describes. Entries about risks require
risk read or manage permission. Entries about other ISMS records require ISMS
read access, which risk read or manage permission also grants to a person. An
MCP connection reads the entries its own scopes name: risk entries with the
risk scope, entries about other ISMS records with the ISMS scope.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import Event, User
from app.security.permissions import has_any_permission, has_shared_permission
from app.security.roles import is_active_user

DIARY_SOURCE = "diary"
# Automatic framework-link entries use this system name and an external ID of
# the form "isms-link:<record type>:<record UUID>".
LINK_ENTRY_SYSTEM = "isms"
LINK_ENTRY_PREFIX = "isms-link:"
RISK_LINK_ENTRY_PREFIX = LINK_ENTRY_PREFIX + "risk:"


def link_entry_external_id(record_type: str, record_id: uuid.UUID) -> str:
    """Return the stable external ID of a record's automatic link entry."""

    return f"{LINK_ENTRY_PREFIX}{record_type}:{record_id}"


def _link_entry_access(db: Session, user: User) -> tuple[bool, bool]:
    """Return whether the caller may read risk and ISMS entries.

    These mirror the domain read predicates (services/risks.py
    ``can_read_risks`` and services/isms/access.py ``can_read_isms``); the
    tests hold them in step.
    """

    risks = has_any_permission(db, user, "risk.read", "risk.manage")
    isms = has_any_permission(
        db, user, "isms.read", "isms.manage"
    ) or has_shared_permission(db, user, "risk.read", "risk.manage")
    return risks, isms


def diary_filter_condition(db: Session, user: User):
    """Return the SQLAlchemy condition for diary-event visibility."""

    if not is_active_user(user):
        return Event.source != DIARY_SOURCE
    risks, isms = _link_entry_access(db, user)
    if risks and isms:
        return True
    not_link_entry = or_(
        Event.source != DIARY_SOURCE,
        Event.system.is_(None),
        Event.system != LINK_ENTRY_SYSTEM,
    )
    about_a_risk = Event.external_id.startswith(RISK_LINK_ENTRY_PREFIX, autoescape=True)
    allowed = [not_link_entry]
    if isms:
        allowed.append(~about_a_risk)
    if risks:
        # An MCP connection granted the risk scope alone: a person who reads
        # risks reads the other entries too.
        allowed.append(about_a_risk)
    return or_(*allowed)


def is_diary_event_visible(db: Session, user: User, event_id: uuid.UUID) -> bool:
    """Return whether a caller can see a diary event."""

    if not is_active_user(user):
        return False
    row = (
        db.query(Event.source, Event.system, Event.external_id)
        .filter(Event.id == event_id)
        .first()
    )
    if row is None:
        return False
    source, system, external_id = row
    if source != DIARY_SOURCE or system != LINK_ENTRY_SYSTEM:
        return True
    risks, isms = _link_entry_access(db, user)
    if (external_id or "").startswith(RISK_LINK_ENTRY_PREFIX):
        return risks
    return isms
