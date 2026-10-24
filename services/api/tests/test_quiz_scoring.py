"""Building, marking and rewarding a quiz sitting, as pure functions."""

import random
from collections import Counter
from datetime import date, datetime
from itertools import permutations
from types import SimpleNamespace

import pytest
from app.services.quizzes import progress, scoring


def _question(qid, kind="choice", options=()):
    return SimpleNamespace(
        id=qid,
        kind=kind,
        prompt=f"Question {qid}",
        explanation="",
        options=[
            SimpleNamespace(id=f"{qid}-{n}", text=text, is_correct=n == 0)
            for n, text in enumerate(options)
        ],
    )


def test_every_order_is_equally_likely():
    rng = random.Random(7)
    counts = Counter(tuple(scoring.shuffled("abc", rng)) for _ in range(6000))
    assert set(counts) == set(permutations("abc"))
    # Each of the six orders lands within 10% of its fair share.
    assert all(900 <= count <= 1100 for count in counts.values())


def test_a_partial_draw_prefers_questions_not_seen_before():
    questions = [_question(n) for n in range(6)]
    rng = random.Random(3)
    drawn = scoring.draw_questions(questions, count=3, seen={"0", "1", "2"}, rng=rng)
    assert {q.id for q in drawn} == {3, 4, 5}
    topped_up = scoring.draw_questions(
        questions, count=4, seen={"0", "1", "2", "3", "4"}, rng=rng
    )
    assert {5} < {q.id for q in topped_up} and len(topped_up) == 4
    assert len(scoring.draw_questions(questions, count=None, seen=set(), rng=rng)) == 6
    assert len(scoring.draw_questions(questions, count=10, seen=set(), rng=rng)) == 6


def test_a_layout_snapshots_questions_with_their_options_shuffled():
    questions = [_question("q", options=["Right", "Wrong", "Also wrong"])]
    seen_first = Counter()
    for seed in range(60):
        [item] = scoring.build_layout(
            questions, count=None, seen=set(), rng=random.Random(seed)
        )
        seen_first[item["options"][0]["text"]] += 1
    assert set(seen_first) == {"Right", "Wrong", "Also wrong"}
    assert item["question_id"] == "q" and item["kind"] == "choice"
    assert {o["id"]: o["correct"] for o in item["options"]} == {
        "q-0": True,
        "q-1": False,
        "q-2": False,
    }


def test_pages_split_a_sitting_into_checkpoints():
    assert scoring.page_count(0, 5) == 1
    assert scoring.page_count(11, 5) == 3
    assert list(scoring.page_positions(2, question_count=11, page_size=5)) == [10]
    assert list(scoring.page_positions(0, question_count=11, page_size=5)) == [
        0,
        1,
        2,
        3,
        4,
    ]


@pytest.mark.parametrize(
    "grade,valid", [(1, True), (4.5, True), (5, True), (0.5, False), (3.25, False)]
)
def test_written_grades_run_from_one_to_five_in_half_points(grade, valid):
    assert scoring.valid_grade(grade) is valid


def test_scores_stay_exact_until_shown():
    assert scoring.text_points(3.5) == pytest.approx(0.7)
    score = scoring.score_from_points([1, 0, 0.7], 3)
    assert score == pytest.approx(56.666666)
    assert scoring.shown_score(score) == 56.67
    assert scoring.shown_score(None) is None
    assert scoring.score_from_points([], 0) == 0.0
    assert scoring.passed(None, 50) is None
    assert scoring.passed(70, None) is None
    assert scoring.passed(70, 70) is True and scoring.passed(69.9, 70) is False


@pytest.mark.parametrize(
    "score,pass_mark,expected",
    [
        (None, 50, None),
        (69.9, 70, None),
        (70, 70, "bronze"),
        (80, 70, "silver"),
        (90, 70, "gold"),
        (49.9, None, None),
        (50, None, "bronze"),
        (66.67, None, "silver"),
        (83.34, None, "gold"),
        (99, 100, None),
        (100, 100, "gold"),
        (0, 0, "bronze"),
    ],
)
def test_trophies_split_the_way_from_the_pass_mark_to_full_marks(
    score, pass_mark, expected
):
    assert scoring.trophy(score, pass_mark) == expected


def _attempt(*answers):
    layout = [
        {
            "kind": kind,
            "options": [{"id": "right", "correct": True}, {"id": "wrong"}],
        }
        for kind, _ in answers
    ]
    rows = [
        SimpleNamespace(
            position=n,
            option_id=value if kind == "choice" else None,
            text_answer=value if kind == "text" else "",
            is_correct="unset",
            points="unset",
        )
        for n, (kind, value) in enumerate(answers)
    ]
    return SimpleNamespace(
        layout=layout, answers=rows, status="in_progress", score=None, completed_at=None
    )


def test_marking_waits_for_written_answers_and_scores_blank_ones_zero():
    attempt = _attempt(
        ("choice", "right"), ("choice", "wrong"), ("choice", None), ("text", " ")
    )
    scoring.mark(attempt)
    assert [a.is_correct for a in attempt.answers] == [True, False, False, None]
    assert [a.points for a in attempt.answers] == [1.0, 0.0, 0.0, 0.0]
    now = datetime(2026, 5, 1, 9, 0)
    scoring.settle(attempt, now)
    assert (attempt.status, attempt.score, attempt.completed_at) == (
        "complete",
        25.0,
        now,
    )

    pending = _attempt(("choice", "right"), ("text", "Phishing uses urgency"))
    scoring.mark(pending)
    scoring.settle(pending, now)
    assert (pending.status, pending.score, pending.completed_at) == (
        "submitted",
        None,
        None,
    )
    pending.answers[1].points = 0.5
    scoring.settle(pending, now)
    assert (pending.status, pending.score) == ("complete", 75.0)
    # A later change keeps the time it was first completed.
    pending.answers[1].points = 1.0
    scoring.settle(pending, datetime(2026, 6, 1))
    assert (pending.score, pending.completed_at) == (100.0, now)
    assert scoring.option_correct({"options": []}, "right") is False
    assert (
        scoring.option_correct({"options": [{"id": "x", "correct": True}]}, None)
        is False
    )


# --- Progress -------------------------------------------------------------------------


def _sitting(day, *, finished=None, closed=True, trophy=None, perfect=False, due=7):
    opens = date(2026, 1, day)
    return progress.Sitting(
        opens_on=opens,
        due_on=date(2026, 1, day + due),
        closed=closed,
        finished_on=date(2026, 1, finished) if finished else None,
        trophy=trophy,
        perfect=perfect,
    )


def test_points_reward_finishing_promptly_and_trophies():
    assert progress.sitting_points(_sitting(1)) == 0
    assert progress.sitting_points(_sitting(1, finished=4)) == 125
    assert progress.sitting_points(_sitting(1, finished=6)) == 100
    assert progress.sitting_points(_sitting(1, finished=1, trophy="silver")) == 155


def test_levels_climb_with_points():
    assert progress.level_for(0) == {
        "level": 1,
        "name": "Newcomer",
        "floor": 0,
        "next": 150,
    }
    assert progress.level_for(400)["name"] == "Defender"
    assert progress.level_for(5000) == {
        "level": 7,
        "name": "Legend",
        "floor": 3000,
        "next": None,
    }


def test_a_run_of_rounds_ends_only_when_one_closes_unfinished():
    sittings = [
        _sitting(1, finished=2),
        _sitting(2, finished=8),
        _sitting(3, finished=3, trophy="gold", perfect=True),
        _sitting(4, closed=False),  # still open: the run waits
        _sitting(5, finished=5),
        _sitting(6),  # closed unfinished: the run ends
        _sitting(7, finished=7),
    ]
    summary = progress.progress(sittings)
    assert (summary["finished"], summary["streak"], summary["best_streak"]) == (5, 1, 4)
    earned = {b["key"] for b in summary["badges"] if b["earned"]}
    assert earned == {
        "first_quiz",
        "streak_3",
        "quizzes_5",
        "early_bird",
        "gold",
        "perfect",
    }
    assert progress.progress([])["badges"][0] == {
        "key": "first_quiz",
        "label": "First steps",
        "description": "Finish your first quiz.",
        "earned": False,
    }
    ten = progress.progress([_sitting(1, finished=3) for _ in range(10)])
    assert {b["key"] for b in ten["badges"] if b["earned"]} >= {"quizzes_10"}
