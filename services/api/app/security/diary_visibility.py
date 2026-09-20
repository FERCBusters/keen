from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import Event, User


def diary_filter_condition(db: Session, user: User):
    """Return the SQLAlchemy condition for diary-event visibility.

    Diary entries are visible to every active authenticated user. Anonymous or
    inactive callers still do not see diary events.
    """
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return Event.source != "diary"
    return True


def is_diary_event_visible(db: Session, user: User, event_id: uuid.UUID) -> bool:
    """Return whether a caller can see a diary event."""
    return isinstance(user, User) and bool(getattr(user, "is_active", False))
