"""Request bodies for quiz operations."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class QuizSettingsInput(BaseModel):
    """A quiz's settings. On update, only the fields sent change."""

    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=20000)
    framework: str | None = Field(default=None, max_length=64)
    status: Literal["draft", "active", "archived"] | None = None
    pass_mark: int | None = Field(default=None, ge=0, le=100)
    questions_per_attempt: int | None = Field(default=None, ge=1, le=200)
    page_size: int | None = Field(default=None, ge=1, le=50)
    show_scores: bool | None = None
    reveal_answers: Literal["never", "after_close", "after_submit"] | None = None
    due_days: int | None = Field(default=None, ge=1, le=365)
    controls: list[str] | None = Field(default=None, max_length=200)
    start_date: date | None = None
    schedule_recurrence: str | None = Field(default=None, max_length=32)
    schedule_interval: int | None = None
    schedule_date_rule: str | None = Field(default=None, max_length=32)
    schedule_anchor_month: int | None = None
    schedule_until_date: date | None = None


class QuizOptionInput(BaseModel):
    id: uuid.UUID | None = None
    text: str = Field(min_length=1, max_length=2000)
    correct: bool = False


class QuizQuestionInput(BaseModel):
    id: uuid.UUID | None = None
    kind: Literal["choice", "text"]
    prompt: str = Field(min_length=1, max_length=4000)
    explanation: str = Field(default="", max_length=4000)
    options: list[QuizOptionInput] = Field(default_factory=list, max_length=10)


class QuizQuestionsInput(BaseModel):
    questions: list[QuizQuestionInput] = Field(max_length=200)


class QuizAudienceInput(BaseModel):
    person_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)
    # Keen accounts; one without a People record gets one linked to it.
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)
    org_node_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


class QuizRoundInput(BaseModel):
    # Both default from the quiz: open today, due after its days to answer.
    due_on: date | None = None
    opens_on: date | None = None


class QuizRoundUpdate(BaseModel):
    status: Literal["open", "closed"] | None = None
    due_on: date | None = None


class QuizAnswerInput(BaseModel):
    position: int = Field(ge=0, le=1000)
    option_id: uuid.UUID | None = None
    text: str | None = Field(default=None, max_length=8000)


class QuizPageInput(BaseModel):
    answers: list[QuizAnswerInput] = Field(default_factory=list, max_length=50)


class QuizSubmitInput(QuizPageInput):
    page: int = Field(ge=0, le=1000)


class QuizGradeInput(BaseModel):
    grade: float = Field(ge=1, le=5)
    feedback: str = Field(default="", max_length=4000)
