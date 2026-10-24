"""Who may see and change quizzes, and the person behind an account."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import IsmsPerson, User
from app.security.permissions import has_permission
from app.services.assurance import person_for_user
from app.services.errors import DomainError

READ = "quiz.read"
MANAGE = "quiz.manage"
# Fewer completed responses than this keep a round's results with quiz.manage,
# so no one else can read one person's score off an average.
MIN_COHORT = 3


def can_manage_quizzes(db: Session, user: User) -> bool:
    return has_permission(db, user, MANAGE)


def can_read_quizzes(db: Session, user: User) -> bool:
    return can_manage_quizzes(db, user) or has_permission(db, user, READ)


def require_manage(db: Session, user: User) -> None:
    if not can_manage_quizzes(db, user):
        raise DomainError(status_code=403, detail="quiz.manage permission required")


def require_read(db: Session, user: User) -> None:
    if not can_read_quizzes(db, user):
        raise DomainError(status_code=403, detail="quiz.read permission required")


def own_person(db: Session, user: User) -> IsmsPerson:
    """The People record a student sits quizzes as."""
    person = person_for_user(db, user.id)
    if person is None:
        raise DomainError(
            status_code=409,
            detail="Your account is not linked to a People record yet; "
            "ask an ISMS manager to link it",
        )
    return person
