"""The effectiveness measure each quiz owns, and the results recorded on it.

The measure carries the quiz's controls and its pass mark as the target. When
a round closes with every response marked and at least three completed, the
round's average score is recorded on the measure, so the controls show the
group's result and never one person's.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    Quiz,
    QuizAttempt,
    QuizParticipant,
    QuizRound,
    User,
)
from app.services.context import ChangeContext
from app.services.isms.effectiveness import (
    add_effectiveness_measure,
    add_effectiveness_result,
    remove_effectiveness_measure,
    revise_effectiveness_measure,
    revise_effectiveness_result,
)
from app.services.isms.schemas import (
    EffectivenessMeasurePayload,
    EffectivenessMetricEntryPayload,
)
from app.services.schedules import schedule_label

from .common import quiz_url, result_ready
from .scoring import passed

DESCRIPTION = (
    "The average score of each round of this quiz, recorded when the round "
    "closes with every response marked and at least three people finished."
)


def _payload(
    quiz: Quiz, controls: list[str] | None, **extra: Any
) -> EffectivenessMeasurePayload:
    fields: dict[str, Any] = {
        **extra,
        "summary": f"Quiz: {quiz.title}",
        "description": DESCRIPTION,
        "source_type": "other",
        "target_value": float(quiz.pass_mark) if quiz.pass_mark is not None else None,
        "target_unit": "%",
        "threshold_operator": "gte" if quiz.pass_mark is not None else "",
        "frequency": schedule_label(quiz) or "Each round",
    }
    if controls is not None:
        fields["controls"] = controls
    return EffectivenessMeasurePayload(**fields)


def create_measure(
    db: Session,
    quiz: Quiz,
    *,
    controls: list[str] | None,
    user: User,
    context: ChangeContext | None,
) -> None:
    """Create a new quiz's measure, before the quiz itself is saved."""
    quiz.measure = add_effectiveness_measure(
        payload=_payload(quiz, controls or [], owner_user_id=user.id),
        context=context,
        framework=quiz.framework_slug,
        user=user,
        db=db,
    )


def sync_measure(
    db: Session,
    quiz: Quiz,
    *,
    controls: list[str] | None,
    user: User,
    context: ChangeContext | None,
) -> None:
    """Carry the quiz's title, pass mark, schedule and controls to its measure."""
    revise_effectiveness_measure(
        row=quiz.measure,
        payload=_payload(quiz, controls),
        context=context,
        user=user,
        db=db,
    )


def remove_measure(
    db: Session,
    measure: IsmsEffectivenessMeasure,
    *,
    user: User,
    context: ChangeContext | None,
) -> None:
    """Delete a deleted quiz's measure with its links and results."""
    remove_effectiveness_measure(row=measure, context=context, user=user, db=db)


def _reference(round_: QuizRound) -> str:
    return f"quiz-round:{round_.id}"


def _notes(quiz: Quiz, scores: list[float], assigned: int) -> str:
    finished = len(scores)
    notes = (
        f"Average of {finished} completed responses from {assigned} assigned people."
    )
    if quiz.pass_mark is not None:
        reached = sum(1 for score in scores if passed(score, quiz.pass_mark))
        notes += f" {reached} reached the {quiz.pass_mark}% pass mark."
    return notes


def _entry(db: Session, quiz: Quiz, round_: QuizRound):
    return (
        db.query(IsmsEffectivenessMetricEntry)
        .filter(
            IsmsEffectivenessMetricEntry.measure_id == quiz.measure_id,
            IsmsEffectivenessMetricEntry.source_reference == _reference(round_),
        )
        .one_or_none()
    )


def round_result_recorded(db: Session, quiz: Quiz, round_: QuizRound) -> bool:
    """Whether the round's average is on the quiz's measure."""
    return _entry(db, quiz, round_) is not None


def record_round_result(db: Session, quiz: Quiz, round_: QuizRound) -> None:
    """Record or update a round's average on the quiz's measure once its result
    is ready.

    Runs in the caller's transaction, which holds the round's lock, whenever
    a round closes or a grade changes in a closed round. A recorded round stays
    closed with its responses, so its result is only ever revised.
    """
    db.flush()
    entry = _entry(db, quiz, round_)
    statuses = (
        db.query(QuizAttempt.status, QuizAttempt.score)
        .filter(QuizAttempt.round_id == round_.id)
        .all()
    )
    scores = [float(score) for status, score in statuses if status == "complete"]
    pending = sum(1 for status, _ in statuses if status == "submitted")
    if not result_ready(round_.status, len(scores), pending):
        return
    assigned = (
        db.query(QuizParticipant.id)
        .filter(QuizParticipant.round_id == round_.id)
        .count()
    )
    payload = EffectivenessMetricEntryPayload(
        recorded_at=round_.closed_at or utc_now_naive(),
        period_start=round_.opens_on,
        period_end=round_.due_on,
        metric_value=round(sum(scores) / len(scores), 2),
        metric_unit="%",
        source_type="other",
        source_title=quiz.title,
        source_url=quiz_url(quiz.id),
        source_reference=_reference(round_),
        notes=_notes(quiz, scores, max(assigned, len(scores))),
    )
    if entry is None:
        add_effectiveness_result(
            measure=quiz.measure, payload=payload, context=None, user=None, db=db
        )
    else:
        revise_effectiveness_result(
            row=entry, payload=payload, context=None, user=None, db=db
        )
