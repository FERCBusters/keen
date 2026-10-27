"""Account-linked People records, within the provisioning transaction."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import IsmsPerson, User
from app.services.mailer import user_email


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
