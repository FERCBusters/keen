from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import Audit, AuditAttendee, Event, Risk, User
from app.security.diary_visibility import is_diary_event_visible
from app.security.permissions import has_permission
from app.services.audits.validation import try_uuid
from app.services.errors import DomainError

AUDIT_MANAGE_PERMISSION = "audits.manage"
AUDIT_ADMIN_PERMISSION = "audits.admin"


AUDIT_READ_PERMISSION = "audits.read"


def can_read_audits(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    # Audit managers and administrators can read audits; application admins have all permissions.
    return can_manage_audits(db, user) or has_permission(
        db, user, AUDIT_READ_PERMISSION
    )


def can_manage_audits(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return has_permission(db, user, AUDIT_MANAGE_PERMISSION) or can_admin_audits(
        db, user
    )


def is_audit_attendee(db: Session, user: User | None, audit_id: uuid.UUID) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return bool(
        db.query(AuditAttendee.id)
        .filter(AuditAttendee.audit_id == audit_id, AuditAttendee.user_id == user.id)
        .first()
    )


def can_read_audit(db: Session, user: User | None, audit: Audit) -> bool:
    return bool(can_read_audits(db, user) or is_audit_attendee(db, user, audit.id))


def require_audit_access(db: Session, user: User | None, audit: Audit) -> None:
    if not can_read_audit(db, user, audit):
        raise DomainError(status_code=403, detail="Audit access required")


def audit_or_404(db: Session, audit_id: str) -> Audit:
    aid = try_uuid(audit_id)
    if not aid:
        raise DomainError(status_code=400, detail="audit_id must be a UUID")
    row = db.query(Audit).filter(Audit.id == aid).one_or_none()
    if not row:
        raise DomainError(status_code=404, detail="Audit not found")
    return row


def can_see_event(db: Session, user: User, event_id: uuid.UUID) -> Event:
    if not has_permission(db, user, "events.read"):
        raise DomainError(403, "events.read permission required")
    row = db.query(Event).filter(Event.id == event_id).one_or_none()
    if not row:
        raise DomainError(status_code=404, detail="Event not found")
    if row.source == "diary" and not is_diary_event_visible(db, user, event_id):
        raise DomainError(status_code=404, detail="Event not found")
    return row


def require_sample_source_access(
    db: Session, user: User, entity_type: str, entity_id: uuid.UUID
) -> None:
    """Adding a sample requires source access before audit sharing is established."""
    if entity_type == "risk":
        from app.services.risks import _can_read_specific_risk

        allowed = _can_read_specific_risk(db, user, db.get(Risk, entity_id))
    elif entity_type == "isms_swot":
        from app.services.isms.access import _can_read_isms

        allowed = _can_read_isms(db, user)
    elif entity_type == "pestle_item":
        from app.services.pestle import _can_read_pestle

        allowed = _can_read_pestle(db, user)
    elif entity_type == "interested_party":
        from app.services.interested_parties import _can_read_interested_parties

        allowed = _can_read_interested_parties(db, user)
    elif entity_type == "bookstack_section" or entity_type.startswith("isms_"):
        allowed = has_permission(db, user, "isms.read")
    else:
        allowed = False
    if not allowed:
        raise DomainError(403, "Read access to the sampled source is required")


def can_admin_audits(db: Session, user: User | None) -> bool:
    return has_permission(db, user, AUDIT_ADMIN_PERMISSION)


def require_audit_admin(db: Session, user: User) -> None:
    if not can_admin_audits(db, user):
        raise DomainError(403, "audits.admin permission required")


def editable_audit_or_404(db: Session, user: User, audit_id: str) -> Audit:
    audit = audit_or_404(db, audit_id)
    if not (
        can_admin_audits(db, user)
        or (can_manage_audits(db, user) and audit.created_by_user_id == user.id)
    ):
        raise DomainError(
            403, "Only the audit creator or an audit administrator can edit this audit"
        )
    return audit
