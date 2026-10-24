"""Building a sitting, marking it and turning marks into rewards.

Each sitting shuffles the order of its questions and the order of every
question's options with an unbiased Fisher-Yates shuffle from the operating
system's random source, so no two people (or two sittings) learn answers by
position. When a quiz draws fewer questions than it holds, questions the
person has not been shown before come first.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from typing import Any

_RANDOM = random.SystemRandom()


def shuffled(items: Iterable[Any], rng: random.Random | None = None) -> list[Any]:
    """Return the items in a uniformly random order."""
    out = list(items)
    (rng or _RANDOM).shuffle(out)
    return out


def draw_questions(
    questions: Sequence[Any],
    *,
    count: int | None,
    seen: set[str],
    rng: random.Random | None = None,
) -> list[Any]:
    """Pick and order a sitting's questions.

    With a ``count`` below the number of questions, unseen questions are drawn
    first and seen ones fill the rest; the selection is then shuffled.
    """
    rng = rng or _RANDOM
    if count is None or count >= len(questions):
        return shuffled(questions, rng)
    unseen = shuffled([q for q in questions if str(q.id) not in seen], rng)
    repeat = shuffled([q for q in questions if str(q.id) in seen], rng)
    return shuffled((unseen + repeat)[:count], rng)


def build_layout(
    questions: Sequence[Any],
    *,
    count: int | None,
    seen: set[str],
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """The questions as one sitting shows them, options shuffled as well."""
    rng = rng or _RANDOM
    layout = []
    for question in draw_questions(questions, count=count, seen=seen, rng=rng):
        options = [
            {"id": str(o.id), "text": o.text, "correct": bool(o.is_correct)}
            for o in question.options
        ]
        layout.append(
            {
                "question_id": str(question.id),
                "kind": question.kind,
                "prompt": question.prompt,
                "explanation": question.explanation or "",
                "options": shuffled(options, rng),
            }
        )
    return layout


def page_count(question_count: int, page_size: int) -> int:
    return max(1, math.ceil(question_count / max(1, page_size)))


def page_positions(page: int, *, question_count: int, page_size: int) -> range:
    first = page * page_size
    return range(first, min(first + page_size, question_count))


def valid_grade(grade: float) -> bool:
    """Free-text grades run from 1 to 5 in half points."""
    return 1 <= grade <= 5 and float(grade * 2).is_integer()


def text_points(grade: float) -> float:
    """A graded free-text answer earns its grade out of 5."""
    return grade / 5


def score_from_points(points: Iterable[float], question_count: int) -> float:
    """The percentage a set of marked answers earns, kept unrounded so that
    averages of scores stay exact."""
    if question_count <= 0:
        return 0.0
    return 100 * sum(points) / question_count


def shown_score(score: float | None) -> float | None:
    """A score as people read it, to two decimal places."""
    return None if score is None else round(score, 2)


def option_correct(item: dict[str, Any], option_id: Any) -> bool:
    """Whether a chosen option is the right answer to a question as shown."""
    if option_id is None:
        return False
    chosen = str(option_id)
    return any(
        option["id"] == chosen and option.get("correct")
        for option in item.get("options") or []
    )


def mark(attempt: Any) -> None:
    """Mark an attempt's answers as it is submitted.

    Multiple-choice answers are marked at once. A written answer waits for an
    assessor's grade, and a blank one scores nothing.
    """
    for answer in attempt.answers:
        item = attempt.layout[answer.position]
        answer.is_correct = None
        if item["kind"] == "choice":
            answer.is_correct = option_correct(item, answer.option_id)
            answer.points = 1.0 if answer.is_correct else 0.0
        elif (answer.text_answer or "").strip():
            answer.points = None
        else:
            answer.points = 0.0


def settle(attempt: Any, now: Any) -> None:
    """Complete an attempt with its score once every answer is marked."""
    points = [answer.points for answer in attempt.answers]
    if any(value is None for value in points):
        attempt.status = "submitted"
        attempt.score = None
        attempt.completed_at = None
        return
    attempt.status = "complete"
    attempt.score = score_from_points(points, len(attempt.layout))
    attempt.completed_at = attempt.completed_at or now


def passed(score: float | None, pass_mark: int | None) -> bool | None:
    if score is None or pass_mark is None:
        return None
    return score >= pass_mark


TROPHIES = ("bronze", "silver", "gold")
# The floor for trophies when a quiz sets no pass mark.
DEFAULT_TROPHY_FLOOR = 50


def trophy(score: float | None, pass_mark: int | None) -> str | None:
    """The trophy a score wins: none below the pass mark, then bronze, silver
    and gold for each third of the way from the pass mark to 100%."""
    if score is None:
        return None
    floor = DEFAULT_TROPHY_FLOOR if pass_mark is None else pass_mark
    if score < floor:
        return None
    step = (100 - floor) / 3
    if score >= floor + 2 * step:
        return "gold"
    if score >= floor + step:
        return "silver"
    return "bronze"
