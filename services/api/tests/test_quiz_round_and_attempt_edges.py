"""Quiz attempts, invitations, results and rounds at their edges.

Covers a handed-in attempt after its person moves to another account, a start
that fails for a reason other than a racing start, invitations while email is
not configured, an option listed twice, questions left blank in the results,
schedule edits to an active one-off quiz, and a due round that another run
closes first. Requests pass through the assembled application unless a test
says otherwise.
"""

import uuid
from datetime import timedelta

import pytest
from app.db.models import Quiz, QuizAttempt, QuizParticipant, QuizRound
from app.services.quizzes import attempts as attempts_service
from app.services.quizzes import audience as audience_service
from app.services.quizzes import rounds as rounds_service
from app.services.schedules import today_for_schedule
from sqlalchemy import event as orm_event
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError

# Imported fixtures: the assembled application and a quiz workspace.
from tests.test_assembled_authority_regressions import keen  # noqa: F401
from tests.test_quizzes_api import QUESTIONS, sit, world  # noqa: F401

CHOICES = QUESTIONS[:2]


# --- Attempts ---------------------------------------------------------------------


def test_a_handed_in_attempt_follows_its_person_to_their_new_account(world):  # noqa: F811
    ada = world.students[0]
    quiz = world.ready(people=[ada], questions=CHOICES)
    round_ = world.open_round(quiz)
    handed_in = sit(world, ada, round_["id"])
    mover = world.keen.client(world.keen.user("isms.manage", "quiz.manage"))
    fresh = world.keen.user()
    moved = world.patch(
        mover,
        f"/v1/isms/people/{ada.person.id}",
        {"name": "Ada", "user_id": str(fresh.id)},
    )
    assert moved.status_code == 200, moved.text

    # Only an attempt in progress stays with the account that started it.
    again = world.post(
        world.keen.client(fresh), f"/v1/me/quizzes/rounds/{round_['id']}/attempt"
    )

    assert again.status_code == 200, again.text
    assert (again.json()["id"], again.json()["status"]) == (
        handed_in["id"],
        "complete",
    )
    world.db.expire_all()
    assert world.db.query(QuizAttempt).count() == 1


def test_a_start_that_fails_for_another_reason_saves_no_attempt(world):  # noqa: F811
    ada = world.students[0]
    quiz = world.ready(people=[ada], questions=CHOICES)
    round_ = world.open_round(quiz)
    db = world.db
    stored = db.get(Quiz, uuid.UUID(quiz["id"]))
    first, second = stored.questions
    # Another request deletes a question after this one has read the quiz.
    db.execute(text("DELETE FROM quiz_questions WHERE id = :id"), {"id": first.id})
    db.commit()

    with pytest.raises(
        IntegrityError,
        match='violates foreign key constraint "quiz_answers_question_id_fkey"',
    ):
        attempts_service.start_attempt(
            round_id=uuid.UUID(round_["id"]), db=db, user=ada.user
        )

    db.rollback()
    assert db.query(QuizAttempt).count() == 0
    # Once the quiz is read afresh, the start goes ahead with what remains.
    started = attempts_service.start_attempt(
        round_id=uuid.UUID(round_["id"]), db=db, user=ada.user
    )
    assert [q["prompt"] for q in started["questions"]] == [second.prompt]


# --- Invitations ------------------------------------------------------------------


def test_invitations_wait_until_email_is_configured(world, monkeypatch):  # noqa: F811
    ada = world.students[0]
    quiz = world.ready(people=[ada])
    monkeypatch.setattr(audience_service, "smtp_configured", lambda: False)
    round_ = world.open_round(quiz)

    waiting = rounds_service.invite_waiting(world.db)

    assert waiting == {"enabled": True, "invited": 0}
    world.sent.assert_not_called()
    world.db.expire_all()
    (participant,) = world.db.query(QuizParticipant).filter_by(
        round_id=uuid.UUID(round_["id"])
    )
    assert participant.invited_at is None
    # The daily job sends the invitation once email works.
    monkeypatch.setattr(audience_service, "smtp_configured", lambda: True)
    assert rounds_service.invite_waiting(world.db) == {"enabled": True, "invited": 1}
    assert [c.kwargs["to_email"] for c in world.sent.call_args_list] == [
        "ada@example.test"
    ]


# --- Authoring --------------------------------------------------------------------


def test_an_option_listed_twice_is_refused(world):  # noqa: F811
    quiz = world.create()
    path = f"/v1/quizzes/{quiz['id']}/questions"
    saved = world.put(world.manager, path, {"questions": CHOICES[:1]})
    assert saved.status_code == 200, saved.text
    (question,) = saved.json()["questions"]
    right, *_ = question["options"]

    refused = world.put(
        world.manager,
        path,
        {
            "questions": [
                {
                    "id": question["id"],
                    "kind": "choice",
                    "prompt": question["prompt"],
                    "options": [
                        {"id": right["id"], "text": "The asset owner", "correct": True},
                        {"id": right["id"], "text": "The data owner"},
                    ],
                }
            ]
        },
    )

    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        "An option is listed twice",
    )
    unchanged = world.manager.get(f"/v1/quizzes/{quiz['id']}").json()["questions"]
    assert unchanged == saved.json()["questions"]


# --- Results ----------------------------------------------------------------------


def test_results_count_a_question_left_blank_as_asked_but_unanswered(world):  # noqa: F811
    ada = world.students[0]
    quiz = world.ready(people=[ada], questions=CHOICES)
    round_ = world.open_round(quiz)
    client = world.student_client(ada)
    attempt = world.post(client, f"/v1/me/quizzes/rounds/{round_['id']}/attempt").json()
    handed_in = world.post(
        client,
        f"/v1/me/quizzes/attempts/{attempt['id']}/submit",
        {"page": 0, "answers": []},
    )
    assert handed_in.status_code == 200, handed_in.text

    results = world.manager.get(f"/v1/quizzes/{quiz['id']}/results").json()

    assert {q["prompt"]: (q["asked"], q["answered"]) for q in results["questions"]} == {
        question["prompt"]: (1, 0) for question in CHOICES
    }
    for question in results["questions"]:
        assert {o["chosen"] for o in question["options"]} == {0}
        assert {o["share"] for o in question["options"]} == {None}


# --- Rounds -----------------------------------------------------------------------


def test_a_schedule_edit_to_an_active_one_off_quiz_opens_no_round(world):  # noqa: F811
    quiz = world.ready(people=world.students[:1])
    assert quiz["rounds"] == []

    edited = world.patch(
        world.manager, f"/v1/quizzes/{quiz['id']}", {"schedule_interval": 2}
    )

    assert edited.status_code == 200, edited.text
    assert edited.json()["rounds"] == []
    assert world.queued == []
    world.db.expire_all()
    assert world.db.query(QuizRound).count() == 0


def test_a_due_round_another_run_closes_first_is_left_to_that_run(world):  # noqa: F811
    ada = world.students[0]
    quiz = world.ready(people=[ada])
    round_ = world.open_round(quiz, days=1)
    round_id = uuid.UUID(round_["id"])
    db = world.db
    closed_elsewhere = []

    def close_first(state):
        # Another run closes the round just before this one locks it.
        if (
            state.is_select
            and state.statement._for_update_arg is not None
            and not closed_elsewhere
        ):
            closed_elsewhere.append(round_id)
            db.execute(
                update(QuizRound)
                .where(QuizRound.id == round_id)
                .values(status="closed")
            )

    orm_event.listen(db, "do_orm_execute", close_first)
    try:
        result = rounds_service.close_due_quiz_rounds(
            db, today=today_for_schedule() + timedelta(days=3)
        )
    finally:
        orm_event.remove(db, "do_orm_execute", close_first)

    assert closed_elsewhere == [round_id]
    assert result == {"enabled": True, "closed": []}
    db.expire_all()
    # This run neither closes it again nor stamps its closing time.
    stored = db.get(QuizRound, round_id)
    assert (stored.status, stored.closed_at) == ("closed", None)
