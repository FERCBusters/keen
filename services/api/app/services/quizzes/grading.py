"""Grading written answers.

quiz.manage grades each written answer from 1 to 5 in half points. The
grading list leaves out names, so answers are graded on their merits, and
leaves out the grader's own answers, which another manager grades. An attempt
completes with its score once its last written answer is graded.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive
from app.db.models import QuizAnswer, QuizAttempt, QuizRound, User
from app.services.assurance import person_for_user
from app.services.errors import DomainError
from app.services.text import clean_text

from .access import require_manage
from .common import iso, quiz_or_404
from .measure import record_round_result
from .schemas import QuizGradeInput
from .scoring import settle, shown_score, text_points, valid_grade


def _own_attempt(db: Session, user: User, attempt: QuizAttempt) -> bool:
    if attempt.user_id == user.id:
        return True
    person = person_for_user(db, user.id)
    return person is not None and attempt.person_id == person.id


def grading_queue(*, quiz_id: uuid.UUID, db: Session, user: User) -> dict[str, Any]:
    """Written answers waiting for a grade, oldest first, without names."""
    require_manage(db, user)
    quiz = quiz_or_404(db, quiz_id)
    rows = (
        db.query(QuizAnswer, QuizAttempt, QuizRound)
        .join(QuizAttempt, QuizAttempt.id == QuizAnswer.attempt_id)
        .join(QuizRound, QuizRound.id == QuizAttempt.round_id)
        .filter(
            QuizAttempt.quiz_id == quiz.id,
            QuizAttempt.status == "submitted",
            QuizAnswer.kind == "text",
            QuizAnswer.points.is_(None),
        )
        .order_by(QuizAttempt.submitted_at.asc(), QuizAnswer.position.asc())
        .all()
    )
    items = []
    own = 0
    for answer, attempt, round_ in rows:
        if _own_attempt(db, user, attempt):
            own += 1
            continue
        item = attempt.layout[answer.position]
        items.append(
            {
                "answer_id": str(answer.id),
                "attempt_id": str(attempt.id),
                "round_id": str(round_.id),
                "round_opens_on": iso(round_.opens_on),
                "prompt": item["prompt"],
                # The question's explanation guides the grade.
                "guidance": item.get("explanation") or "",
                "text": answer.text_answer,
                "submitted_at": iso(attempt.submitted_at),
            }
        )
    return {"items": items, "own_answers_waiting": own}


def grade_answer(
    *,
    quiz_id: uuid.UUID,
    answer_id: uuid.UUID,
    payload: QuizGradeInput,
    db: Session,
    user: User,
) -> dict[str, Any]:
    require_manage(db, user)
    quiz = quiz_or_404(db, quiz_id)
    attempt = (
        db.query(QuizAttempt)
        .join(QuizAnswer, QuizAnswer.attempt_id == QuizAttempt.id)
        .filter(QuizAnswer.id == answer_id, QuizAttempt.quiz_id == quiz.id)
        .with_for_update(of=QuizAttempt)
        .one_or_none()
    )
    if attempt is None:
        raise DomainError(status_code=404, detail="Answer not found")
    # Read under the attempt's lock, so concurrent grades apply in turn.
    answer = db.query(QuizAnswer).filter(QuizAnswer.id == answer_id).one()
    if attempt.status == "in_progress":
        raise DomainError(status_code=409, detail="This answer is not handed in yet")
    if answer.kind != "text":
        raise DomainError(status_code=400, detail="Only written answers are graded")
    if not (answer.text_answer or "").strip():
        raise DomainError(
            status_code=400, detail="A blank answer scores nothing and takes no grade"
        )
    if _own_attempt(db, user, attempt):
        raise DomainError(
            status_code=403, detail="Another quiz manager grades your own answers"
        )
    if not valid_grade(payload.grade):
        raise DomainError(
            status_code=400, detail="Grades run from 1 to 5 in half points"
        )
    now = utc_now_naive()
    answer.grade = float(payload.grade)
    answer.points = text_points(answer.grade)
    answer.feedback = clean_text(payload.feedback, max_len=4000, label="feedback")
    answer.graded_by_user_id = user.id
    answer.graded_at = now
    settle(attempt, now)
    round_ = (
        db.query(QuizRound)
        .filter(QuizRound.id == attempt.round_id)
        .with_for_update()
        .one()
    )
    if round_.status == "closed":
        record_round_result(db, quiz, round_)
    db.commit()
    return {
        "answer": {
            "id": str(answer.id),
            "grade": answer.grade,
            "points": answer.points,
            "feedback": answer.feedback,
            "graded_at": iso(answer.graded_at),
        },
        "attempt": {
            "id": str(attempt.id),
            "status": attempt.status,
            "score": shown_score(attempt.score),
        },
    }
