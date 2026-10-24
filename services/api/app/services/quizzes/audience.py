"""Who a quiz is assigned to, and the people each round is for.

A quiz is assigned to people and to organisation roles. A round is for every
assigned person, every person placed in an assigned role and every account
holding one, as long as they can sign in to sit it. An account assigned
directly, or through a role, without a People record gets one linked to it,
so its scores have a person to belong to.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from functools import partial
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    IsmsOrgNode,
    IsmsOrgNodeUser,
    IsmsPerson,
    Quiz,
    QuizAttempt,
    QuizAudience,
    QuizParticipant,
    QuizRound,
    User,
)
from app.services.assurance import ensure_person_for_user
from app.services.context import ChangeContext
from app.services.errors import DomainError
from app.services.mailer import (
    looks_like_email,
    send_quiz_open_email,
    smtp_configured,
    user_email,
)
from app.services.schedules import deliver_once

from .access import require_manage
from .common import quiz_or_404, quiz_snapshot, record_quiz_change, sitting_url
from .schemas import QuizAudienceInput

log = logging.getLogger(__name__)


def person_email(person: IsmsPerson) -> str | None:
    """The address that reaches a person who can sign in: their own, or their
    account's. Someone whose account is gone or deactivated gets no email."""
    account = person.user
    if account is None or not account.is_active:
        return None
    email = str(person.email or "").strip()
    if looks_like_email(email):
        return email
    return user_email(account)


def round_invite_uid(round_id: uuid.UUID) -> str:
    """One calendar event per round, so a reminder updates the invitation."""
    return f"keen-quiz-round-{round_id}"


def uninvited(db: Session, round_id: uuid.UUID) -> list[uuid.UUID]:
    """A round's participants who have not been told it is ready."""
    return [
        participant_id
        for (participant_id,) in db.query(QuizParticipant.id).filter(
            QuizParticipant.round_id == round_id, QuizParticipant.invited_at.is_(None)
        )
    ]


def invite_participants(db: Session, participant_ids: list[uuid.UUID]) -> int:
    """Email each participant once that a round is ready for them.

    Runs after the round is committed; returns how many were invited.
    """
    if not participant_ids or not smtp_configured():
        return 0

    def compose(participant: QuizParticipant):
        email = person_email(participant.person)
        if not email:
            return False
        round_ = participant.round
        return partial(
            send_quiz_open_email,
            to_email=email,
            person_name=participant.person.name,
            quiz_title=round_.quiz.title,
            opens_on=round_.opens_on.isoformat(),
            due_on=round_.due_on.isoformat(),
            quiz_url=sitting_url(round_.id),
            uid=round_invite_uid(round_.id),
        )

    return deliver_once(
        db,
        model=QuizParticipant,
        marker="invited_at",
        ids=participant_ids,
        compose=compose,
    )


def invite_rounds(db: Session, round_ids: Iterable[uuid.UUID]) -> int:
    """Invite each round's participants who have not been told it is ready."""
    return sum(
        invite_participants(db, uninvited(db, round_id)) for round_id in round_ids
    )


def queue_invitations(round_ids: list[uuid.UUID]) -> None:
    """Hand the rounds' invitations to the worker, so no request waits on the
    mail server; the daily round job sends any the worker misses."""
    if not round_ids or not smtp_configured():
        return
    try:
        from app.worker.tasks import send_quiz_invitations_task

        send_quiz_invitations_task.delay([str(round_id) for round_id in round_ids])
    except Exception:
        log.exception("Could not queue quiz invitations")


def _holders(db: Session, node_ids: set[uuid.UUID]) -> list[tuple[uuid.UUID, Any]]:
    """Active accounts holding the roles, each with its People record id."""
    return (
        db.query(User.id, IsmsPerson.id)
        .select_from(IsmsOrgNodeUser)
        .join(User, User.id == IsmsOrgNodeUser.user_id)
        .outerjoin(IsmsPerson, IsmsPerson.user_id == User.id)
        .filter(
            IsmsOrgNodeUser.org_node_id.in_(node_ids),
            User.is_active.is_(True),
        )
        .distinct()
        .all()
    )


def audience_members(db: Session, quiz: Quiz) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """The people who can sit the quiz, and role-holding accounts without a
    People record yet. Reads only; a round creates the missing records."""
    person_ids = {entry.person_id for entry in quiz.audience if entry.person_id}
    node_ids = {entry.org_node_id for entry in quiz.audience if entry.org_node_id}
    accounts: set[uuid.UUID] = set()
    if node_ids:
        person_ids.update(
            person_id
            for (person_id,) in db.query(IsmsPerson.id).filter(
                IsmsPerson.org_node_id.in_(node_ids)
            )
        )
        for account_id, person_id in _holders(db, node_ids):
            if person_id is None:
                accounts.add(account_id)
            else:
                person_ids.add(person_id)
    if not person_ids:
        return set(), accounts
    # Only people who can sign in can sit a quiz.
    sitting = {
        person_id
        for (person_id,) in db.query(IsmsPerson.id)
        .join(User, User.id == IsmsPerson.user_id)
        .filter(IsmsPerson.id.in_(person_ids), User.is_active.is_(True))
    }
    return sitting, accounts


def round_person_ids(db: Session, quiz: Quiz) -> set[uuid.UUID]:
    """The people a round is for, creating People records it needs."""
    sitting, accounts = audience_members(db, quiz)
    if accounts:
        for account in db.query(User).filter(User.id.in_(accounts)).all():
            sitting.add(ensure_person_for_user(db, account).id)
    return sitting


def sync_participants(db: Session, quiz: Quiz, round_: QuizRound) -> list[uuid.UUID]:
    """Bring an open round's participants into line with the audience.

    New people join; people no longer assigned leave unless they have started
    the round, so nobody loses an attempt. Returns the participants added.
    """
    wanted = round_person_ids(db, quiz)
    current = {
        row.person_id: row
        for row in db.query(QuizParticipant).filter(
            QuizParticipant.round_id == round_.id
        )
    }
    started = {
        person_id
        for (person_id,) in db.query(QuizAttempt.person_id).filter(
            QuizAttempt.round_id == round_.id, QuizAttempt.person_id.isnot(None)
        )
    }
    for person_id, row in current.items():
        if person_id not in wanted and person_id not in started:
            db.delete(row)
    added = []
    for person_id in sorted(wanted - current.keys()):
        row = QuizParticipant(round_id=round_.id, person_id=person_id)
        db.add(row)
        added.append(row)
    db.flush()
    return [row.id for row in added]


def _role_holder_counts(db: Session, node_ids: list[uuid.UUID]) -> dict:
    """People in each role: those placed in it and accounts holding it."""
    counts: dict[uuid.UUID, set] = {node_id: set() for node_id in node_ids}
    if not node_ids:
        return {}
    for node_id, person_id, user_id in db.query(
        IsmsPerson.org_node_id, IsmsPerson.id, IsmsPerson.user_id
    ).filter(IsmsPerson.org_node_id.in_(node_ids)):
        counts[node_id].add(user_id or person_id)
    for node_id, user_id in (
        db.query(IsmsOrgNodeUser.org_node_id, IsmsOrgNodeUser.user_id)
        .join(User, User.id == IsmsOrgNodeUser.user_id)
        .filter(IsmsOrgNodeUser.org_node_id.in_(node_ids), User.is_active.is_(True))
    ):
        counts[node_id].add(user_id)
    return {node_id: len(members) for node_id, members in counts.items()}


def _sits(person: IsmsPerson) -> bool:
    return person.user is not None and bool(person.user.is_active)


def audience_out(db: Session, quiz: Quiz) -> dict[str, Any]:
    entries = list(quiz.audience)
    node_ids = [entry.org_node_id for entry in entries if entry.org_node_id]
    holders = _role_holder_counts(db, node_ids)
    sitting, accounts = audience_members(db, quiz)
    people = sorted(
        (entry.person for entry in entries if entry.person is not None),
        key=lambda person: person.name.lower(),
    )
    roles = sorted(
        (entry.org_node for entry in entries if entry.org_node is not None),
        key=lambda node: node.name.lower(),
    )
    return {
        "people": [
            {
                "id": str(person.id),
                "name": person.name,
                "email": person.email or "",
                "can_sit": _sits(person),
            }
            for person in people
        ],
        "roles": [
            {"id": str(node.id), "name": node.name, "holders": holders.get(node.id, 0)}
            for node in roles
        ],
        # People who can sit the next round, counting role-holding accounts
        # that a round will give People records.
        "size": len(sitting) + len(accounts),
    }


def audience_size(db: Session, quiz: Quiz) -> int:
    sitting, accounts = audience_members(db, quiz)
    return len(sitting) + len(accounts)


def assignees(*, db: Session, user: User) -> dict[str, Any]:
    """The people, accounts and roles a quiz can be assigned to."""
    require_manage(db, user)
    people = db.query(IsmsPerson).order_by(func.lower(IsmsPerson.name)).all()
    linked = {person.user_id for person in people if person.user_id}
    accounts = (
        db.query(User)
        .filter(User.is_active.is_(True))
        .order_by(func.lower(User.username))
        .all()
    )
    nodes = db.query(IsmsOrgNode).order_by(func.lower(IsmsOrgNode.name)).all()
    holders = _role_holder_counts(db, [node.id for node in nodes])
    return {
        "people": [
            {
                "id": str(person.id),
                "name": person.name,
                "email": person.email or "",
                "position": person.position or "",
                "role": person.org_node.name if person.org_node else "",
                "can_sit": _sits(person),
            }
            for person in people
        ],
        # Accounts without a People record; assigning one creates its record.
        "accounts": [
            {
                "id": str(account.id),
                "username": account.username,
                "email": user_email(account) or "",
            }
            for account in accounts
            if account.id not in linked
        ],
        "roles": [
            {
                "id": str(node.id),
                "name": node.name,
                "node_type": node.node_type or "",
                "holders": holders.get(node.id, 0),
            }
            for node in nodes
        ],
    }


def _known(db: Session, model, ids: list[uuid.UUID], label: str) -> list[uuid.UUID]:
    unique = list(dict.fromkeys(ids))
    if unique:
        found = {
            row_id for (row_id,) in db.query(model.id).filter(model.id.in_(unique))
        }
        if len(found) != len(unique):
            raise DomainError(status_code=400, detail=f"Unknown {label}")
    return unique


def replace_audience(
    *,
    quiz_id: uuid.UUID,
    payload: QuizAudienceInput,
    context: ChangeContext | None,
    db: Session,
    user: User,
) -> dict[str, Any]:
    """Set who a quiz is assigned to, update its open rounds to match and
    invite the people they gain."""
    require_manage(db, user)
    quiz = quiz_or_404(db, quiz_id, lock=True)
    before = quiz_snapshot(quiz)
    person_ids = _known(db, IsmsPerson, payload.person_ids, "person")
    node_ids = _known(db, IsmsOrgNode, payload.org_node_ids, "organisation role")
    account_ids = list(dict.fromkeys(payload.user_ids))
    if account_ids:
        accounts = (
            db.query(User)
            .filter(User.id.in_(account_ids), User.is_active.is_(True))
            .all()
        )
        if len(accounts) != len(account_ids):
            raise DomainError(status_code=400, detail="Unknown or inactive account")
        for account in accounts:
            person_ids.append(ensure_person_for_user(db, account).id)
    db.query(QuizAudience).filter(QuizAudience.quiz_id == quiz.id).delete(
        synchronize_session=False
    )
    for person_id in dict.fromkeys(person_ids):
        db.add(QuizAudience(quiz_id=quiz.id, person_id=person_id))
    for node_id in node_ids:
        db.add(QuizAudience(quiz_id=quiz.id, org_node_id=node_id))
    db.flush()
    db.expire(quiz, ["audience"])
    joined = [
        round_.id
        for round_ in (
            db.query(QuizRound)
            .filter(QuizRound.quiz_id == quiz.id, QuizRound.status == "open")
            .with_for_update()
            .all()
        )
        if sync_participants(db, quiz, round_)
    ]
    record_quiz_change(db, quiz, "updated", before, quiz_snapshot(quiz), user, context)
    db.commit()
    queue_invitations(joined)
    db.refresh(quiz)
    return audience_out(db, quiz)
