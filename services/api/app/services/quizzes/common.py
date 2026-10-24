"""Lookups, links and change records shared by the quiz operations."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Quiz, QuizRound, User
from app.services.context import ChangeContext
from app.services.entity_changelog import record_entity_changelog
from app.services.errors import DomainError
from app.services.schedules import schedule_label

from .access import MIN_COHORT


def quiz_or_404(db: Session, quiz_id: uuid.UUID, *, lock: bool = False) -> Quiz:
    query = db.query(Quiz).filter(Quiz.id == quiz_id)
    quiz = (query.with_for_update() if lock else query).one_or_none()
    if quiz is None:
        raise DomainError(status_code=404, detail="Quiz not found")
    return quiz


def round_or_404(
    db: Session, quiz: Quiz, round_id: uuid.UUID, *, lock: bool = False
) -> QuizRound:
    query = db.query(QuizRound).filter(
        QuizRound.id == round_id, QuizRound.quiz_id == quiz.id
    )
    row = (query.with_for_update() if lock else query).one_or_none()
    if row is None:
        raise DomainError(status_code=404, detail="Round not found")
    return row


def quiz_url(quiz_id: uuid.UUID) -> str:
    """The quiz's page, for the record on its effectiveness measure."""
    return f"{settings.public_base_url}/quiz.html?id={quiz_id}"


def sitting_url(round_id: uuid.UUID) -> str:
    """Where a participant sits a round, for invitations and reminders."""
    return f"{settings.public_base_url}/quiz-take.html?round={round_id}"


def result_ready(status: str, complete: int, pending: int) -> bool:
    """Whether a round's results may be shown beyond quiz.manage.

    The round has closed, every response is marked and enough people
    completed it that the average describes the group, not one person.
    """
    return status == "closed" and pending == 0 and complete >= MIN_COHORT


def iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def schedule_out(quiz: Quiz) -> dict[str, Any]:
    """A quiz's schedule, named as it is sent and as audit schedules are."""
    return {
        "start_date": iso(quiz.start_date),
        "schedule_recurrence": quiz.schedule_recurrence or "once",
        "schedule_interval": int(quiz.schedule_interval or 1),
        "schedule_date_rule": quiz.schedule_date_rule or "exact",
        "schedule_anchor_month": quiz.schedule_anchor_month,
        "schedule_until_date": iso(quiz.schedule_until_date),
        "schedule_next_run_date": iso(quiz.schedule_next_run_date),
        "schedule_last_run_date": iso(quiz.schedule_last_run_date),
        "label": schedule_label(quiz),
    }


def quiz_snapshot(quiz: Quiz) -> dict[str, Any]:
    """A quiz's state for its change history.

    The answer key stays out: the history is open to audit trail readers, who
    may also sit the quiz.
    """
    return {
        "title": quiz.title,
        "description": quiz.description or "",
        "framework": quiz.framework_slug,
        "status": quiz.status,
        "pass_mark": quiz.pass_mark,
        "questions_per_attempt": quiz.questions_per_attempt,
        "page_size": quiz.page_size,
        "show_scores": bool(quiz.show_scores),
        "reveal_answers": quiz.reveal_answers,
        "due_days": quiz.due_days,
        "start_date": iso(quiz.start_date),
        "schedule": schedule_label(quiz),
        "questions": [
            {
                "kind": question.kind,
                "prompt": question.prompt,
                # Alphabetical, so the history gives away no answer by order.
                "options": sorted(
                    (option.text for option in question.options), key=str.casefold
                ),
            }
            for question in quiz.questions
        ],
        "people": sorted(
            entry.person.name for entry in quiz.audience if entry.person is not None
        ),
        "roles": sorted(
            entry.org_node.name for entry in quiz.audience if entry.org_node is not None
        ),
    }


def record_quiz_change(
    db: Session,
    quiz: Quiz,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    user: User | None,
    context: ChangeContext | None,
) -> None:
    record_entity_changelog(
        db,
        entity_type="quiz",
        entity_id=quiz.id,
        action=action,
        before=before,
        after=after,
        user=user,
        request_method=context.method if context else None,
        request_path=context.path if context else None,
    )
