"""Account-linked People records, within the provisioning transaction."""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    AuditAttendee,
    IsmsPerson,
    QuizAttempt,
    QuizAudience,
    QuizParticipant,
    User,
)
from app.security.permissions import has_permission
from app.services.errors import DomainError
from app.services.mailer import user_email

# What an account receives through the People record linked to it, and the
# permission that may move or remove that link. Moving the link moves the
# authority with it, so whoever moves it must already hold the authority over
# what the record carries. Every access rule that reads a People record's
# account is listed in tests/test_people_link_authority.py, which fails when
# one appears without an entry here or a reason it needs none.
LINK_AUTHORITIES = (
    # A person's quiz assignments, rounds and attempts.
    ("sits quizzes", "quiz.manage", (QuizAudience, QuizParticipant, QuizAttempt)),
    # Reading the audits the person is named on as an attendee.
    ("attends audits", "audits.admin", (AuditAttendee,)),
)


def require_link_authority(db: Session, actor: User, person_id: uuid.UUID, action):
    """Refuse a change to a People record's account, or its deletion, that
    would move authority the actor does not hold over the record's quizzes
    or audits. ``action`` completes the refusal: "Changing the Keen account
    of" or "Deleting"."""
    for carries, permission, models in LINK_AUTHORITIES:
        held = any(
            db.query(model.id).filter(model.person_id == person_id).first() is not None
            for model in models
        )
        if held and not has_permission(db, actor, permission):
            raise DomainError(403, f"{action} someone who {carries} needs {permission}")


def person_for_user(db: Session, user_id) -> IsmsPerson | None:
    """The People record linked to a Keen account, if there is one."""
    if user_id is None:
        return None
    return db.query(IsmsPerson).filter(IsmsPerson.user_id == user_id).one_or_none()


def ensure_person_for_user(db: Session, account: User) -> IsmsPerson:
    """The account's People record, created from the account when it has none.

    Runs in the caller's transaction. A concurrent request linking the same
    account wins the unique link; this request then uses that record.
    """
    existing = person_for_user(db, account.id)
    if existing is not None:
        return existing
    person = IsmsPerson(
        name=(account.username or "Keen user")[:256],
        email=user_email(account) or "",
        user_id=account.id,
    )
    try:
        with db.begin_nested():
            db.add(person)
            db.flush()
    except IntegrityError:
        found = person_for_user(db, account.id)
        if found is None:
            raise
        return found
    return person
