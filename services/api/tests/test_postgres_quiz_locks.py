"""A quiz round and the attempts handed in to it queue behind its row lock.

Handing in takes a shared lock on the round and closing it takes the row lock,
so a round that closes while someone hands in counts their attempt in the
average it records, and an attempt handed in while the round closes is
refused rather than missed. The changes run on separate PostgreSQL
connections, so these tests skip on SQLite.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace

from app.db.models import (
    IsmsEffectivenessMetricEntry,
    IsmsPerson,
    Quiz,
    QuizAttempt,
    User,
)
from app.services.quizzes import attempts, audience, authoring, rounds
from app.services.quizzes.schemas import (
    QuizAudienceInput,
    QuizQuestionsInput,
    QuizRoundInput,
    QuizRoundUpdate,
    QuizSettingsInput,
    QuizSubmitInput,
)
from app.services.schedules import today_for_schedule

# Imported fixture: two changes on separate connections, the first held at a
# chosen call while the second starts.
from tests.test_postgres_concurrent_replacement import race  # noqa: F401

QUESTION = {
    "kind": "choice",
    "prompt": "Report a lost laptop within?",
    "options": [{"text": "One hour", "correct": True}, {"text": "One week"}],
}


def _hand_in(db, attempt_id, user_id):
    view = attempts.my_attempt(attempt_id=attempt_id, db=db, user=db.get(User, user_id))
    question = view["questions"][0]
    right = next(o for o in question["options"] if o["text"] == "One hour")
    attempts.submit_attempt(
        attempt_id=attempt_id,
        payload=QuizSubmitInput(
            page=0, answers=[{"position": 0, "option_id": right["id"]}]
        ),
        db=db,
        user=db.get(User, user_id),
    )


def open_round(race):  # noqa: F811 - fixture imported above
    """An active quiz with an open round: two people have handed in and a
    third has started."""
    with race.session() as db:
        admin = db.get(User, race.user_id)
        quiz = authoring.create_quiz(
            payload=QuizSettingsInput(title="Locks", framework="fw-locks"),
            context=None,
            db=db,
            user=admin,
        )
        quiz_id = uuid.UUID(quiz["id"])
        authoring.replace_questions(
            quiz_id=quiz_id,
            payload=QuizQuestionsInput(questions=[QUESTION]),
            context=None,
            db=db,
            user=admin,
        )
        students = []
        for n in range(3):
            account = User(
                username=f"student-{n}-" + uuid.uuid4().hex[:8],
                password_hash="unused",
                is_active=True,
            )
            db.add(account)
            db.flush()
            person = IsmsPerson(name=f"Student {n}", user_id=account.id)
            db.add(person)
            db.flush()
            students.append((account.id, person.id))
        db.commit()
        audience.replace_audience(
            quiz_id=quiz_id,
            payload=QuizAudienceInput(person_ids=[person for _, person in students]),
            context=None,
            db=db,
            user=admin,
        )
        authoring.update_quiz(
            quiz_id=quiz_id,
            payload=QuizSettingsInput(status="active"),
            context=None,
            db=db,
            user=admin,
        )
        round_ = rounds.start_round(
            quiz_id=quiz_id,
            payload=QuizRoundInput(due_on=today_for_schedule() + timedelta(days=7)),
            db=db,
            user=admin,
        )
        round_id = uuid.UUID(round_["id"])
        started = {}
        for account_id, _ in students:
            view = attempts.start_attempt(
                round_id=round_id, db=db, user=db.get(User, account_id)
            )
            started[account_id] = uuid.UUID(view["id"])
        for account_id, _ in students[:2]:
            _hand_in(db, started[account_id], account_id)
        last = students[2][0]
        return SimpleNamespace(
            quiz_id=quiz_id,
            round_id=round_id,
            attempt_id=started[last],
            user_id=last,
        )


def _close(state):
    def close(db, user):
        rounds.update_round(
            quiz_id=state.quiz_id,
            round_id=state.round_id,
            payload=QuizRoundUpdate(status="closed"),
            db=db,
            user=user,
        )

    return close


def _recorded(race, state):  # noqa: F811 - fixture imported above
    with race.session() as db:
        quiz = db.get(Quiz, state.quiz_id)
        return [
            (entry.metric_value, entry.notes)
            for entry in db.query(IsmsEffectivenessMetricEntry).filter_by(
                measure_id=quiz.measure_id
            )
        ]


def test_closing_waits_for_an_attempt_being_handed_in(race):  # noqa: F811
    state = open_round(race)
    outcome = race.run(
        lambda db, user: _hand_in(db, state.attempt_id, state.user_id),
        _close(state),
        # The hand-in holds here, with the round's shared lock taken.
        pause_at=(attempts, "mark"),
    )
    assert outcome == ("done", "done")
    # The average counts all three attempts, the last included.
    assert _recorded(race, state) == [
        (100.0, "Average of 3 completed responses from 3 assigned people.")
    ]


def test_an_attempt_handed_in_while_its_round_closes_is_refused(race):  # noqa: F811
    state = open_round(race)
    outcome = race.run(
        _close(state),
        lambda db, user: _hand_in(db, state.attempt_id, state.user_id),
        # The close holds here, with the round locked and marked closed.
        pause_at=(rounds, "record_round_result"),
    )
    assert outcome == ("done", (409, "This quiz round has closed"))
    with race.session() as db:
        assert db.get(QuizAttempt, state.attempt_id).status == "in_progress"
    # Two completed responses stay with quiz.manage.
    assert _recorded(race, state) == []
