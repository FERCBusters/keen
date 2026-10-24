"""Quiz rounds: starting, closing and scheduling them, and reminders.

A round opens for the quiz's audience and falls due on a date. quiz.manage
starts rounds by hand; an active quiz's schedule opens one on each run date,
due the quiz's number of days later. A round closes on its due date, and its
average is then recorded on the quiz's effectiveness measure. Each
participant is told when a round opens and reminded once shortly before it
falls due while they have not handed in their attempt.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from functools import partial
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive
from app.db.models import Quiz, QuizAttempt, QuizParticipant, QuizRound, User
from app.services.errors import DomainError
from app.services.features import state
from app.services.mailer import send_quiz_reminder_email
from app.services.schedules import (
    advance_past_missed_runs,
    deliver_once,
    materialise_due,
    materialise_one,
    reminder_window,
    today_for_schedule,
)

from .access import require_manage
from .audience import (
    invite_rounds,
    person_email,
    queue_invitations,
    round_invite_uid,
    sync_participants,
)
from .common import iso, quiz_or_404, round_or_404, sitting_url
from .measure import record_round_result, round_result_recorded
from .results import round_out, round_stats
from .schemas import QuizRoundInput, QuizRoundUpdate


def _round_view(db: Session, quiz: Quiz, round_: QuizRound) -> dict[str, Any]:
    return round_out(quiz, round_, round_stats(db, [round_])[round_.id], manager=True)


def open_round(
    db: Session,
    quiz: Quiz,
    *,
    opens_on: date,
    due_on: date,
    scheduled: bool,
    user: User | None,
) -> QuizRound:
    """Create a round for the quiz's audience."""
    round_ = QuizRound(
        quiz_id=quiz.id,
        opens_on=opens_on,
        due_on=due_on,
        status="open",
        scheduled=scheduled,
        created_by_user_id=user.id if user else None,
    )
    db.add(round_)
    db.flush()
    sync_participants(db, quiz, round_)
    return round_


def quiz_availability(quiz: Quiz, rounds, stats) -> dict[str, Any]:
    """Whether people can take the quiz now, and if not, when they next can.

    ``state`` is ``open`` (with ``until`` and ``people``), ``waiting`` (with
    ``opens_on``), ``no_round``, ``archived`` or ``draft``. A draft says what
    activating does in ``on_activation``: ``now`` (with ``until``),
    ``scheduled`` (with ``opens_on``) or ``nothing`` when its schedule has no
    rounds left.
    """
    today = today_for_schedule()
    if quiz.status == "archived":
        return {"state": "archived"}
    scheduled = quiz.schedule_next_run_date is not None
    next_date, _ = advance_past_missed_runs(quiz, run_today=today)
    if quiz.status == "draft":
        if scheduled and next_date is None:
            return {"state": "draft", "on_activation": "nothing"}
        if scheduled and next_date > today:
            return {
                "state": "draft",
                "on_activation": "scheduled",
                "opens_on": iso(next_date),
            }
        until = today + timedelta(days=int(quiz.due_days or 1))
        return {"state": "draft", "on_activation": "now", "until": iso(until)}
    sittable = [
        r for r in rounds if r.status == "open" and r.opens_on <= today <= r.due_on
    ]
    if sittable:
        latest = max(sittable, key=lambda r: (r.due_on, r.created_at))
        return {
            "state": "open",
            "until": iso(latest.due_on),
            "people": stats[latest.id].participants,
        }
    dates = [r.opens_on for r in rounds if r.status == "open" and r.opens_on > today]
    if next_date is not None:
        dates.append(next_date)
    if dates:
        return {"state": "waiting", "opens_on": iso(min(dates))}
    return {"state": "no_round"}


def open_due_round(
    db: Session, quiz: Quiz, *, user: User | None, activating: bool
) -> QuizRound | None:
    """Open the round an active quiz is due now, if any.

    A scheduled quiz opens its round for today, so a schedule saved or a quiz
    activated after the day's run still opens on time. A quiz without a
    schedule is one-off: activating it opens its round straight away.
    """
    today = today_for_schedule()
    if quiz.schedule_next_run_date is not None:
        round_, _ = materialise_one(
            quiz,
            run_today=today,
            latest_create_date=today,
            create_run=_scheduled_round(db),
        )
        return round_
    open_now = (
        db.query(QuizRound.id)
        .filter(QuizRound.quiz_id == quiz.id, QuizRound.status == "open")
        .first()
    )
    if not activating or open_now is not None:
        return None
    return open_round(
        db,
        quiz,
        opens_on=today,
        due_on=today + timedelta(days=int(quiz.due_days or 1)),
        scheduled=False,
        user=user,
    )


def close_round(db: Session, quiz: Quiz, round_: QuizRound) -> None:
    round_.status = "closed"
    round_.closed_at = utc_now_naive()
    record_round_result(db, quiz, round_)


def start_round(
    *, quiz_id: uuid.UUID, payload: QuizRoundInput, db: Session, user: User
) -> dict[str, Any]:
    require_manage(db, user)
    quiz = quiz_or_404(db, quiz_id, lock=True)
    if quiz.status != "active":
        raise DomainError(
            status_code=409, detail="Activate the quiz before starting a round"
        )
    today = today_for_schedule()
    opens_on = payload.opens_on or today
    due_on = payload.due_on or opens_on + timedelta(days=int(quiz.due_days or 1))
    if opens_on < today:
        raise DomainError(status_code=400, detail="opens_on cannot be in the past")
    if due_on < opens_on:
        raise DomainError(status_code=400, detail="due_on cannot be before opens_on")
    round_ = open_round(
        db,
        quiz,
        opens_on=opens_on,
        due_on=due_on,
        scheduled=False,
        user=user,
    )
    db.commit()
    queue_invitations([round_.id])
    return _round_view(db, quiz, round_)


def update_round(
    *,
    quiz_id: uuid.UUID,
    round_id: uuid.UUID,
    payload: QuizRoundUpdate,
    db: Session,
    user: User,
) -> dict[str, Any]:
    """Move a round's due date, close it early or reopen it."""
    require_manage(db, user)
    quiz = quiz_or_404(db, quiz_id, lock=True)
    round_ = round_or_404(db, quiz, round_id, lock=True)
    fields = payload.model_fields_set
    today = today_for_schedule()
    status = payload.status if "status" in fields else round_.status
    if status is None:
        raise DomainError(status_code=400, detail="status is required")
    if "due_on" in fields:
        if payload.due_on is None:
            raise DomainError(status_code=400, detail="due_on is required")
        if payload.due_on < round_.opens_on:
            raise DomainError(
                status_code=400, detail="due_on cannot be before opens_on"
            )
        if payload.due_on != round_.due_on:
            round_.due_on = payload.due_on
            # Remind people again before the new due date.
            db.query(QuizParticipant).filter(
                QuizParticipant.round_id == round_.id
            ).update({QuizParticipant.reminded_at: None}, synchronize_session=False)
    if status == "open" and round_.due_on < today:
        raise DomainError(
            status_code=400, detail="An open round needs a due date from today"
        )
    if status == "open" and quiz.status != "active":
        raise DomainError(
            status_code=409, detail="Activate the quiz before reopening a round"
        )
    if (
        status == "open"
        and round_.status == "closed"
        and round_result_recorded(db, quiz, round_)
    ):
        # Comparing the result before and after a latecomer would give away
        # the latecomer's score.
        raise DomainError(
            status_code=409,
            detail="This round's result is recorded; start a new round for anyone who missed it",
        )
    if status != round_.status:
        if status == "closed":
            close_round(db, quiz, round_)
        else:
            round_.status = "open"
            round_.closed_at = None
    db.commit()
    return _round_view(db, quiz, round_)


def delete_round(
    *, quiz_id: uuid.UUID, round_id: uuid.UUID, db: Session, user: User
) -> dict[str, Any]:
    require_manage(db, user)
    quiz = quiz_or_404(db, quiz_id, lock=True)
    round_ = round_or_404(db, quiz, round_id, lock=True)
    if db.query(QuizAttempt.id).filter(QuizAttempt.round_id == round_.id).first():
        raise DomainError(
            status_code=409,
            detail="People have started this round; close it instead",
        )
    db.delete(round_)
    db.commit()
    return {"ok": True}


def _quizzes_enabled(db: Session) -> bool:
    return state(db, refresh=True).enabled("quizzes")


def close_due_quiz_rounds(db: Session, *, today: date | None = None) -> dict:
    """Close every open round whose due date has passed."""
    if not _quizzes_enabled(db):
        return {"enabled": False, "closed": []}
    run_today = today or today_for_schedule()
    due = [
        round_id
        for (round_id,) in db.query(QuizRound.id).filter(
            QuizRound.status == "open", QuizRound.due_on < run_today
        )
    ]
    closed = []
    for round_id in due:
        round_ = (
            db.query(QuizRound)
            .filter(
                QuizRound.id == round_id,
                QuizRound.status == "open",
                QuizRound.due_on < run_today,
            )
            .with_for_update()
            .one_or_none()
        )
        if round_ is not None:
            close_round(db, round_.quiz, round_)
            closed.append(str(round_id))
        db.commit()
    return {"enabled": True, "closed": closed}


def _scheduled_round(db: Session):
    def create(quiz: Quiz, run_date: date) -> QuizRound:
        return open_round(
            db,
            quiz,
            opens_on=run_date,
            due_on=run_date + timedelta(days=int(quiz.due_days or 1)),
            scheduled=True,
            user=None,
        )

    return create


def materialize_due_quiz_rounds(db: Session, *, today: date | None = None) -> dict:
    """Open the rounds active quizzes' schedules have due today."""
    if not _quizzes_enabled(db):
        return {"enabled": False, "created": [], "skipped_past": []}
    run_today = today or today_for_schedule()
    checked, created, skipped = materialise_due(
        db,
        model=Quiz,
        filters=[Quiz.status == "active"],
        run_today=run_today,
        latest_create_date=run_today,
        create_run=_scheduled_round(db),
        after_commit=lambda quiz_id, round_: {
            "quiz_id": str(quiz_id),
            "round_id": str(round_.id),
            "opens_on": round_.opens_on.isoformat(),
            "due_on": round_.due_on.isoformat(),
            "invited": invite_rounds(db, [round_.id]),
        },
    )
    return {
        "enabled": True,
        "checked": checked,
        "created": created,
        "skipped_past": [
            {"quiz_id": str(quiz_id), **record} for quiz_id, record in skipped
        ],
    }


def send_quiz_invitations(db: Session, round_ids: list[uuid.UUID]) -> dict:
    """Invite the people of rounds a quiz manager has just started or added to."""
    if not _quizzes_enabled(db):
        return {"enabled": False, "invited": 0}
    return {"enabled": True, "invited": invite_rounds(db, round_ids)}


def invite_waiting(db: Session, *, today: date | None = None) -> dict:
    """Invite anyone an open round has not reached yet, such as after a failed
    delivery or a queue that was down."""
    day = today or today_for_schedule()
    round_ids = [
        round_id
        for (round_id,) in db.query(QuizRound.id)
        .join(QuizParticipant, QuizParticipant.round_id == QuizRound.id)
        .filter(
            QuizRound.status == "open",
            QuizRound.due_on >= day,
            QuizParticipant.invited_at.is_(None),
        )
        .distinct()
    ]
    return send_quiz_invitations(db, round_ids)


def send_quiz_reminders(db: Session, *, today: date | None = None) -> dict:
    """Remind each participant once before a round falls due, unless they
    have handed in their attempt."""
    window = reminder_window(today)
    if window is None or not _quizzes_enabled(db):
        return {"enabled": False, "sent": 0}
    first, last = window
    ids = [
        participant_id
        for (participant_id,) in db.query(QuizParticipant.id)
        .join(QuizRound, QuizRound.id == QuizParticipant.round_id)
        .outerjoin(
            QuizAttempt,
            and_(
                QuizAttempt.round_id == QuizParticipant.round_id,
                QuizAttempt.person_id == QuizParticipant.person_id,
            ),
        )
        .filter(
            QuizRound.status == "open",
            QuizRound.opens_on <= first,
            QuizRound.due_on >= first,
            QuizRound.due_on <= last,
            QuizParticipant.reminded_at.is_(None),
            (QuizAttempt.id.is_(None)) | (QuizAttempt.status == "in_progress"),
        )
        .order_by(QuizRound.due_on.asc())
        .all()
    ]

    def compose(participant: QuizParticipant):
        email = person_email(participant.person)
        if not email:
            return False
        round_ = participant.round
        return partial(
            send_quiz_reminder_email,
            to_email=email,
            person_name=participant.person.name,
            quiz_title=round_.quiz.title,
            due_on=round_.due_on.isoformat(),
            quiz_url=sitting_url(round_.id),
            uid=round_invite_uid(round_.id),
        )

    return {
        "enabled": True,
        "sent": deliver_once(
            db, model=QuizParticipant, marker="reminded_at", ids=ids, compose=compose
        ),
        "window_start": first.isoformat(),
        "window_end": last.isoformat(),
    }
