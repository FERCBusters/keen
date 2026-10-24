"""Quiz HTTP adapters.

quiz.manage writes pass the middleware's /v1/quizzes gate; a person's own
sittings under /v1/me/quizzes are open to every signed-in account, and the
operations check that the round or attempt belongs to the account's person.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import Depends, Path, Query, Request
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.routing import Router
from app.db.models import User
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.services.quizzes import (
    access,
    attempts,
    audience,
    authoring,
    grading,
    results,
    rounds,
)
from app.services.quizzes.schemas import (
    QuizAudienceInput,
    QuizGradeInput,
    QuizPageInput,
    QuizQuestionsInput,
    QuizRoundInput,
    QuizRoundUpdate,
    QuizSettingsInput,
    QuizSubmitInput,
)

router = Router()


def _reader(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    access.require_read(db, user)
    return user


def _manager(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    access.require_manage(db, user)
    return user


@router.get("/v1/quizzes")
def list_quizzes(
    framework: str | None = Query(None, max_length=64),
    user=Depends(_reader),
    db: Session = Depends(get_db),
):
    return authoring.list_quizzes(framework=framework, db=db, user=user)


@router.post("/v1/quizzes", status_code=201)
def create_quiz(
    payload: QuizSettingsInput,
    request: Request,
    user=Depends(_manager),
    db: Session = Depends(get_db),
):
    return authoring.create_quiz(
        payload=payload, context=change_context(request), db=db, user=user
    )


@router.get("/v1/quizzes/calendar")
def quiz_calendar(
    start: date | None = None,
    end: date | None = None,
    framework: str | None = Query(None, max_length=64),
    user=Depends(_reader),
    db: Session = Depends(get_db),
):
    return results.quiz_calendar(
        start=start, end=end, framework=framework, db=db, user=user
    )


@router.get("/v1/quizzes/assignees")
def quiz_assignees(user=Depends(_manager), db: Session = Depends(get_db)):
    return audience.assignees(db=db, user=user)


@router.get("/v1/quizzes/{quiz_id}")
def get_quiz(quiz_id: uuid.UUID, user=Depends(_reader), db: Session = Depends(get_db)):
    return authoring.get_quiz(quiz_id=quiz_id, db=db, user=user)


@router.patch("/v1/quizzes/{quiz_id}")
def update_quiz(
    quiz_id: uuid.UUID,
    payload: QuizSettingsInput,
    request: Request,
    user=Depends(_manager),
    db: Session = Depends(get_db),
):
    return authoring.update_quiz(
        quiz_id=quiz_id,
        payload=payload,
        context=change_context(request),
        db=db,
        user=user,
    )


@router.delete("/v1/quizzes/{quiz_id}")
def delete_quiz(
    quiz_id: uuid.UUID,
    request: Request,
    user=Depends(_manager),
    db: Session = Depends(get_db),
):
    return authoring.delete_quiz(
        quiz_id=quiz_id, context=change_context(request), db=db, user=user
    )


@router.put("/v1/quizzes/{quiz_id}/questions")
def replace_quiz_questions(
    quiz_id: uuid.UUID,
    payload: QuizQuestionsInput,
    request: Request,
    user=Depends(_manager),
    db: Session = Depends(get_db),
):
    return authoring.replace_questions(
        quiz_id=quiz_id,
        payload=payload,
        context=change_context(request),
        db=db,
        user=user,
    )


@router.put("/v1/quizzes/{quiz_id}/audience")
def replace_quiz_audience(
    quiz_id: uuid.UUID,
    payload: QuizAudienceInput,
    request: Request,
    user=Depends(_manager),
    db: Session = Depends(get_db),
):
    return audience.replace_audience(
        quiz_id=quiz_id,
        payload=payload,
        context=change_context(request),
        db=db,
        user=user,
    )


@router.post("/v1/quizzes/{quiz_id}/rounds", status_code=201)
def start_quiz_round(
    quiz_id: uuid.UUID,
    payload: QuizRoundInput,
    user=Depends(_manager),
    db: Session = Depends(get_db),
):
    return rounds.start_round(quiz_id=quiz_id, payload=payload, db=db, user=user)


@router.patch("/v1/quizzes/{quiz_id}/rounds/{round_id}")
def update_quiz_round(
    quiz_id: uuid.UUID,
    round_id: uuid.UUID,
    payload: QuizRoundUpdate,
    user=Depends(_manager),
    db: Session = Depends(get_db),
):
    return rounds.update_round(
        quiz_id=quiz_id, round_id=round_id, payload=payload, db=db, user=user
    )


@router.delete("/v1/quizzes/{quiz_id}/rounds/{round_id}")
def delete_quiz_round(
    quiz_id: uuid.UUID,
    round_id: uuid.UUID,
    user=Depends(_manager),
    db: Session = Depends(get_db),
):
    return rounds.delete_round(quiz_id=quiz_id, round_id=round_id, db=db, user=user)


@router.get("/v1/quizzes/{quiz_id}/results")
def quiz_results(
    quiz_id: uuid.UUID,
    round_id: uuid.UUID | None = None,
    user=Depends(_reader),
    db: Session = Depends(get_db),
):
    return results.quiz_results(quiz_id=quiz_id, round_id=round_id, db=db, user=user)


@router.get("/v1/quizzes/{quiz_id}/attempts/{attempt_id}")
def quiz_attempt(
    quiz_id: uuid.UUID,
    attempt_id: uuid.UUID,
    user=Depends(_manager),
    db: Session = Depends(get_db),
):
    return results.quiz_attempt_detail(
        quiz_id=quiz_id, attempt_id=attempt_id, db=db, user=user
    )


@router.get("/v1/quizzes/{quiz_id}/grading")
def quiz_grading_queue(
    quiz_id: uuid.UUID, user=Depends(_manager), db: Session = Depends(get_db)
):
    return grading.grading_queue(quiz_id=quiz_id, db=db, user=user)


@router.put("/v1/quizzes/{quiz_id}/answers/{answer_id}/grade")
def grade_quiz_answer(
    quiz_id: uuid.UUID,
    answer_id: uuid.UUID,
    payload: QuizGradeInput,
    user=Depends(_manager),
    db: Session = Depends(get_db),
):
    return grading.grade_answer(
        quiz_id=quiz_id, answer_id=answer_id, payload=payload, db=db, user=user
    )


@router.get("/v1/me/quizzes")
def my_quizzes(
    user: User = Depends(require_authenticated), db: Session = Depends(get_db)
):
    return attempts.my_quizzes(db=db, user=user)


@router.post("/v1/me/quizzes/rounds/{round_id}/attempt")
def start_my_attempt(
    round_id: uuid.UUID,
    user: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    return attempts.start_attempt(round_id=round_id, db=db, user=user)


@router.get("/v1/me/quizzes/attempts/{attempt_id}")
def my_attempt(
    attempt_id: uuid.UUID,
    user: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    return attempts.my_attempt(attempt_id=attempt_id, db=db, user=user)


@router.put("/v1/me/quizzes/attempts/{attempt_id}/pages/{page}")
def save_my_page(
    attempt_id: uuid.UUID,
    payload: QuizPageInput,
    page: int = Path(ge=0, le=1000),
    user: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    return attempts.save_page(
        attempt_id=attempt_id, page=page, payload=payload, db=db, user=user
    )


@router.post("/v1/me/quizzes/attempts/{attempt_id}/submit")
def submit_my_attempt(
    attempt_id: uuid.UUID,
    payload: QuizSubmitInput,
    user: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    return attempts.submit_attempt(
        attempt_id=attempt_id, payload=payload, db=db, user=user
    )
