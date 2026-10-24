"""Sitting quizzes: a person's rounds, their attempts and their progress.

A person sits the rounds they are a participant of, as the People record
linked to their account. An attempt keeps the questions in the order the
person sees them. Answers are saved a page at a time as the person moves on,
so they can leave and carry on later. Nothing the person receives while
sitting says which answers are right.

Once handed in, a person reviews their answers beside the right ones only as
the quiz allows, while they have no other round of the quiz left to sit and
until a later round opens, so answers never carry from one round into another.
Their score stays visible. An attempt continues only from the account that
started it.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizParticipant,
    QuizRound,
    User,
)
from app.services.assurance import person_for_user
from app.services.errors import DomainError
from app.services.schedules import (
    occurrences_between,
    schedule_date,
    today_for_schedule,
)
from app.services.text import clean_text

from .access import own_person
from .audience import audience_members
from .common import iso
from .progress import Sitting, progress, sitting_points
from .schemas import QuizAnswerInput, QuizPageInput, QuizSubmitInput
from .scoring import (
    build_layout,
    mark,
    page_count,
    page_positions,
    passed,
    settle,
    shown_score,
    trophy,
)

# How far ahead a person's list shows rounds their quizzes will open.
UPCOMING_DAYS = 90


def score_visible(quiz: Quiz, attempt: QuizAttempt) -> bool:
    return bool(quiz.show_scores) and attempt.status == "complete"


def later_round_opened(db: Session, round_: QuizRound, today: date) -> bool:
    """Whether a round of the same quiz has opened since ``round_`` did."""
    later = or_(
        QuizRound.opens_on > round_.opens_on,
        and_(
            QuizRound.opens_on == round_.opens_on,
            QuizRound.created_at > round_.created_at,
        ),
    )
    return (
        db.query(QuizRound.id)
        .filter(
            QuizRound.quiz_id == round_.quiz_id,
            QuizRound.id != round_.id,
            QuizRound.opens_on <= today,
            later,
        )
        .first()
        is not None
    )


def round_left_to_sit(
    db: Session, round_: QuizRound, person_id: uuid.UUID, today: date
) -> bool:
    """Whether the person can still sit another round of the same quiz."""
    handed_in = (
        db.query(QuizAttempt.id)
        .filter(
            QuizAttempt.round_id == QuizRound.id,
            QuizAttempt.person_id == person_id,
            QuizAttempt.status != "in_progress",
        )
        .exists()
    )
    return (
        db.query(QuizRound.id)
        .join(QuizParticipant, QuizParticipant.round_id == QuizRound.id)
        .filter(
            QuizRound.quiz_id == round_.quiz_id,
            QuizRound.id != round_.id,
            QuizRound.status == "open",
            QuizRound.opens_on <= today,
            QuizRound.due_on >= today,
            QuizParticipant.person_id == person_id,
            ~handed_in,
        )
        .first()
        is not None
    )


def review_state(
    db: Session, quiz: Quiz, round_: QuizRound, attempt: QuizAttempt
) -> str | None:
    """Whether the person may review a handed-in attempt, or why not.

    ``shown``: their answers beside the right ones. ``at_close``: once the
    round closes. ``never``: the quiz keeps its answers. ``superseded``: a later
    round has opened. ``other_round``: they still have another round of the
    quiz to sit. ``scores_hidden``: answers would give the score away.
    """
    if attempt.status == "in_progress":
        return None
    if not quiz.show_scores:
        return "scores_hidden"
    if quiz.reveal_answers == "never":
        return "never"
    today = today_for_schedule()
    if later_round_opened(db, round_, today):
        return "superseded"
    if quiz.reveal_answers == "after_close" and round_.status != "closed":
        return "at_close"
    if attempt.person_id and round_left_to_sit(db, round_, attempt.person_id, today):
        return "other_round"
    return "shown"


def _finished_on(attempt: QuizAttempt | None):
    if attempt is None or attempt.submitted_at is None:
        return None
    return schedule_date(attempt.submitted_at)


def _sitting(quiz: Quiz, round_: QuizRound, attempt: QuizAttempt | None) -> Sitting:
    shown = attempt is not None and score_visible(quiz, attempt)
    return Sitting(
        opens_on=round_.opens_on,
        due_on=round_.due_on,
        closed=round_.status == "closed",
        finished_on=_finished_on(attempt),
        trophy=trophy(attempt.score, quiz.pass_mark) if shown else None,
        perfect=bool(shown and attempt.score >= 100),
    )


def attempt_view(
    quiz: Quiz,
    round_: QuizRound,
    attempt: QuizAttempt,
    *,
    review: str | None,
    show_score: bool,
) -> dict[str, Any]:
    """An attempt as its reader may see it.

    While the attempt is in progress, it carries the questions and the saved
    answers. Once handed in, it carries them only while ``review`` is
    ``shown``, with the right answers, marks and feedback, and the score when
    ``show_score`` allows.
    """
    layout = attempt.layout or []
    reveal = review == "shown"
    shown = layout if reveal or attempt.status == "in_progress" else []
    answers = {answer.position: answer for answer in attempt.answers}
    questions = []
    for position, item in enumerate(shown):
        answer = answers.get(position)
        question: dict[str, Any] = {
            "position": position,
            "kind": item["kind"],
            "prompt": item["prompt"],
            "options": [
                {"id": option["id"], "text": option["text"]}
                | ({"correct": bool(option.get("correct"))} if reveal else {})
                for option in item.get("options") or []
            ],
            "answer": {
                "option_id": str(answer.option_id)
                if answer and answer.option_id
                else None,
                "text": answer.text_answer if answer else "",
            },
        }
        if reveal and answer is not None:
            question |= {
                "explanation": item.get("explanation") or "",
                "correct": answer.is_correct,
                "points": answer.points,
                "grade": answer.grade,
                "feedback": answer.feedback or "",
                "pending": answer.points is None,
            }
        questions.append(question)
    out = {
        "id": str(attempt.id),
        "quiz_id": str(quiz.id),
        "round_id": str(round_.id),
        "title": quiz.title,
        "description": quiz.description or "",
        "status": attempt.status,
        "round_status": round_.status,
        "opens_on": iso(round_.opens_on),
        "due_on": iso(round_.due_on),
        "question_count": len(layout),
        "page_size": attempt.page_size,
        "page_count": page_count(len(layout), attempt.page_size),
        "pages_saved": attempt.pages_saved,
        "started_at": iso(attempt.started_at),
        "saved_at": iso(attempt.saved_at),
        "submitted_at": iso(attempt.submitted_at),
        "completed_at": iso(attempt.completed_at),
        "score_visible": show_score,
        "review": review,
        "questions": questions,
    }
    if show_score:
        out |= {
            "score": shown_score(attempt.score),
            "pass_mark": quiz.pass_mark,
            "passed": passed(attempt.score, quiz.pass_mark),
            "trophy": trophy(attempt.score, quiz.pass_mark),
        }
    return out


def _student_view(
    db: Session, quiz: Quiz, round_: QuizRound, attempt: QuizAttempt
) -> dict:
    out = attempt_view(
        quiz,
        round_,
        attempt,
        review=review_state(db, quiz, round_, attempt),
        show_score=score_visible(quiz, attempt),
    )
    if attempt.status != "in_progress":
        out["reward"] = {"points": sitting_points(_sitting(quiz, round_, attempt))}
    return out


def _state(round_: QuizRound, attempt: QuizAttempt | None, today) -> str:
    if attempt is not None and attempt.status == "complete":
        return "complete"
    if attempt is not None and attempt.status == "submitted":
        return "awaiting_grading"
    if round_.status == "closed" or today > round_.due_on:
        return "missed"
    if today < round_.opens_on:
        return "upcoming"
    return "in_progress" if attempt is not None else "available"


_TO_SIT = ("in_progress", "available", "upcoming")


def _item(quiz: Quiz, round_: QuizRound, attempt: QuizAttempt | None, today) -> dict:
    state = _state(round_, attempt, today)
    item: dict[str, Any] = {
        "round_id": str(round_.id),
        "quiz_id": str(quiz.id),
        "title": quiz.title,
        "description": quiz.description or "",
        "opens_on": iso(round_.opens_on),
        "due_on": iso(round_.due_on),
        "round_status": round_.status,
        "state": state,
        "days_left": (round_.due_on - today).days
        if state in {"available", "in_progress"}
        else None,
        "attempt": None,
        "result": None,
    }
    if attempt is not None:
        layout = attempt.layout or []
        item["attempt"] = {
            "id": str(attempt.id),
            "status": attempt.status,
            "question_count": len(layout),
            "page_count": page_count(len(layout), attempt.page_size),
            "pages_saved": attempt.pages_saved,
            "submitted_at": iso(attempt.submitted_at),
        }
        if score_visible(quiz, attempt):
            item["result"] = {
                "score": shown_score(attempt.score),
                "pass_mark": quiz.pass_mark,
                "passed": passed(attempt.score, quiz.pass_mark),
                "trophy": trophy(attempt.score, quiz.pass_mark),
            }
    return item


def _upcoming(db: Session, person_id: uuid.UUID, user: User, today) -> list[dict]:
    """Rounds the person's scheduled quizzes will open in the coming weeks."""
    out = []
    quizzes = (
        db.query(Quiz)
        .filter(Quiz.status == "active", Quiz.schedule_next_run_date.isnot(None))
        .all()
    )
    for quiz in quizzes:
        sitting, accounts = audience_members(db, quiz)
        if person_id not in sitting and user.id not in accounts:
            continue
        for day in occurrences_between(
            quiz, start=today, end=today + timedelta(days=UPCOMING_DAYS), limit=3
        ):
            out.append({"quiz_id": str(quiz.id), "title": quiz.title, "date": iso(day)})
    return sorted(out, key=lambda entry: (entry["date"], entry["title"]))


def my_quizzes(*, db: Session, user: User) -> dict[str, Any]:
    """The rounds assigned to the signed-in person, their results and progress."""
    today = today_for_schedule()
    person = person_for_user(db, user.id)
    if person is None:
        return {
            "linked": False,
            "person": None,
            "items": [],
            "upcoming": [],
            "progress": progress([]),
        }
    rows = (
        db.query(QuizRound, Quiz)
        .join(QuizParticipant, QuizParticipant.round_id == QuizRound.id)
        .join(Quiz, Quiz.id == QuizRound.quiz_id)
        .filter(QuizParticipant.person_id == person.id)
        .all()
    )
    attempts = {
        attempt.round_id: attempt
        for attempt in db.query(QuizAttempt).filter(QuizAttempt.person_id == person.id)
    }
    items = []
    sittings = []
    for round_, quiz in rows:
        attempt = attempts.get(round_.id)
        items.append(_item(quiz, round_, attempt, today))
        sittings.append(_sitting(quiz, round_, attempt))
    # Rounds to sit come first, soonest due; finished and missed rounds
    # follow, most recent first.
    to_sit = sorted(
        (item for item in items if item["state"] in _TO_SIT),
        key=lambda item: (_TO_SIT.index(item["state"]), item["due_on"]),
    )
    settled = sorted(
        (item for item in items if item["state"] not in _TO_SIT),
        key=lambda item: item["due_on"],
        reverse=True,
    )
    return {
        "linked": True,
        "person": {"id": str(person.id), "name": person.name},
        "items": to_sit + settled,
        "upcoming": _upcoming(db, person.id, user, today),
        "progress": progress(sittings),
    }


def _shared_round(db: Session, round_id: uuid.UUID):
    """Query a round under a shared lock: answers go in while it is open,
    and a round closing waits for them, so its average counts them."""
    return (
        db.query(QuizRound)
        .filter(QuizRound.id == round_id)
        .with_for_update(read=True, of=QuizRound)
        .populate_existing()
    )


def _own_round(db: Session, person_id: uuid.UUID, round_id: uuid.UUID) -> QuizRound:
    round_ = (
        _shared_round(db, round_id)
        .join(QuizParticipant, QuizParticipant.round_id == QuizRound.id)
        .filter(QuizParticipant.person_id == person_id)
        .one_or_none()
    )
    if round_ is None:
        raise DomainError(status_code=404, detail="Quiz round not found")
    return round_


def _existing_attempt(
    db: Session, round_id: uuid.UUID, person_id: uuid.UUID
) -> QuizAttempt | None:
    return (
        db.query(QuizAttempt)
        .filter(QuizAttempt.round_id == round_id, QuizAttempt.person_id == person_id)
        .one_or_none()
    )


def _require_open(round_: QuizRound, today) -> None:
    if round_.status != "open" or today > round_.due_on:
        raise DomainError(status_code=409, detail="This quiz round has closed")
    if today < round_.opens_on:
        raise DomainError(
            status_code=409,
            detail=f"This quiz round opens on {round_.opens_on.isoformat()}",
        )


def _seen_questions(db: Session, quiz: Quiz, person_id: uuid.UUID) -> set[str]:
    seen: set[str] = set()
    for (layout,) in db.query(QuizAttempt.layout).filter(
        QuizAttempt.quiz_id == quiz.id, QuizAttempt.person_id == person_id
    ):
        seen.update(str(item.get("question_id")) for item in layout or [])
    return seen


def start_attempt(*, round_id: uuid.UUID, db: Session, user: User) -> dict[str, Any]:
    """Start the person's attempt at a round, or return the one they have."""
    person = own_person(db, user)
    round_ = _own_round(db, person.id, round_id)
    quiz = round_.quiz
    attempt = _existing_attempt(db, round_.id, person.id)
    if attempt is not None:
        if attempt.status == "in_progress":
            _require_starter(attempt, user)
        return _student_view(db, quiz, round_, attempt)
    # An open round belongs to an active quiz, which has questions.
    _require_open(round_, today_for_schedule())
    layout = build_layout(
        quiz.questions,
        count=quiz.questions_per_attempt,
        seen=_seen_questions(db, quiz, person.id),
    )
    attempt = QuizAttempt(
        quiz_id=quiz.id,
        round_id=round_.id,
        person_id=person.id,
        user_id=user.id,
        status="in_progress",
        layout=layout,
        page_size=quiz.page_size,
        pages_saved=0,
        started_at=utc_now_naive(),
    )
    # Every question starts unanswered, linked to its question while it exists.
    attempt.answers = [
        QuizAnswer(
            position=position,
            question_id=uuid.UUID(item["question_id"]),
            kind=item["kind"],
        )
        for position, item in enumerate(layout)
    ]
    try:
        with db.begin_nested():
            db.add(attempt)
            db.flush()
    except IntegrityError:
        # Another request started the same attempt first; carry on with it.
        attempt = _existing_attempt(db, round_.id, person.id)
        if attempt is None:
            raise
    db.commit()
    return _student_view(db, quiz, round_, attempt)


def _own_attempt(
    db: Session, user: User, attempt_id: uuid.UUID, *, lock: bool = False
) -> QuizAttempt:
    person = own_person(db, user)
    query = db.query(QuizAttempt).filter(
        QuizAttempt.id == attempt_id, QuizAttempt.person_id == person.id
    )
    attempt = (query.with_for_update() if lock else query).one_or_none()
    if attempt is None:
        raise DomainError(status_code=404, detail="Quiz attempt not found")
    return attempt


def _require_starter(attempt: QuizAttempt, user: User) -> None:
    """Answers go in only from the account that started the attempt, so a
    People record linked to another account cannot be answered for."""
    if attempt.user_id != user.id:
        raise DomainError(
            status_code=409, detail="This attempt was started from another account"
        )


def my_attempt(*, attempt_id: uuid.UUID, db: Session, user: User) -> dict[str, Any]:
    attempt = _own_attempt(db, user, attempt_id)
    round_ = attempt.round
    return _student_view(db, round_.quiz, round_, attempt)


def _save_answers(
    attempt: QuizAttempt, page: int, answers: list[QuizAnswerInput]
) -> None:
    """Replace the answers on one page; a question left out is unanswered."""
    layout = attempt.layout or []
    if page >= page_count(len(layout), attempt.page_size):
        raise DomainError(status_code=400, detail="This quiz has no such page")
    positions = page_positions(
        page, question_count=len(layout), page_size=attempt.page_size
    )
    given: dict[int, QuizAnswerInput] = {}
    for answer in answers:
        if answer.position not in positions:
            raise DomainError(
                status_code=400, detail="An answer is not for a question on this page"
            )
        if answer.position in given:
            raise DomainError(
                status_code=400, detail="A question on this page is answered twice"
            )
        given[answer.position] = answer
    rows = {row.position: row for row in attempt.answers}
    for position in positions:
        item = layout[position]
        row = rows[position]
        answer = given.get(position)
        if item["kind"] == "choice":
            if answer is not None and answer.text:
                raise DomainError(
                    status_code=400,
                    detail="A multiple-choice question is answered with an option",
                )
            option_id = answer.option_id if answer is not None else None
            if option_id is not None and str(option_id) not in {
                option["id"] for option in item.get("options") or []
            }:
                raise DomainError(
                    status_code=400, detail="Choose one of the question's options"
                )
            row.option_id = option_id
            row.text_answer = ""
        else:
            if answer is not None and answer.option_id is not None:
                raise DomainError(
                    status_code=400, detail="A written question is answered with text"
                )
            row.option_id = None
            row.text_answer = clean_text(
                answer.text if answer is not None else "",
                max_len=8000,
                label="answer",
            )
    attempt.pages_saved = max(attempt.pages_saved, page + 1)
    attempt.saved_at = utc_now_naive()


def _sitting_attempt(
    db: Session, user: User, attempt_id: uuid.UUID
) -> tuple[QuizAttempt, QuizRound]:
    attempt = _own_attempt(db, user, attempt_id, lock=True)
    if attempt.status != "in_progress":
        raise DomainError(status_code=409, detail="This attempt is already submitted")
    _require_starter(attempt, user)
    round_ = _shared_round(db, attempt.round_id).one()
    _require_open(round_, today_for_schedule())
    return attempt, round_


def save_page(
    *,
    attempt_id: uuid.UUID,
    page: int,
    payload: QuizPageInput,
    db: Session,
    user: User,
) -> dict[str, Any]:
    """Save one page of answers as the person moves on from it."""
    attempt, _ = _sitting_attempt(db, user, attempt_id)
    _save_answers(attempt, page, payload.answers)
    db.commit()
    return {
        "id": str(attempt.id),
        "pages_saved": attempt.pages_saved,
        "saved_at": iso(attempt.saved_at),
    }


def submit_attempt(
    *,
    attempt_id: uuid.UUID,
    payload: QuizSubmitInput,
    db: Session,
    user: User,
) -> dict[str, Any]:
    """Save the last page shown and hand the attempt in for marking."""
    attempt, round_ = _sitting_attempt(db, user, attempt_id)
    _save_answers(attempt, payload.page, payload.answers)
    now = utc_now_naive()
    attempt.submitted_at = now
    mark(attempt)
    settle(attempt, now)
    db.commit()
    return _student_view(db, round_.quiz, round_, attempt)
