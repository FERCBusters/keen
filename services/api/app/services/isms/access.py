"""ISMS permission predicates shared by entry points."""

import uuid

from sqlalchemy import CompoundSelect, select
from sqlalchemy.orm import Session

from app.db.models import IsmsMeetingAttendee, IsmsMeetingPerson, IsmsPerson, User
from app.security.permissions import has_any_permission
from app.security.roles import is_active_user

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


def meeting_ids_for(user: User) -> CompoundSelect:
    """Meetings a person is recorded on, as an attendee or with apologies,
    through their account or a People record linked to it."""
    people = select(IsmsPerson.id).where(IsmsPerson.user_id == user.id)
    return (
        select(IsmsMeetingAttendee.meeting_id)
        .where(IsmsMeetingAttendee.user_id == user.id)
        .union(
            select(IsmsMeetingPerson.meeting_id).where(
                IsmsMeetingPerson.person_id.in_(people)
            )
        )
    )


def can_read_meeting(db: Session, user: User | None, meeting_id: uuid.UUID) -> bool:
    """ISMS readers read every meeting; others read the meetings they are on."""
    if not is_active_user(user):
        return False
    if can_read_isms(db, user):
        return True
    on = meeting_ids_for(user).subquery()
    found = db.scalar(
        select(on.c.meeting_id).where(on.c.meeting_id == meeting_id).limit(1)
    )
    return found is not None
