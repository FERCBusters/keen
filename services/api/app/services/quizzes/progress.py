"""A student's points, level, streak and badges.

Progress rewards taking part: finishing a round, finishing it promptly and
keeping a run of finished rounds going. Trophy and perfect-score rewards count
only rounds whose quiz shows students their scores, so progress never reveals
a score a quiz keeps hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

FINISH_POINTS = 100
PROMPT_POINTS = 25
TROPHY_POINTS = {"bronze": 15, "silver": 30, "gold": 50}
LEVELS = (
    (0, "Newcomer"),
    (150, "Apprentice"),
    (400, "Defender"),
    (800, "Guardian"),
    (1300, "Sentinel"),
    (2000, "Champion"),
    (3000, "Legend"),
)
BADGES = (
    ("first_quiz", "First steps", "Finish your first quiz."),
    (
        "streak_3",
        "On a roll",
        "Finish three assigned rounds in a row before they close.",
    ),
    ("quizzes_5", "Regular", "Finish five quizzes."),
    ("quizzes_10", "Devoted", "Finish ten quizzes."),
    ("early_bird", "Early bird", "Finish a quiz on the day it opens."),
    ("gold", "Gold standard", "Win a gold trophy."),
    ("perfect", "Flawless", "Score 100% on a quiz."),
)


@dataclass(frozen=True)
class Sitting:
    """One round assigned to the student, as progress sees it."""

    opens_on: date
    due_on: date
    closed: bool
    finished_on: date | None = None
    # Present only when the quiz shows students their scores.
    trophy: str | None = None
    perfect: bool = False


def sitting_points(sitting: Sitting) -> int:
    """Points one round earns: finishing, finishing promptly and its trophy."""
    if sitting.finished_on is None:
        return 0
    points = FINISH_POINTS
    halfway = sitting.opens_on + (sitting.due_on - sitting.opens_on) / 2
    if sitting.finished_on <= halfway:
        points += PROMPT_POINTS
    return points + TROPHY_POINTS.get(sitting.trophy or "", 0)


def level_for(points: int) -> dict:
    index = max(i for i, (floor, _) in enumerate(LEVELS) if points >= floor)
    floor, name = LEVELS[index]
    following = LEVELS[index + 1][0] if index + 1 < len(LEVELS) else None
    return {
        "level": index + 1,
        "name": name,
        "floor": floor,
        "next": following,
    }


def progress(sittings: list[Sitting]) -> dict:
    """Summarise a student's rounds, oldest due date first.

    Of rounds due on the same day, finished ones count first, so a round left
    unfinished that day ends the run they make.
    """
    ordered = sorted(
        sittings,
        key=lambda s: (
            s.due_on,
            s.opens_on,
            s.finished_on is None,
            s.finished_on or date.max,
        ),
    )
    points = sum(sitting_points(s) for s in ordered)
    finished = [s for s in ordered if s.finished_on is not None]
    streak = best = 0
    for sitting in ordered:
        if sitting.finished_on is not None:
            streak += 1
            best = max(best, streak)
        elif sitting.closed:
            # A round that closed unfinished ends the run; an open one waits.
            streak = 0
    earned = {
        "first_quiz": len(finished) >= 1,
        "streak_3": best >= 3,
        "quizzes_5": len(finished) >= 5,
        "quizzes_10": len(finished) >= 10,
        "early_bird": any(s.finished_on == s.opens_on for s in finished),
        "gold": any(s.trophy == "gold" for s in finished),
        "perfect": any(s.perfect for s in finished),
    }
    return {
        "points": points,
        **level_for(points),
        "finished": len(finished),
        "streak": streak,
        "best_streak": best,
        "badges": [
            {
                "key": key,
                "label": label,
                "description": description,
                "earned": earned[key],
            }
            for key, label, description in BADGES
        ],
    }
