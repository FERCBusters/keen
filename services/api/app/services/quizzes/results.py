"""Quiz results: round statistics, insights and each person's answers.

quiz.manage sees every round as it happens, each person's score and answers,
and which wrong answers people choose most. quiz.read sees the same averages
and question success rates for rounds that have closed with every response
marked and at least three people finished, and never names or answers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    IsmsPerson,
    Quiz,
    QuizAttempt,
    QuizParticipant,
    QuizRound,
    User,
)
from app.services.errors import DomainError
from app.services.frameworks import framework_parameter
from app.services.schedules import occurrences_between, today_for_schedule

from .access import can_manage_quizzes, require_manage, require_read
from .attempts import attempt_view
from .common import iso, quiz_or_404, result_ready
from .scoring import TROPHIES, passed, shown_score, trophy

# Questions listed in each insight.
INSIGHT_SIZE = 5


@dataclass
class RoundStats:
    participants: int = 0
    started: int = 0
    pending: int = 0
    scores: list[float] = field(default_factory=list)

    @property
    def complete(self) -> int:
        return len(self.scores)

    @property
    def average(self) -> float | None:
        return round(sum(self.scores) / len(self.scores), 2) if self.scores else None


def round_stats(db: Session, rounds: list[QuizRound]) -> dict[uuid.UUID, RoundStats]:
    stats = {round_.id: RoundStats() for round_ in rounds}
    if not stats:
        return stats
    ids = list(stats)
    for (round_id,) in db.query(QuizParticipant.round_id).filter(
        QuizParticipant.round_id.in_(ids)
    ):
        stats[round_id].participants += 1
    for round_id, status, score in db.query(
        QuizAttempt.round_id, QuizAttempt.status, QuizAttempt.score
    ).filter(QuizAttempt.round_id.in_(ids)):
        entry = stats[round_id]
        entry.started += 1
        if status == "submitted":
            entry.pending += 1
        elif status == "complete":
            entry.scores.append(float(score))
    return stats


def _ready(round_: QuizRound, stats: RoundStats) -> bool:
    return result_ready(round_.status, stats.complete, stats.pending)


def _pass_rate(scores: list[float], pass_mark: int | None) -> float | None:
    if pass_mark is None or not scores:
        return None
    reached = sum(1 for score in scores if passed(score, pass_mark))
    return round(100 * reached / len(scores), 1)


def round_out(
    quiz: Quiz, round_: QuizRound, stats: RoundStats, *, manager: bool
) -> dict[str, Any]:
    shown = manager or _ready(round_, stats)
    return {
        "id": str(round_.id),
        "opens_on": iso(round_.opens_on),
        "due_on": iso(round_.due_on),
        "status": round_.status,
        "scheduled": bool(round_.scheduled),
        "closed_at": iso(round_.closed_at),
        "participants": stats.participants,
        "started": stats.started,
        "pending_grading": stats.pending,
        "complete": stats.complete,
        "completion_rate": round(100 * stats.complete / stats.participants, 1)
        if stats.participants
        else None,
        "results_shown": shown,
        "recorded": _ready(round_, stats),
        "average": stats.average if shown else None,
        "pass_rate": _pass_rate(stats.scores, quiz.pass_mark) if shown else None,
    }


def _summary(quiz: Quiz, rounds, stats: dict[uuid.UUID, RoundStats]) -> dict:
    scores = [score for round_ in rounds for score in stats[round_.id].scores]
    participants = sum(stats[round_.id].participants for round_ in rounds)
    distribution = [0] * 10
    trophies = dict.fromkeys(TROPHIES, 0)
    for score in scores:
        distribution[min(9, int(score // 10))] += 1
        earned = trophy(score, quiz.pass_mark)
        if earned:
            trophies[earned] += 1
    return {
        "rounds": len(rounds),
        "participants": participants,
        "responses": len(scores),
        "completion_rate": round(100 * len(scores) / participants, 1)
        if participants
        else None,
        "average": round(sum(scores) / len(scores), 2) if scores else None,
        "pass_rate": _pass_rate(scores, quiz.pass_mark),
        # Completed responses in each tenth of the score range.
        "distribution": distribution,
        "trophies": trophies,
    }


@dataclass
class _QuestionStats:
    key: str
    prompt: str
    kind: str
    question_id: str | None
    asked: int = 0
    answered: int = 0
    marked: int = 0
    points: float = 0.0
    grades: list[float] = field(default_factory=list)
    options: dict[str, dict] = field(default_factory=dict)


def _question_stats(attempts: list[QuizAttempt]) -> dict[str, _QuestionStats]:
    stats: dict[str, _QuestionStats] = {}
    # Later attempts carry the question's latest wording.
    # Every question of an attempt has an answer row, blank until answered.
    for attempt in sorted(attempts, key=lambda a: a.started_at):
        answers = {answer.position: answer for answer in attempt.answers}
        for position, item in enumerate(attempt.layout or []):
            answer = answers[position]
            question_id = str(answer.question_id) if answer.question_id else None
            key = question_id or f"prompt:{item['prompt']}"
            entry = stats.get(key)
            if entry is None:
                entry = stats[key] = _QuestionStats(
                    key=key,
                    prompt=item["prompt"],
                    kind=item["kind"],
                    question_id=question_id,
                )
            entry.prompt = item["prompt"]
            entry.asked += 1
            for option in item.get("options") or []:
                counted = entry.options.setdefault(
                    option["id"], {"id": option["id"], "chosen": 0}
                )
                counted["text"] = option["text"]
                counted["correct"] = bool(option.get("correct"))
            if answer.option_id or (answer.text_answer or "").strip():
                entry.answered += 1
            if answer.option_id:
                # Saved answers name an option of the question as shown.
                entry.options[str(answer.option_id)]["chosen"] += 1
            if answer.points is not None:
                entry.marked += 1
                entry.points += float(answer.points)
            if answer.grade is not None:
                entry.grades.append(float(answer.grade))
    return stats


def _question_out(entry: _QuestionStats, *, manager: bool) -> dict[str, Any]:
    out = {
        "question_id": entry.question_id,
        "prompt": entry.prompt,
        "kind": entry.kind,
        "asked": entry.asked,
        "answered": entry.answered,
        # The average mark, as a percentage of a full mark.
        "success_rate": round(100 * entry.points / entry.marked, 1)
        if entry.marked
        else None,
        "average_grade": round(sum(entry.grades) / len(entry.grades), 2)
        if entry.grades
        else None,
    }
    if manager and entry.kind == "choice":
        out["options"] = [
            {
                **option,
                "share": round(100 * option["chosen"] / entry.answered, 1)
                if entry.answered
                else None,
            }
            for option in entry.options.values()
        ]
    return out


def _ordered_questions(quiz: Quiz, stats: dict[str, _QuestionStats]) -> list:
    order = {str(question.id): index for index, question in enumerate(quiz.questions)}
    return sorted(
        stats.values(),
        key=lambda entry: (
            order.get(entry.question_id or "", len(order)),
            entry.prompt,
        ),
    )


def _insights(entries: list[_QuestionStats], *, manager: bool) -> dict[str, Any]:
    rated = [entry for entry in entries if entry.marked]

    def rate(entry: _QuestionStats) -> float:
        return entry.points / entry.marked

    def brief(entry: _QuestionStats) -> dict[str, Any]:
        return {
            "question_id": entry.question_id,
            "prompt": entry.prompt,
            "success_rate": round(100 * rate(entry), 1),
        }

    ordered = sorted(rated, key=rate)
    # The lower half are the hardest and the upper half the easiest, so no
    # question appears in both.
    hardest = ordered[: min(INSIGHT_SIZE, max(1, len(ordered) // 2))]
    rest = ordered[len(hardest) :]
    out: dict[str, Any] = {
        "hardest": [brief(entry) for entry in hardest],
        "easiest": [brief(entry) for entry in reversed(rest[-INSIGHT_SIZE:])],
    }
    if manager:
        wrong = [
            {
                "question_id": entry.question_id,
                "prompt": entry.prompt,
                "answer": option["text"],
                "chosen": option["chosen"],
                "share": round(100 * option["chosen"] / entry.answered, 1),
            }
            for entry in entries
            if entry.kind == "choice" and entry.answered
            for option in entry.options.values()
            if option["chosen"] and not option["correct"]
        ]
        wrong.sort(key=lambda item: (-item["chosen"], -item["share"]))
        out["common_wrong_answers"] = wrong[:INSIGHT_SIZE]
    return out


def _people(
    db: Session, quiz: Quiz, round_: QuizRound, attempts: list[QuizAttempt]
) -> list[dict[str, Any]]:
    """Everyone the round is for, with their attempt; quiz.manage only.

    A participant with an attempt stays a participant, so each attempt
    belongs to a listed person unless that person's record was deleted.
    """
    by_person = {a.person_id: a for a in attempts if a.person_id is not None}
    participants = (
        db.query(IsmsPerson)
        .join(QuizParticipant, QuizParticipant.person_id == IsmsPerson.id)
        .filter(QuizParticipant.round_id == round_.id)
        .order_by(func.lower(IsmsPerson.name))
        .all()
    )
    rows = [(person, by_person.get(person.id)) for person in participants]
    rows.extend((None, a) for a in attempts if a.person_id is None)
    return [
        {
            "person_id": str(person.id) if person else None,
            "name": person.name if person else "Deleted person",
            "attempt_id": str(attempt.id) if attempt else None,
            "status": attempt.status if attempt else "not_started",
            "pages_saved": attempt.pages_saved if attempt else 0,
            "submitted_at": iso(attempt.submitted_at) if attempt else None,
            "score": shown_score(attempt.score) if attempt else None,
            "passed": passed(attempt.score, quiz.pass_mark) if attempt else None,
            "trophy": trophy(attempt.score, quiz.pass_mark) if attempt else None,
        }
        for person, attempt in rows
    ]


def quiz_results(
    *, quiz_id: uuid.UUID, round_id: uuid.UUID | None, db: Session, user: User
) -> dict[str, Any]:
    require_read(db, user)
    manager = can_manage_quizzes(db, user)
    quiz = quiz_or_404(db, quiz_id)
    rounds = list(quiz.rounds)
    stats = round_stats(db, rounds)
    shown = [r for r in rounds if manager or _ready(r, stats[r.id])]
    scope = shown
    if round_id is not None:
        scope = [r for r in shown if r.id == round_id]
        if not scope:
            raise DomainError(status_code=404, detail="Round not found")
    # Readers count only completed responses; quiz.manage also sees answers
    # still waiting for a grade.
    statuses = ("submitted", "complete") if manager else ("complete",)
    attempts = (
        db.query(QuizAttempt)
        .options(selectinload(QuizAttempt.answers))
        .filter(
            QuizAttempt.round_id.in_([r.id for r in scope]),
            QuizAttempt.status.in_(statuses),
        )
        .all()
        if scope
        else []
    )
    entries = _ordered_questions(quiz, _question_stats(attempts))
    out = {
        "quiz": {
            "id": str(quiz.id),
            "title": quiz.title,
            "pass_mark": quiz.pass_mark,
            "show_scores": bool(quiz.show_scores),
        },
        "can_manage": manager,
        "round_id": str(round_id) if round_id else None,
        "rounds": [round_out(quiz, r, stats[r.id], manager=manager) for r in shown],
        # Rounds whose results stay with quiz.manage for now.
        "hidden_rounds": len(rounds) - len(shown),
        "summary": _summary(quiz, scope, stats),
        "questions": [_question_out(entry, manager=manager) for entry in entries],
        "insights": _insights(entries, manager=manager),
    }
    if manager and round_id is not None:
        everyone = db.query(QuizAttempt).filter(QuizAttempt.round_id == round_id).all()
        out["people"] = _people(db, quiz, scope[0], everyone)
    return out


def quiz_attempt_detail(
    *, quiz_id: uuid.UUID, attempt_id: uuid.UUID, db: Session, user: User
) -> dict[str, Any]:
    """One person's attempt with every answer and mark; quiz.manage only."""
    require_manage(db, user)
    quiz = quiz_or_404(db, quiz_id)
    attempt = (
        db.query(QuizAttempt)
        .filter(QuizAttempt.id == attempt_id, QuizAttempt.quiz_id == quiz.id)
        .one_or_none()
    )
    if attempt is None:
        raise DomainError(status_code=404, detail="Quiz attempt not found")
    out = attempt_view(quiz, attempt.round, attempt, review="shown", show_score=True)
    out["person"] = (
        {"id": str(attempt.person.id), "name": attempt.person.name}
        if attempt.person
        else None
    )
    ids = {answer.position: str(answer.id) for answer in attempt.answers}
    for question in out["questions"]:
        question["answer_id"] = ids.get(question["position"])
    return out


def quiz_calendar(
    *,
    start: date | None,
    end: date | None,
    framework: str | None,
    db: Session,
    user: User,
) -> dict[str, Any]:
    """Rounds and scheduled openings between two dates."""
    require_read(db, user)
    first = start or today_for_schedule().replace(day=1)
    if end is None:
        following_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        last = following_month - timedelta(days=1)
    else:
        last = end
    if last < first:
        raise DomainError(status_code=400, detail="end cannot be before start")
    if (last - first).days > 400:
        raise DomainError(status_code=400, detail="The calendar covers up to 400 days")
    fw = framework_parameter(framework)
    quizzes = db.query(Quiz).filter(Quiz.framework_slug == fw).all()
    titles = {quiz.id: quiz.title for quiz in quizzes}
    occurrences = [
        {"date": iso(day), "quiz_id": str(quiz.id), "title": quiz.title}
        for quiz in quizzes
        if quiz.status == "active"
        for day in occurrences_between(quiz, start=first, end=last, limit=100)
    ]
    rounds = (
        db.query(QuizRound)
        .filter(
            QuizRound.quiz_id.in_(list(titles)),
            QuizRound.opens_on <= last,
            QuizRound.due_on >= first,
        )
        .order_by(QuizRound.opens_on.asc())
        .all()
        if titles
        else []
    )
    return {
        "start": iso(first),
        "end": iso(last),
        "occurrences": sorted(occurrences, key=lambda o: (o["date"], o["title"])),
        "rounds": [
            {
                "id": str(round_.id),
                "quiz_id": str(round_.quiz_id),
                "title": titles[round_.quiz_id],
                "opens_on": iso(round_.opens_on),
                "due_on": iso(round_.due_on),
                "status": round_.status,
            }
            for round_ in rounds
        ],
    }
