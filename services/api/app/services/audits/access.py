from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import (
    Audit,
    AuditAttendee,
    Event,
    User,
)
from app.security.diary_visibility import is_diary_event_visible
from app.security.permissions import has_permission
from app.services.audits.errors import AuditError
from app.services.audits.validation import try_uuid

AUDIT_MANAGE_PERMISSION = "audits.manage"


AUDIT_READ_PERMISSION = "audits.read"


def can_read_audits(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    # audits.manage implies read access, and admins are granted implicitly by has_permission().
    return has_permission(db, user, AUDIT_MANAGE_PERMISSION) or has_permission(
        db, user, AUDIT_READ_PERMISSION
    )


def can_manage_audits(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return has_permission(db, user, AUDIT_MANAGE_PERMISSION)


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
        raise AuditError(status_code=403, detail="Audit access required")


def audit_or_404(db: Session, audit_id: str) -> Audit:
    aid = try_uuid(audit_id)
    if not aid:
        raise AuditError(status_code=400, detail="audit_id must be a UUID")
    row = db.query(Audit).filter(Audit.id == aid).one_or_none()
    if not row:
        raise AuditError(status_code=404, detail="Audit not found")
    return row


def can_see_event(db: Session, user: User, event_id: uuid.UUID) -> Event:
    row = db.query(Event).filter(Event.id == event_id).one_or_none()
    if not row:
        raise AuditError(status_code=404, detail="Event not found")
    if row.source == "diary" and not is_diary_event_visible(db, user, event_id):
        raise AuditError(status_code=404, detail="Event not found")
    return row
