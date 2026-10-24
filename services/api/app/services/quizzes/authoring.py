"""Creating and changing quizzes, their questions and their schedules.

A quiz starts as a draft. Activating it needs at least one question, after
which its schedule opens rounds and quiz.manage can start them by hand.
Archiving a quiz closes its open rounds and stops its schedule. A quiz that
people have sat cannot be deleted, so their results stay on record.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    Quiz,
    QuizAttempt,
    QuizOption,
    QuizQuestion,
    QuizRound,
    User,
)
from app.services.context import ChangeContext
from app.services.errors import DomainError
from app.services.frameworks import framework_parameter
from app.services.isms.serializers import _linked_controls
from app.services.schedules import revise_schedule
from app.services.text import clean_text

from .access import can_manage_quizzes, require_manage, require_read
from .audience import audience_out, audience_size, queue_invitations
from .common import (
    iso,
    quiz_or_404,
    quiz_snapshot,
    record_quiz_change,
    result_ready,
    schedule_out,
)
from .measure import create_measure, remove_measure, sync_measure
from .results import round_out, round_stats
from .rounds import close_round, open_due_round, quiz_availability
from .schemas import QuizQuestionInput, QuizQuestionsInput, QuizSettingsInput

SCHEDULE_FIELDS = {
    "start_date",
    "schedule_recurrence",
    "schedule_interval",
    "schedule_date_rule",
    "schedule_anchor_month",
    "schedule_until_date",
}
# Settings that cannot be cleared once set.
REQUIRED_SETTINGS = {
    "title",
    "status",
    "page_size",
    "show_scores",
    "reveal_answers",
    "due_days",
}
MIN_OPTIONS = 2


def _controls(db: Session, quiz: Quiz) -> list[dict[str, Any]]:
    """The controls a quiz tests: those linked to its measure."""
    return _linked_controls(
        db, "effectiveness_measure", quiz.measure_id, quiz.framework_slug
    )


def question_out(question: QuizQuestion, *, manager: bool) -> dict[str, Any]:
    """A question as quiz.manage sees it, or without its answer for quiz.read.

    quiz.read gets the options in alphabetical order, so the order they were
    written in says nothing about which is right.
    """
    options = (
        question.options
        if manager
        else sorted(question.options, key=lambda option: option.text.casefold())
    )
    out: dict[str, Any] = {
        "id": str(question.id),
        "position": question.position,
        "kind": question.kind,
        "prompt": question.prompt,
        "options": [
            {"id": str(option.id), "text": option.text}
            | ({"correct": bool(option.is_correct)} if manager else {})
            for option in options
        ],
    }
    if manager:
        out["explanation"] = question.explanation or ""
    return out


def quiz_out(db: Session, quiz: Quiz, *, manager: bool) -> dict[str, Any]:
    rounds = list(quiz.rounds)
    stats = round_stats(db, rounds)
    out: dict[str, Any] = {
        "id": str(quiz.id),
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
        "schedule": schedule_out(quiz),
        "measure_id": str(quiz.measure_id),
        "controls": _controls(db, quiz),
        "question_count": len(quiz.questions),
        "questions": [question_out(q, manager=manager) for q in quiz.questions],
        "rounds": [round_out(quiz, r, stats[r.id], manager=manager) for r in rounds],
        "availability": quiz_availability(quiz, rounds, stats),
        "created_at": iso(quiz.created_at),
        "updated_at": iso(quiz.updated_at),
        "can_manage": manager,
    }
    if manager:
        out["audience"] = audience_out(db, quiz)
    else:
        out["audience"] = {"size": audience_size(db, quiz)}
    return out


def _all_time_average(
    quiz: Quiz, stats: dict, *, manager: bool
) -> tuple[float | None, int]:
    scores = [
        score
        for round_ in quiz.rounds
        if manager
        or result_ready(
            round_.status, stats[round_.id].complete, stats[round_.id].pending
        )
        for score in stats[round_.id].scores
    ]
    if not scores:
        return None, 0
    return round(sum(scores) / len(scores), 2), len(scores)


def list_quizzes(*, framework: str | None, db: Session, user: User) -> dict[str, Any]:
    require_read(db, user)
    manager = can_manage_quizzes(db, user)
    fw = framework_parameter(framework)
    quizzes = (
        db.query(Quiz)
        .filter(Quiz.framework_slug == fw)
        .order_by(Quiz.status.asc(), Quiz.title.asc())
        .all()
    )
    stats = round_stats(db, [r for quiz in quizzes for r in quiz.rounds])
    items = []
    for quiz in quizzes:
        average, responses = _all_time_average(quiz, stats, manager=manager)
        items.append(
            {
                "id": str(quiz.id),
                "title": quiz.title,
                "description": quiz.description or "",
                "status": quiz.status,
                "pass_mark": quiz.pass_mark,
                "question_count": len(quiz.questions),
                "schedule": schedule_out(quiz),
                "open_rounds": sum(1 for r in quiz.rounds if r.status == "open"),
                "rounds": len(quiz.rounds),
                "availability": quiz_availability(quiz, quiz.rounds, stats),
                "average": average,
                "responses": responses,
                "audience_size": audience_size(db, quiz),
                "control_count": len(_controls(db, quiz)),
            }
        )
    return {"framework": fw, "can_manage": manager, "items": items}


def get_quiz(*, quiz_id: uuid.UUID, db: Session, user: User) -> dict[str, Any]:
    require_read(db, user)
    return quiz_out(db, quiz_or_404(db, quiz_id), manager=can_manage_quizzes(db, user))


def _apply_settings(quiz: Quiz, payload: QuizSettingsInput, fields: set[str]) -> None:
    for name in sorted(REQUIRED_SETTINGS & fields):
        if getattr(payload, name) is None:
            raise DomainError(status_code=400, detail=f"{name} is required")
    if "title" in fields:
        quiz.title = clean_text(
            payload.title, max_len=256, required=True, label="title", single_line=True
        )
    if "description" in fields:
        quiz.description = clean_text(
            payload.description, max_len=20000, label="description"
        )
    for name in (
        "pass_mark",
        "questions_per_attempt",
        "page_size",
        "show_scores",
        "reveal_answers",
        "due_days",
    ):
        if name in fields:
            setattr(quiz, name, getattr(payload, name))


def _check_activation(quiz: Quiz, status: str) -> None:
    if status == "active" and not quiz.questions:
        raise DomainError(
            status_code=409, detail="Add at least one question before activating"
        )


def create_quiz(
    *,
    payload: QuizSettingsInput,
    context: ChangeContext | None,
    db: Session,
    user: User,
) -> dict[str, Any]:
    require_manage(db, user)
    fields = set(payload.model_fields_set)
    if payload.status not in (None, "draft"):
        raise DomainError(status_code=400, detail="A new quiz starts as a draft")
    if not payload.title:
        raise DomainError(status_code=400, detail="title is required")
    quiz = Quiz(
        framework_slug=framework_parameter(payload.framework),
        status="draft",
        created_by_user_id=user.id,
    )
    _apply_settings(quiz, payload, fields)
    revise_schedule(quiz, payload, fields & SCHEDULE_FIELDS, start_required=False)
    create_measure(db, quiz, controls=payload.controls, user=user, context=context)
    db.add(quiz)
    db.flush()
    record_quiz_change(db, quiz, "created", None, quiz_snapshot(quiz), user, context)
    db.commit()
    db.refresh(quiz)
    return quiz_out(db, quiz, manager=True)


def update_quiz(
    *,
    quiz_id: uuid.UUID,
    payload: QuizSettingsInput,
    context: ChangeContext | None,
    db: Session,
    user: User,
) -> dict[str, Any]:
    require_manage(db, user)
    quiz = quiz_or_404(db, quiz_id, lock=True)
    before = quiz_snapshot(quiz)
    fields = set(payload.model_fields_set)
    if "framework" in fields and payload.framework != quiz.framework_slug:
        raise DomainError(status_code=400, detail="A quiz's framework cannot change")
    _apply_settings(quiz, payload, fields)
    if fields & SCHEDULE_FIELDS:
        revise_schedule(quiz, payload, fields & SCHEDULE_FIELDS, start_required=False)
    activating = (
        "status" in fields and payload.status == "active" and quiz.status != "active"
    )
    if "status" in fields and payload.status != quiz.status:
        _change_status(db, quiz, payload.status)
    # An active quiz opens the round it is due now: a one-off quiz as it is
    # activated, a scheduled one when today is a round date.
    opened = (
        open_due_round(db, quiz, user=user, activating=activating)
        if quiz.status == "active" and (activating or fields & SCHEDULE_FIELDS)
        else None
    )
    controls = payload.controls if "controls" in fields else None
    sync_measure(db, quiz, controls=controls, user=user, context=context)
    record_quiz_change(db, quiz, "updated", before, quiz_snapshot(quiz), user, context)
    db.commit()
    if opened is not None:
        queue_invitations([opened.id])
    db.refresh(quiz)
    return quiz_out(db, quiz, manager=True)


def _change_status(db: Session, quiz: Quiz, status: str) -> None:
    _check_activation(quiz, status)
    if status == "draft" and quiz.rounds:
        raise DomainError(
            status_code=409,
            detail="A quiz that has had rounds can be archived but not made a draft",
        )
    if status == "archived":
        for round_ in (
            db.query(QuizRound)
            .filter(QuizRound.quiz_id == quiz.id, QuizRound.status == "open")
            .with_for_update()
            .all()
        ):
            close_round(db, quiz, round_)
    quiz.status = status


def delete_quiz(
    *,
    quiz_id: uuid.UUID,
    context: ChangeContext | None,
    db: Session,
    user: User,
) -> dict[str, Any]:
    require_manage(db, user)
    quiz = quiz_or_404(db, quiz_id, lock=True)
    if db.query(QuizAttempt.id).filter(QuizAttempt.quiz_id == quiz.id).first():
        raise DomainError(
            status_code=409,
            detail="People have sat this quiz; archive it to keep their results",
        )
    record_quiz_change(db, quiz, "deleted", quiz_snapshot(quiz), None, user, context)
    measure = quiz.measure
    db.delete(quiz)
    db.flush()
    remove_measure(db, measure, user=user, context=context)
    db.commit()
    return {"ok": True}


def _clean_question(item: QuizQuestionInput, number: int) -> dict[str, Any]:
    label = f"question {number}"
    prompt = clean_text(item.prompt, max_len=4000, required=True, label=label)
    explanation = clean_text(
        item.explanation, max_len=4000, label=f"{label} explanation"
    )
    options = [
        (
            option.id,
            clean_text(
                option.text, max_len=2000, required=True, label=f"{label} option"
            ),
            option.correct,
        )
        for option in item.options
    ]
    if item.kind == "text":
        if options:
            raise DomainError(
                status_code=400, detail=f"Written {label} takes no options"
            )
    else:
        if len(options) < MIN_OPTIONS:
            raise DomainError(
                status_code=400,
                detail=f"Multiple-choice {label} needs at least {MIN_OPTIONS} options",
            )
        if sum(1 for _, _, correct in options if correct) != 1:
            raise DomainError(
                status_code=400,
                detail=f"Multiple-choice {label} needs exactly one correct option",
            )
        texts = [text.casefold() for _, text, _ in options]
        if len(set(texts)) != len(texts):
            raise DomainError(
                status_code=400, detail=f"The options of {label} repeat themselves"
            )
    return {
        "id": item.id,
        "kind": item.kind,
        "prompt": prompt,
        "explanation": explanation,
        "options": options,
    }


def replace_questions(
    *,
    quiz_id: uuid.UUID,
    payload: QuizQuestionsInput,
    context: ChangeContext | None,
    db: Session,
    user: User,
) -> dict[str, Any]:
    """Save a quiz's questions in order.

    Questions and options sent with their id are updated, new ones are added
    and the rest are removed. Attempts keep the questions as they were shown,
    so changes apply from the next attempt.
    """
    require_manage(db, user)
    quiz = quiz_or_404(db, quiz_id, lock=True)
    before = quiz_snapshot(quiz)
    cleaned = [
        _clean_question(item, number)
        for number, item in enumerate(payload.questions, start=1)
    ]
    if quiz.status == "active" and not cleaned:
        raise DomainError(
            status_code=409, detail="An active quiz needs at least one question"
        )
    existing = {question.id: question for question in quiz.questions}
    kept: list[QuizQuestion] = []
    for position, item in enumerate(cleaned):
        if item["id"] is not None and item["id"] not in existing:
            raise DomainError(status_code=400, detail="Unknown question")
        question = existing.get(item["id"]) or QuizQuestion(quiz_id=quiz.id)
        if question in kept:
            raise DomainError(status_code=400, detail="A question is listed twice")
        question.position = position
        question.kind = item["kind"]
        question.prompt = item["prompt"]
        question.explanation = item["explanation"]
        _replace_options(question, item["options"])
        kept.append(question)
    quiz.questions = kept
    db.flush()
    record_quiz_change(db, quiz, "updated", before, quiz_snapshot(quiz), user, context)
    db.commit()
    db.refresh(quiz)
    return quiz_out(db, quiz, manager=True)


def _replace_options(question: QuizQuestion, options: list[tuple]) -> None:
    existing = {option.id: option for option in question.options}
    kept: list[QuizOption] = []
    for position, (option_id, text, correct) in enumerate(options):
        if option_id is not None and option_id not in existing:
            raise DomainError(status_code=400, detail="Unknown option")
        option = existing.get(option_id) or QuizOption()
        if option in kept:
            raise DomainError(status_code=400, detail="An option is listed twice")
        option.position = position
        option.text = text
        option.is_correct = bool(correct)
        kept.append(option)
    question.options = kept
