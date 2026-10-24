"""Quizzes through the assembled application.

quiz.manage writes, schedules and grades quizzes; quiz.read sees quizzes and
results that describe a group; every signed-in account sits the rounds
assigned to its People record. A round's average reaches the quiz's
effectiveness measure, and so its controls, only once the round has closed
with every response marked and at least three people finished.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.db.models import (
    ControlItem,
    EntityChangelog,
    Framework,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    IsmsEntityControlLink,
    IsmsOrgNode,
    IsmsOrgNodeUser,
    IsmsPerson,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizParticipant,
    QuizRound,
)
from app.services import features
from app.services.quizzes import audience as audience_service
from app.services.quizzes import rounds as rounds_service
from app.services.schedules import today_for_schedule
from app.worker import tasks

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401

FW = "fw-quizzes"


# The right answer to each multiple-choice prompt the tests write.
ANSWER_KEY = {}


def choice(prompt, right, *wrong, explanation=""):
    ANSWER_KEY[prompt] = right
    return {
        "kind": "choice",
        "prompt": prompt,
        "explanation": explanation,
        "options": [{"text": right, "correct": True}]
        + [{"text": text, "correct": False} for text in wrong],
    }


def written(prompt, guidance=""):
    return {"kind": "text", "prompt": prompt, "explanation": guidance, "options": []}


QUESTIONS = [
    choice("Who may approve access?", "The asset owner", "Anyone", "The requester"),
    choice("Report a lost laptop within?", "One hour", "One week"),
    written("Describe a phishing sign.", "Urgency, mismatched sender, odd links."),
]


@pytest.fixture
def world(keen, monkeypatch):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add_all([Framework(slug=FW), Framework(slug="fw-elsewhere")])
    access = ControlItem(framework_slug=FW, ref="A.5.15", type="annex_control")
    training = ControlItem(framework_slug=FW, ref="A.6.3", type="annex_control")
    foreign = ControlItem(
        framework_slug="fw-elsewhere", ref="CC1.4", type="annex_control"
    )
    db.add_all([access, training, foreign])
    db.commit()

    manager_user = keen.user("quiz.manage")
    reader_user = keen.user("quiz.read")
    students = []
    for name in ("Ada", "Ben", "Cleo", "Dev"):
        account = keen.user()
        person = IsmsPerson(
            name=name, email=f"{name.lower()}@example.test", user_id=account.id
        )
        db.add(person)
        students.append(SimpleNamespace(user=account, person=person, name=name))
    db.commit()

    sent = Mock()
    monkeypatch.setattr(audience_service, "smtp_configured", lambda: True)
    monkeypatch.setattr(audience_service, "send_quiz_open_email", sent)
    # Requests queue invitations for the worker; deliver() runs them as the
    # worker would, once the request has finished.
    queued = []
    monkeypatch.setattr(tasks.send_quiz_invitations_task, "delay", queued.append)

    def deliver():
        while queued:
            round_ids = [uuid.UUID(rid) for rid in queued.pop(0)]
            rounds_service.send_quiz_invitations(db, round_ids)

    manager = keen.client(manager_user)

    def create(client=manager, **overrides):
        payload = {"title": "Security basics", "framework": FW, **overrides}
        response = client.post("/v1/quizzes", json=payload, headers=unsafe(client))
        assert response.status_code == 201, response.text
        return response.json()

    def put(client, path, payload):
        return client.put(path, json=payload, headers=unsafe(client))

    def patch(client, path, payload):
        return client.patch(path, json=payload, headers=unsafe(client))

    def post(client, path, payload=None):
        return client.post(path, json=payload or {}, headers=unsafe(client))

    def ready(questions=QUESTIONS, people=None, started=False, **overrides):
        """An active quiz with questions, assigned to the given people.

        Activating a quiz opens the round it is due now; unless ``started``,
        the fixture removes that round so each test starts the rounds it needs.
        """
        quiz = create(**overrides)
        saved = put(
            manager, f"/v1/quizzes/{quiz['id']}/questions", {"questions": questions}
        )
        assert saved.status_code == 200, saved.text
        people = students if people is None else people
        assigned = put(
            manager,
            f"/v1/quizzes/{quiz['id']}/audience",
            {"person_ids": [str(s.person.id) for s in people]},
        )
        assert assigned.status_code == 200, assigned.text
        active = patch(manager, f"/v1/quizzes/{quiz['id']}", {"status": "active"})
        assert active.status_code == 200, active.text
        if started:
            return active.json()
        for round_ in active.json()["rounds"]:
            removed = manager.delete(
                f"/v1/quizzes/{quiz['id']}/rounds/{round_['id']}",
                headers=unsafe(manager),
            )
            assert removed.status_code == 200, removed.text
        queued.clear()
        return manager.get(f"/v1/quizzes/{quiz['id']}").json()

    def open_round(quiz, days=7):
        due = (today_for_schedule() + timedelta(days=days)).isoformat()
        response = post(manager, f"/v1/quizzes/{quiz['id']}/rounds", {"due_on": due})
        assert response.status_code == 201, response.text
        # The request queued the round's invitations for the worker.
        deliver()
        return response.json()

    def student_client(student):
        return keen.client(student.user)

    return SimpleNamespace(
        keen=keen,
        db=db,
        access=access,
        training=training,
        foreign=foreign,
        manager=manager,
        manager_user=manager_user,
        reader=keen.client(reader_user),
        students=students,
        sent=sent,
        queued=queued,
        deliver=deliver,
        create=create,
        put=put,
        patch=patch,
        post=post,
        ready=ready,
        open_round=open_round,
        student_client=student_client,
    )


def sit(world, student, round_id, *, choose=None, texts=None):
    """Sit a round as a student: answer every question and hand it in.

    ``choose`` maps a prompt to the option text to pick (the right answer when
    omitted); ``texts`` maps a prompt to a written answer.
    """
    client = world.student_client(student)
    started = world.post(client, f"/v1/me/quizzes/rounds/{round_id}/attempt")
    assert started.status_code == 200, started.text
    attempt = started.json()
    answers = []
    for question in attempt["questions"]:
        if question["kind"] == "choice":
            wanted = (choose or {}).get(question["prompt"]) or _right_answer(
                question["prompt"]
            )
            option = next(o for o in question["options"] if o["text"] == wanted)
            answers.append(
                {"position": question["position"], "option_id": option["id"]}
            )
        else:
            answers.append(
                {
                    "position": question["position"],
                    "text": (texts or {}).get(question["prompt"], "Urgent requests"),
                }
            )
    last = attempt["page_count"] - 1
    for page in range(last):
        on_page = [
            a
            for a in answers
            if page * attempt["page_size"]
            <= a["position"]
            < (page + 1) * attempt["page_size"]
        ]
        saved = world.put(
            client,
            f"/v1/me/quizzes/attempts/{attempt['id']}/pages/{page}",
            {"answers": on_page},
        )
        assert saved.status_code == 200, saved.text
    final = [a for a in answers if a["position"] >= last * attempt["page_size"]]
    submitted = world.post(
        client,
        f"/v1/me/quizzes/attempts/{attempt['id']}/submit",
        {"page": last, "answers": final},
    )
    assert submitted.status_code == 200, submitted.text
    return submitted.json()


def _right_answer(prompt):
    return ANSWER_KEY[prompt]


def grade_all(world, quiz_id, grade=4):
    queue = world.manager.get(f"/v1/quizzes/{quiz_id}/grading").json()
    for item in queue["items"]:
        response = world.put(
            world.manager,
            f"/v1/quizzes/{quiz_id}/answers/{item['answer_id']}/grade",
            {"grade": grade, "feedback": "Good"},
        )
        assert response.status_code == 200, response.text
    return queue


# --- Smoke ---------------------------------------------------------------------


def test_a_quiz_runs_from_creation_to_a_recorded_result(world):
    quiz = world.ready(controls=["A.6.3"], pass_mark=60, show_scores=True)
    assert quiz["status"] == "active"
    assert [c["ref"] for c in quiz["controls"]] == ["A.6.3"]
    round_ = world.open_round(quiz)
    assert round_["participants"] == 4
    for student in world.students[:3]:
        sit(world, student, round_["id"])
    grade_all(world, quiz["id"], grade=4)
    closed = world.patch(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/rounds/{round_['id']}",
        {"status": "closed"},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["recorded"] is True
    world.db.expire_all()
    entries = world.db.query(IsmsEffectivenessMetricEntry).all()
    assert [(e.metric_value, e.metric_unit) for e in entries] == [(93.33, "%")]


# --- Permissions and availability ---------------------------------------------


def test_reading_and_managing_follow_the_two_permissions(world):
    nobody = world.keen.client(world.keen.user())
    reader = world.reader
    assert nobody.get(f"/v1/quizzes?framework={FW}").status_code == 403
    refused = world.post(nobody, "/v1/quizzes", {"title": "No", "framework": FW})
    assert refused.status_code == 403
    assert refused.json()["detail"] == "quiz.manage permission required"
    assert reader.get(f"/v1/quizzes?framework={FW}").status_code == 200
    assert (
        world.post(reader, "/v1/quizzes", {"title": "No", "framework": FW}).status_code
        == 403
    )
    for path in ("/v1/quizzes/assignees",):
        assert reader.get(path).status_code == 403

    quiz = world.ready(pass_mark=70)
    round_ = world.open_round(quiz)
    sit(world, world.students[0], round_["id"])
    attempt_id = world.db.query(QuizAttempt.id).scalar()
    for path in (
        f"/v1/quizzes/{quiz['id']}/grading",
        f"/v1/quizzes/{quiz['id']}/attempts/{attempt_id}",
    ):
        assert reader.get(path).status_code == 403
        assert nobody.get(path).status_code == 403
    for path in (f"/v1/quizzes/{quiz['id']}", f"/v1/quizzes/{quiz['id']}/results"):
        assert nobody.get(path).status_code == 403
    assert (
        world.patch(reader, f"/v1/quizzes/{quiz['id']}", {"title": "X"}).status_code
        == 403
    )

    # Readers see the questions without their answers, and only the size of
    # the audience.
    seen = reader.get(f"/v1/quizzes/{quiz['id']}").json()
    assert seen["can_manage"] is False
    assert seen["audience"] == {"size": 4}
    for question in seen["questions"]:
        assert "explanation" not in question
        assert all(set(option) == {"id", "text"} for option in question["options"])
    # Alphabetical, where the manager wrote the right answer first.
    assert [o["text"] for o in seen["questions"][0]["options"]] == [
        "Anyone",
        "The asset owner",
        "The requester",
    ]
    managed = world.manager.get(f"/v1/quizzes/{quiz['id']}").json()
    assert managed["can_manage"] is True
    assert {o["correct"] for q in managed["questions"] for o in q["options"]} == {
        True,
        False,
    }
    assert managed["audience"]["size"] == 4


def test_me_reports_quiz_permissions_and_the_feature_switches_quizzes_off(world):
    assert world.reader.get("/v1/me").json()["can_view_quizzes"] is True
    assert world.reader.get("/v1/me").json()["can_manage_quizzes"] is False
    assert world.manager.get("/v1/me").json()["can_manage_quizzes"] is True
    student = world.student_client(world.students[0])
    me = student.get("/v1/me").json()
    assert (me["can_view_quizzes"], me["can_manage_quizzes"]) == (False, False)
    assert student.get("/v1/me/quizzes").status_code == 200

    features.update(world.db, world.keen.user(role="admin"), 0, {"quizzes": False})
    assert world.manager.get(f"/v1/quizzes?framework={FW}").status_code == 404
    assert student.get("/v1/me/quizzes").status_code == 404
    assert world.manager.get("/v1/me").json()["can_manage_quizzes"] is False
    assert rounds_service.materialize_due_quiz_rounds(world.db)["enabled"] is False
    assert rounds_service.close_due_quiz_rounds(world.db)["enabled"] is False
    assert rounds_service.invite_waiting(world.db) == {"enabled": False, "invited": 0}


# --- Writing a quiz --------------------------------------------------------------


def test_a_new_quiz_is_a_draft_with_its_own_measure(world):
    refused = world.post(
        world.manager,
        "/v1/quizzes",
        {"title": "X", "framework": FW, "status": "active"},
    )
    assert refused.status_code == 400
    assert refused.json()["detail"] == "A new quiz starts as a draft"
    # A title reaches email subjects and calendar entries, so it keeps to a line.
    folded = world.post(
        world.manager, "/v1/quizzes", {"title": "Phishing\r\n  basics", "framework": FW}
    )
    assert folded.json()["title"] == "Phishing basics"
    untitled = world.post(world.manager, "/v1/quizzes", {"framework": FW})
    assert untitled.status_code == 400
    assert (
        world.post(
            world.manager, "/v1/quizzes", {"title": "  ", "framework": FW}
        ).status_code
        == 400
    )
    unknown = world.post(
        world.manager,
        "/v1/quizzes",
        {"title": "X", "framework": FW, "controls": ["CC1.4"]},
    )
    assert unknown.status_code == 400
    assert unknown.json()["detail"] == "Unknown control: CC1.4"

    quiz = world.create(
        description="Monthly refresher", pass_mark=70, controls=["A.5.15", "A.6.3"]
    )
    assert (quiz["status"], quiz["page_size"], quiz["reveal_answers"]) == (
        "draft",
        5,
        "after_close",
    )
    assert (quiz["show_scores"], quiz["due_days"], quiz["schedule"]["label"]) == (
        False,
        14,
        "",
    )
    world.db.expire_all()
    measure = world.db.get(IsmsEffectivenessMeasure, uuid.UUID(quiz["measure_id"]))
    assert (measure.summary, measure.target_value, measure.threshold_operator) == (
        "Quiz: Security basics",
        70.0,
        "gte",
    )
    assert (measure.target_unit, measure.frequency, measure.metric_key) == (
        "%",
        "Each round",
        None,
    )
    links = world.db.query(IsmsEntityControlLink).filter_by(entity_id=measure.id).all()
    assert {link.control_item_id for link in links} == {
        world.access.id,
        world.training.id,
    }

    changed = world.patch(
        world.manager,
        f"/v1/quizzes/{quiz['id']}",
        {
            "title": "Security refresher",
            "pass_mark": None,
            "controls": ["A.6.3"],
            "start_date": today_for_schedule().isoformat(),
            "schedule_recurrence": "monthly",
            "schedule_interval": 2,
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["schedule"]["label"] == "Every 2 months"
    assert [c["ref"] for c in changed.json()["controls"]] == ["A.6.3"]
    world.db.expire_all()
    measure = world.db.get(IsmsEffectivenessMeasure, measure.id)
    assert (measure.summary, measure.target_value, measure.threshold_operator) == (
        "Quiz: Security refresher",
        None,
        "",
    )
    assert measure.frequency == "Every 2 months"
    framework = world.patch(
        world.manager, f"/v1/quizzes/{quiz['id']}", {"framework": "fw-elsewhere"}
    )
    assert framework.status_code == 400
    for cleared in ("title", "page_size", "status"):
        response = world.patch(
            world.manager, f"/v1/quizzes/{quiz['id']}", {cleared: None}
        )
        assert response.status_code == 400, cleared
        assert response.json()["detail"] == f"{cleared} is required"


def test_a_quiz_measure_changes_only_through_its_quiz(world):
    from app.services import webhooks
    from app.services.context import ChangeContext
    from app.services.errors import DomainError

    quiz = world.create(controls=["A.6.3"])
    isms = world.keen.client(world.keen.user("isms.manage"))
    path = f"/v1/isms/effectiveness-measures/{quiz['measure_id']}?framework={FW}"
    shown = isms.get(path).json()
    assert shown["quiz"] == {"id": quiz["id"], "title": "Security basics"}
    for response in (
        world.patch(isms, path, {"summary": "Renamed"}),
        isms.delete(path, headers=unsafe(isms)),
        world.post(
            isms,
            f"/v1/isms/effectiveness-measures/{quiz['measure_id']}/metrics?framework={FW}",
            {"metric_value": 99},
        ),
    ):
        assert response.status_code == 409
        assert (
            response.json()["detail"]
            == "This measure belongs to a quiz; change it from the quiz"
        )
    with pytest.raises(DomainError) as refused:
        webhooks.record_metric(
            db=world.db,
            metric_key=quiz["measure_id"],
            payload={"framework": FW, "value": 50},
            context=ChangeContext(),
        )
    assert refused.value.status_code == 409


def test_activating_needs_questions_and_questions_are_checked(world):
    quiz = world.create()
    path = f"/v1/quizzes/{quiz['id']}"
    activate = world.patch(world.manager, path, {"status": "active"})
    assert activate.status_code == 409
    assert activate.json()["detail"] == "Add at least one question before activating"
    cases = [
        (
            [
                {
                    "kind": "choice",
                    "prompt": "Q",
                    "options": [{"text": "A", "correct": True}],
                }
            ],
            "Multiple-choice question 1 needs at least 2 options",
        ),
        (
            [
                choice("Q", "A", "B")
                | {
                    "options": [
                        {"text": "A", "correct": True},
                        {"text": "B", "correct": True},
                    ]
                }
            ],
            "Multiple-choice question 1 needs exactly one correct option",
        ),
        (
            [
                choice("Q", "A", "B")
                | {"options": [{"text": "A", "correct": False}, {"text": "B"}]}
            ],
            "Multiple-choice question 1 needs exactly one correct option",
        ),
        (
            [written("W"), choice("Q", "Same", "same ")],
            "The options of question 2 repeat themselves",
        ),
        (
            [written("W") | {"options": [{"text": "A"}]}],
            "Written question 1 takes no options",
        ),
        ([written("  ")], "question 1 is required"),
        ([written("W") | {"id": str(uuid.uuid4())}], "Unknown question"),
    ]
    for questions, detail in cases:
        response = world.put(
            world.manager, f"{path}/questions", {"questions": questions}
        )
        assert response.status_code == 400, (questions, response.text)
        assert response.json()["detail"] == detail

    saved = world.put(
        world.manager, f"{path}/questions", {"questions": QUESTIONS}
    ).json()
    assert [q["position"] for q in saved["questions"]] == [0, 1, 2]
    first, second, third = saved["questions"]
    # Saving with ids keeps questions and options; the rest are removed.
    edited = dict(QUESTIONS[0])
    edited["id"] = first["id"]
    edited["prompt"] = "Who approves access?"
    edited["options"] = [
        {"id": first["options"][0]["id"], "text": "The asset owner", "correct": True},
        {"text": "The help desk", "correct": False},
    ]
    resaved = world.put(
        world.manager,
        f"{path}/questions",
        {"questions": [QUESTIONS[2] | {"id": third["id"]}, edited]},
    ).json()
    assert [q["id"] for q in resaved["questions"]] == [third["id"], first["id"]]
    assert resaved["questions"][1]["prompt"] == "Who approves access?"
    assert [o["text"] for o in resaved["questions"][1]["options"]] == [
        "The asset owner",
        "The help desk",
    ]
    assert resaved["questions"][1]["options"][0]["id"] == first["options"][0]["id"]
    assert world.db.query(Quiz).count() == 1
    stray = world.put(
        world.manager,
        f"{path}/questions",
        {
            "questions": [
                edited
                | {
                    "options": [
                        {
                            "id": second["options"][0]["id"],
                            "text": "X",
                            "correct": True,
                        },
                        {"text": "Y"},
                    ]
                }
            ]
        },
    )
    assert stray.status_code == 400
    assert stray.json()["detail"] == "Unknown option"
    twice = world.put(
        world.manager, f"{path}/questions", {"questions": [edited, edited]}
    )
    assert twice.json()["detail"] == "A question is listed twice"

    assert world.patch(world.manager, path, {"status": "active"}).status_code == 200
    emptied = world.put(world.manager, f"{path}/questions", {"questions": []})
    assert emptied.status_code == 409
    assert emptied.json()["detail"] == "An active quiz needs at least one question"


def test_the_change_history_leaves_out_the_answer_key(world):
    quiz = world.ready()
    world.db.expire_all()
    rows = world.db.query(EntityChangelog).filter_by(entity_type="quiz").all()
    assert {row.action for row in rows} == {"created", "updated"}
    assert all("correct" not in str(row.after_state or {}) for row in rows)
    latest = max(rows, key=lambda row: row.changed_at)
    assert latest.after_state["status"] == "active"
    # Alphabetical, so the history gives away no answer by order.
    assert latest.after_state["questions"][0]["options"] == [
        "Anyone",
        "The asset owner",
        "The requester",
    ]
    assert latest.after_state["people"] == ["Ada", "Ben", "Cleo", "Dev"]
    assert quiz["status"] == "active"


def test_a_quiz_people_have_sat_cannot_be_deleted(world):
    draft = world.create(controls=["A.6.3"])
    deleted = world.manager.delete(
        f"/v1/quizzes/{draft['id']}", headers=unsafe(world.manager)
    )
    assert deleted.status_code == 200
    world.db.expire_all()
    assert (
        world.db.get(IsmsEffectivenessMeasure, uuid.UUID(draft["measure_id"])) is None
    )
    assert world.db.query(IsmsEntityControlLink).count() == 0

    quiz = world.ready()
    round_ = world.open_round(quiz)
    sit(world, world.students[0], round_["id"])
    refused = world.manager.delete(
        f"/v1/quizzes/{quiz['id']}", headers=unsafe(world.manager)
    )
    assert refused.status_code == 409
    assert (
        refused.json()["detail"]
        == "People have sat this quiz; archive it to keep their results"
    )
    round_refused = world.manager.delete(
        f"/v1/quizzes/{quiz['id']}/rounds/{round_['id']}", headers=unsafe(world.manager)
    )
    assert round_refused.status_code == 409
    other = world.open_round(quiz)
    assert (
        world.manager.delete(
            f"/v1/quizzes/{quiz['id']}/rounds/{other['id']}",
            headers=unsafe(world.manager),
        ).status_code
        == 200
    )
    missing = world.manager.get(f"/v1/quizzes/{uuid.uuid4()}")
    assert missing.status_code == 404


def test_archiving_closes_open_rounds_and_draft_is_only_for_new_quizzes(world):
    quiz = world.ready()
    round_ = world.open_round(quiz)
    path = f"/v1/quizzes/{quiz['id']}"
    draft = world.patch(world.manager, path, {"status": "draft"})
    assert draft.status_code == 409
    archived = world.patch(world.manager, path, {"status": "archived"}).json()
    assert archived["status"] == "archived"
    assert [r["status"] for r in archived["rounds"]] == ["closed"]
    reopened = world.patch(
        world.manager, f"{path}/rounds/{round_['id']}", {"status": "open"}
    )
    assert reopened.status_code == 409
    assert reopened.json()["detail"] == "Activate the quiz before reopening a round"
    refused = world.post(
        world.manager, f"{path}/rounds", {"due_on": today_for_schedule().isoformat()}
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Activate the quiz before starting a round"
    assert world.patch(world.manager, path, {"status": "active"}).status_code == 200


def _availability(world, quiz, client=None):
    client = client or world.manager
    return client.get(f"/v1/quizzes/{quiz['id']}").json()["availability"]


def test_activating_a_one_off_quiz_opens_it_for_its_days_to_answer(world):
    ada, ben = world.students[:2]
    today = today_for_schedule()
    quiz = world.create(due_days=5)
    path = f"/v1/quizzes/{quiz['id']}"
    # A draft says what activating it will do.
    assert _availability(world, quiz) == {
        "state": "draft",
        "on_activation": "now",
        "until": (today + timedelta(days=5)).isoformat(),
    }
    world.put(world.manager, f"{path}/questions", {"questions": QUESTIONS[:2]})
    world.put(
        world.manager,
        f"{path}/audience",
        {"person_ids": [str(ada.person.id), str(ben.person.id)]},
    )
    active = world.patch(world.manager, path, {"status": "active"}).json()
    [round_] = active["rounds"]
    assert (round_["opens_on"], round_["due_on"], round_["scheduled"]) == (
        today.isoformat(),
        (today + timedelta(days=5)).isoformat(),
        False,
    )
    assert active["availability"] == {
        "state": "open",
        "until": (today + timedelta(days=5)).isoformat(),
        "people": 2,
    }
    # Its people are invited through the worker.
    assert world.queued == [[round_["id"]]]
    world.deliver()
    assert sorted(c.kwargs["to_email"] for c in world.sent.call_args_list) == [
        "ada@example.test",
        "ben@example.test",
    ]
    # Readers see the same, and the list says it in short.
    assert _availability(world, quiz, world.reader)["state"] == "open"
    listed = world.manager.get(f"/v1/quizzes?framework={FW}").json()["items"]
    assert [item["availability"]["state"] for item in listed] == ["open"]

    # Once its round closes, no one can take it until a manager starts one.
    _close(world, quiz, round_)
    assert _availability(world, quiz) == {"state": "no_round"}
    soon = (today + timedelta(days=2)).isoformat()
    world.post(world.manager, f"{path}/rounds", {"opens_on": soon})
    assert _availability(world, quiz) == {"state": "waiting", "opens_on": soon}
    # A round started now opens today, due after the quiz's days to answer.
    started = world.post(world.manager, f"{path}/rounds", {})
    assert started.status_code == 201, started.text
    assert (started.json()["opens_on"], started.json()["due_on"]) == (
        today.isoformat(),
        (today + timedelta(days=5)).isoformat(),
    )
    assert _availability(world, quiz)["state"] == "open"
    world.patch(world.manager, path, {"status": "archived"})
    assert _availability(world, quiz) == {"state": "archived"}


def test_a_scheduled_quiz_waits_for_its_first_round_date(world):
    today = today_for_schedule()
    later = today + timedelta(days=10)
    quiz = world.create(start_date=later.isoformat(), schedule_recurrence="monthly")
    path = f"/v1/quizzes/{quiz['id']}"
    assert _availability(world, quiz) == {
        "state": "draft",
        "on_activation": "scheduled",
        "opens_on": later.isoformat(),
    }
    world.put(world.manager, f"{path}/questions", {"questions": QUESTIONS[:2]})
    active = world.patch(world.manager, path, {"status": "active"}).json()
    assert active["rounds"] == []
    assert active["availability"] == {"state": "waiting", "opens_on": later.isoformat()}
    # Moving the first round to today opens it straight away.
    moved = world.patch(world.manager, path, {"start_date": today.isoformat()}).json()
    [round_] = moved["rounds"]
    assert (round_["opens_on"], round_["scheduled"]) == (today.isoformat(), True)
    assert moved["availability"]["state"] == "open"
    assert world.queued == [[round_["id"]]]

    # A schedule whose only date has passed leaves activating nothing to open.
    missed = world.create(
        title="Missed",
        start_date=(today - timedelta(days=3)).isoformat(),
        schedule_recurrence="once",
    )
    assert _availability(world, missed) == {
        "state": "draft",
        "on_activation": "nothing",
    }


# --- Who a quiz is for -------------------------------------------------------------


def test_assigning_accounts_and_roles_gives_everyone_a_people_record(world):
    db = world.db
    loner = world.keen.user()  # an account with no People record
    holder = world.keen.user()  # holds the role, no People record
    placed = world.keen.user()
    retired = world.keen.user()
    retired.is_active = False
    role = IsmsOrgNode(name="Service desk", node_type="team")
    db.add(role)
    db.flush()
    known = world.keen.user()  # holds the role and already has a People record
    db.add(IsmsOrgNodeUser(org_node_id=role.id, user_id=holder.id))
    db.add(IsmsOrgNodeUser(org_node_id=role.id, user_id=known.id))
    db.add(IsmsPerson(name="Known Kim", email="kim@example.test", user_id=known.id))
    db.add(
        IsmsPerson(
            name="Placed Pat",
            email="pat@example.test",
            user_id=placed.id,
            org_node_id=role.id,
        )
    )
    db.add(
        IsmsPerson(name="No Account", email="none@example.test", org_node_id=role.id)
    )
    outsider = IsmsPerson(name="Visitor", email="visitor@example.test")
    db.add(outsider)
    db.commit()

    listed = world.manager.get("/v1/quizzes/assignees").json()
    usernames = {a["username"] for a in listed["accounts"]}
    assert {loner.username, holder.username} <= usernames
    assert placed.username not in usernames and retired.username not in usernames
    assert [(r["name"], r["holders"]) for r in listed["roles"]] == [("Service desk", 4)]
    assert {p["name"]: p["can_sit"] for p in listed["people"]}["Visitor"] is False

    quiz = world.ready(questions=QUESTIONS[:1], people=[])
    path = f"/v1/quizzes/{quiz['id']}/audience"
    assert (
        world.put(world.manager, path, {"user_ids": [str(retired.id)]}).status_code
        == 400
    )
    assert (
        world.put(world.manager, path, {"person_ids": [str(uuid.uuid4())]}).status_code
        == 400
    )
    assert (
        world.put(
            world.manager, path, {"org_node_ids": [str(uuid.uuid4())]}
        ).status_code
        == 400
    )
    saved = world.put(
        world.manager,
        path,
        {
            "person_ids": [str(world.students[0].person.id), str(outsider.id)],
            "user_ids": [str(loner.id)],
            "org_node_ids": [str(role.id)],
        },
    )
    assert saved.status_code == 200, saved.text
    audience = saved.json()
    db.expire_all()
    created = db.query(IsmsPerson).filter_by(user_id=loner.id).one()
    assert (created.name, created.email) == (loner.username, "")
    names = {p["name"]: p["can_sit"] for p in audience["people"]}
    assert names == {"Ada": True, "Visitor": False, loner.username: True}
    assert audience["roles"] == [
        {"id": str(role.id), "name": "Service desk", "holders": 4}
    ]
    # Ada, the assigned account, Placed Pat, Known Kim and the role-holding
    # account without a People record.
    assert audience["size"] == 5

    round_ = world.open_round(quiz)
    assert round_["participants"] == 5
    db.expire_all()
    holder_person = db.query(IsmsPerson).filter_by(user_id=holder.id).one()
    participants = {
        p.person_id
        for p in db.query(QuizParticipant).filter_by(round_id=uuid.UUID(round_["id"]))
    }
    assert holder_person.id in participants
    assert outsider.id not in participants


def test_changing_the_audience_updates_open_rounds_and_invites_newcomers(world):
    ada, ben, cleo, dev = world.students
    quiz = world.ready(people=[ada, ben])
    round_ = world.open_round(quiz)
    invited = sorted(call.kwargs["to_email"] for call in world.sent.call_args_list)
    assert invited == ["ada@example.test", "ben@example.test"]
    call = world.sent.call_args_list[0].kwargs
    assert (
        call["quiz_url"] == f"https://keen.example/quiz-take.html?round={round_['id']}"
    )
    assert call["uid"] == f"keen-quiz-round-{round_['id']}"
    # Ben starts; then the audience swaps Ada and Ben for Cleo and Dev.
    client = world.student_client(ben)
    assert (
        world.post(client, f"/v1/me/quizzes/rounds/{round_['id']}/attempt").status_code
        == 200
    )
    world.sent.reset_mock()
    world.put(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/audience",
        {"person_ids": [str(cleo.person.id), str(dev.person.id)]},
    )
    # The newcomers' round goes to the worker.
    assert world.queued == [[round_["id"]]]
    world.deliver()
    assert sorted(c.kwargs["to_email"] for c in world.sent.call_args_list) == [
        "cleo@example.test",
        "dev@example.test",
    ]
    world.db.expire_all()
    people = {
        p.person_id
        for p in world.db.query(QuizParticipant).filter_by(
            round_id=uuid.UUID(round_["id"])
        )
    }
    # Ben keeps his place because he has started; Ada leaves.
    assert people == {ben.person.id, cleo.person.id, dev.person.id}
    assert (
        world.post(
            world.student_client(ada), f"/v1/me/quizzes/rounds/{round_['id']}/attempt"
        ).status_code
        == 404
    )


def test_invitations_skip_people_without_an_address_and_wait_for_smtp(
    world, monkeypatch
):
    ada, ben = world.students[:2]
    ben.person.email = "not an address"
    ben.user.email = None
    world.db.commit()
    quiz = world.ready(people=[ada, ben])
    world.open_round(quiz)
    assert [c.kwargs["to_email"] for c in world.sent.call_args_list] == [
        "ada@example.test"
    ]
    world.db.expire_all()
    invited = {p.person_id: p.invited_at for p in world.db.query(QuizParticipant)}
    assert invited[ada.person.id] is not None and invited[ben.person.id] is None

    monkeypatch.setattr(audience_service, "smtp_configured", lambda: False)
    world.sent.reset_mock()
    world.open_round(quiz)
    world.sent.assert_not_called()


def test_a_queue_that_is_down_leaves_invitations_to_the_daily_job(world, monkeypatch):
    ada, ben = world.students[:2]
    quiz = world.ready(people=[ada, ben])

    def broken(round_ids):
        raise ConnectionError("broker down")

    log = Mock()
    monkeypatch.setattr(tasks.send_quiz_invitations_task, "delay", broken)
    monkeypatch.setattr(audience_service, "log", log)
    world.sent.reset_mock()
    due = (today_for_schedule() + timedelta(days=7)).isoformat()
    started = world.post(
        world.manager, f"/v1/quizzes/{quiz['id']}/rounds", {"due_on": due}
    )
    # The round starts all the same; the daily job sends what the queue missed.
    assert started.status_code == 201
    world.sent.assert_not_called()
    log.exception.assert_called_once_with("Could not queue quiz invitations")
    # Ben's account is deactivated meanwhile: nobody emails a person who
    # cannot sign in.
    ben.user.is_active = False
    world.db.commit()
    assert rounds_service.invite_waiting(world.db) == {"enabled": True, "invited": 1}
    assert [c.kwargs["to_email"] for c in world.sent.call_args_list] == [
        "ada@example.test"
    ]
    assert rounds_service.invite_waiting(world.db)["invited"] == 0


# --- Rounds -----------------------------------------------------------------------


def test_round_dates_are_checked_and_moving_the_due_date_reminds_again(world):
    quiz = world.ready()
    path = f"/v1/quizzes/{quiz['id']}/rounds"
    today = today_for_schedule()
    past = world.post(
        world.manager,
        path,
        {
            "opens_on": (today - timedelta(days=1)).isoformat(),
            "due_on": today.isoformat(),
        },
    )
    assert past.status_code == 400
    assert past.json()["detail"] == "opens_on cannot be in the past"
    early = world.post(
        world.manager,
        path,
        {
            "opens_on": (today + timedelta(days=3)).isoformat(),
            "due_on": (today + timedelta(days=1)).isoformat(),
        },
    )
    assert early.json()["detail"] == "due_on cannot be before opens_on"
    round_ = world.open_round(quiz, days=5)
    assert (round_["status"], round_["scheduled"], round_["participants"]) == (
        "open",
        False,
        4,
    )
    world.db.query(QuizParticipant).update({QuizParticipant.reminded_at: today})
    world.db.commit()
    moved = world.patch(
        world.manager,
        f"{path}/{round_['id']}",
        {"due_on": (today + timedelta(days=9)).isoformat()},
    )
    assert moved.status_code == 200
    assert moved.json()["due_on"] == (today + timedelta(days=9)).isoformat()
    world.db.expire_all()
    assert {p.reminded_at for p in world.db.query(QuizParticipant)} == {None}
    # Saving the same due date keeps the reminders already sent.
    world.db.query(QuizParticipant).update({QuizParticipant.reminded_at: today})
    world.db.commit()
    world.patch(
        world.manager,
        f"{path}/{round_['id']}",
        {"due_on": (today + timedelta(days=9)).isoformat()},
    )
    world.db.expire_all()
    assert {p.reminded_at.date() for p in world.db.query(QuizParticipant)} == {today}
    before = world.patch(
        world.manager,
        f"{path}/{round_['id']}",
        {"due_on": (today - timedelta(days=30)).isoformat()},
    )
    assert before.json()["detail"] == "due_on cannot be before opens_on"
    world.patch(world.manager, f"{path}/{round_['id']}", {"status": "closed"})
    world.db.query(QuizRound).update(
        {
            QuizRound.due_on: today - timedelta(days=1),
            QuizRound.opens_on: today - timedelta(days=8),
        }
    )
    world.db.commit()
    stale = world.patch(world.manager, f"{path}/{round_['id']}", {"status": "open"})
    assert stale.status_code == 400
    assert stale.json()["detail"] == "An open round needs a due date from today"
    reopened = world.patch(
        world.manager,
        f"{path}/{round_['id']}",
        {"status": "open", "due_on": today.isoformat()},
    )
    assert reopened.json()["status"] == "open"
    assert (
        world.patch(
            world.manager, f"{path}/{round_['id']}", {"status": None}
        ).status_code
        == 400
    )
    assert (
        world.patch(
            world.manager, f"{path}/{round_['id']}", {"due_on": None}
        ).status_code
        == 400
    )
    assert (
        world.patch(
            world.manager, f"{path}/{uuid.uuid4()}", {"status": "closed"}
        ).status_code
        == 404
    )


# --- Sitting a round ------------------------------------------------------------------


def _keys(value):
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_a_sitting_never_carries_the_answer_key_and_resumes_by_page(world):
    ada, ben = world.students[:2]
    quiz = world.ready(page_size=2, show_scores=True, reveal_answers="after_submit")
    round_ = world.open_round(quiz)
    client = world.student_client(ada)
    start = f"/v1/me/quizzes/rounds/{round_['id']}/attempt"
    attempt = world.post(client, start).json()
    assert {"correct", "explanation", "score", "points", "grade"}.isdisjoint(
        _keys(attempt)
    )
    assert (attempt["status"], attempt["page_count"], attempt["pages_saved"]) == (
        "in_progress",
        2,
        0,
    )
    assert attempt["score_visible"] is False and attempt["review"] is None
    # Starting again returns the same attempt.
    assert world.post(client, start).json()["id"] == attempt["id"]

    base = f"/v1/me/quizzes/attempts/{attempt['id']}"
    by_position = {q["position"]: q for q in attempt["questions"]}
    first_page = [by_position[0], by_position[1]]
    other = (
        next(
            q
            for q in attempt["questions"]
            if q["kind"] == "choice" and q not in first_page
        )
        if any(
            q["kind"] == "choice" and q["position"] == 2 for q in attempt["questions"]
        )
        else None
    )

    def answer(question, pick=0):
        if question["kind"] == "choice":
            return {
                "position": question["position"],
                "option_id": question["options"][pick]["id"],
            }
        return {"position": question["position"], "text": "  A considered answer  "}

    errors = [
        (5, [], "This quiz has no such page"),
        (0, [answer(by_position[2])], "An answer is not for a question on this page"),
        (
            0,
            [answer(by_position[0]), answer(by_position[0])],
            "A question on this page is answered twice",
        ),
    ]
    for page, answers, detail in errors:
        response = world.put(client, f"{base}/pages/{page}", {"answers": answers})
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == detail
    choice_question = next(q for q in first_page if q["kind"] == "choice")
    text_question = next((q for q in attempt["questions"] if q["kind"] == "text"), None)
    foreign = {"position": choice_question["position"], "option_id": str(uuid.uuid4())}
    response = world.put(client, f"{base}/pages/0", {"answers": [foreign]})
    assert response.json()["detail"] == "Choose one of the question's options"
    wrong_kind = {"position": choice_question["position"], "text": "words"}
    response = world.put(client, f"{base}/pages/0", {"answers": [wrong_kind]})
    assert (
        response.json()["detail"]
        == "A multiple-choice question is answered with an option"
    )
    if text_question is not None:
        page = text_question["position"] // 2
        bad = {"position": text_question["position"], "option_id": str(uuid.uuid4())}
        response = world.put(client, f"{base}/pages/{page}", {"answers": [bad]})
        assert response.json()["detail"] == "A written question is answered with text"
    assert world.put(client, f"{base}/pages/-1", {"answers": []}).status_code == 422

    saved = world.put(
        client, f"{base}/pages/0", {"answers": [answer(q) for q in first_page]}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["pages_saved"] == 1
    # Another person cannot read or change this attempt.
    other_client = world.student_client(ben)
    assert other_client.get(base).status_code == 404
    assert (
        world.put(other_client, f"{base}/pages/0", {"answers": []}).status_code == 404
    )
    resumed = client.get(base).json()
    assert resumed["pages_saved"] == 1
    saved_answers = {q["position"]: q["answer"] for q in resumed["questions"]}
    for question in first_page:
        if question["kind"] == "choice":
            assert (
                saved_answers[question["position"]]["option_id"]
                == question["options"][0]["id"]
            )
        else:
            assert saved_answers[question["position"]]["text"] == "A considered answer"
    assert other is None or other["kind"] == "choice"


def test_handing_in_marks_choices_and_waits_for_written_grades(world):
    ada = world.students[0]
    quiz = world.ready(show_scores=True, reveal_answers="after_submit", pass_mark=50)
    round_ = world.open_round(quiz)
    result = sit(world, ada, round_["id"], choose={"Who may approve access?": "Anyone"})
    assert result["status"] == "submitted"
    assert result["score_visible"] is False  # not every answer is marked yet
    assert result["review"] == "shown"
    marks = {q["prompt"]: q for q in result["questions"]}
    assert marks["Who may approve access?"]["correct"] is False
    assert marks["Report a lost laptop within?"]["correct"] is True
    assert marks["Describe a phishing sign."]["pending"] is True
    assert (
        marks["Describe a phishing sign."]["explanation"]
        == "Urgency, mismatched sender, odd links."
    )
    assert result["reward"]["points"] == 125  # finished on the day it opened
    client = world.student_client(ada)
    again = world.put(
        client, f"/v1/me/quizzes/attempts/{result['id']}/pages/0", {"answers": []}
    )
    assert again.status_code == 409
    assert again.json()["detail"] == "This attempt is already submitted"

    grade_all(world, quiz["id"], grade=3.5)
    review = client.get(f"/v1/me/quizzes/attempts/{result['id']}").json()
    assert review["status"] == "complete"
    # 0 + 1 + 3.5/5 of three questions.
    assert review["score"] == pytest.approx(56.67)
    assert (review["passed"], review["trophy"], review["pass_mark"]) == (
        True,
        "bronze",
        50,
    )
    written_mark = {q["prompt"]: q for q in review["questions"]}[
        "Describe a phishing sign."
    ]
    assert (
        written_mark["grade"],
        written_mark["points"],
        written_mark["feedback"],
    ) == (3.5, 0.7, "Good")


def test_a_blank_written_answer_scores_nothing_without_a_grade(world):
    ada = world.students[0]
    quiz = world.ready(show_scores=True)
    round_ = world.open_round(quiz)
    result = sit(world, ada, round_["id"], texts={"Describe a phishing sign.": "   "})
    assert result["status"] == "complete"
    assert result["score"] == pytest.approx(66.67)
    assert world.manager.get(f"/v1/quizzes/{quiz['id']}/grading").json()["items"] == []


def test_scores_and_answers_reach_the_student_only_as_the_quiz_allows(world):
    ada, ben = world.students[:2]
    hidden = world.ready(title="Hidden", questions=QUESTIONS[:2])
    round_ = world.open_round(hidden)
    result = sit(world, ada, round_["id"])
    assert result["status"] == "complete"
    assert "score" not in result and "trophy" not in result
    # Reviewing answers would give away a score the quiz keeps hidden, so
    # not even the person's own answers come back.
    assert (result["review"], result["questions"]) == ("scores_hidden", [])
    assert (
        result["reward"]["points"] == 125
    )  # no trophy points while scores stay hidden

    after_close = world.ready(
        title="After close", questions=QUESTIONS[:2], show_scores=True
    )
    round_ = world.open_round(after_close)
    result = sit(world, ben, round_["id"])
    assert (
        result["score"],
        result["trophy"],
        result["review"],
        result["questions"],
    ) == (100.0, "gold", "at_close", [])
    assert result["reward"]["points"] == 175
    _close(world, after_close, round_)
    revealed = (
        world.student_client(ben).get(f"/v1/me/quizzes/attempts/{result['id']}").json()
    )
    assert revealed["review"] == "shown"
    assert {q["correct"] for q in revealed["questions"]} == {True}

    never = world.ready(
        title="Never", questions=QUESTIONS[:2], show_scores=True, reveal_answers="never"
    )
    round_ = world.open_round(never)
    result = sit(world, ada, round_["id"])
    _close(world, never, round_)
    closed = (
        world.student_client(ada).get(f"/v1/me/quizzes/attempts/{result['id']}").json()
    )
    assert (closed["score"], closed["review"], closed["questions"]) == (
        100.0,
        "never",
        [],
    )


def test_a_review_closes_once_the_quizs_next_round_opens(world):
    """Answers never carry into the next round: a person's review of a round
    closes for good once a later round of the quiz opens, and the score stays."""
    ada = world.students[0]
    quiz = world.ready(
        questions=QUESTIONS[:2],
        show_scores=True,
        reveal_answers="after_submit",
        people=[ada],
    )
    first = world.open_round(quiz)
    result = sit(world, ada, first["id"])
    client = world.student_client(ada)
    review = f"/v1/me/quizzes/attempts/{result['id']}"
    assert client.get(review).json()["review"] == "shown"

    # A round set to open next week leaves the review open until it does.
    today = today_for_schedule()
    response = world.post(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/rounds",
        {
            "opens_on": (today + timedelta(days=7)).isoformat(),
            "due_on": (today + timedelta(days=14)).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    assert client.get(review).json()["review"] == "shown"

    # A round opening today closes it, though the first round is still open.
    second = world.open_round(quiz)
    closed = client.get(review).json()
    assert (closed["review"], closed["score"], closed["trophy"]) == (
        "superseded",
        100.0,
        "gold",
    )
    assert {"prompt", "options", "answer", "correct", "explanation"}.isdisjoint(
        _keys(closed)
    )
    # Sitting the new round shows only the person's own saved answers.
    sitting = world.post(client, f"/v1/me/quizzes/rounds/{second['id']}/attempt").json()
    assert sitting["review"] is None
    assert {"correct", "explanation"}.isdisjoint(_keys(sitting))
    # Closing the first round later never reopens its review; the newest
    # round's own review opens as the quiz allows.
    _close(world, quiz, first)
    assert client.get(review).json()["review"] == "superseded"
    latest = sit(world, ada, second["id"])
    assert latest["review"] == "shown" and latest["questions"]


def test_a_review_waits_while_another_round_of_the_quiz_is_left_to_sit(world):
    """Overlapping rounds share their questions, so a person's review of one
    stays closed while they can still sit another."""
    ada = world.students[0]
    quiz = world.ready(
        questions=QUESTIONS[:2],
        show_scores=True,
        reveal_answers="after_submit",
        people=[ada],
    )
    older = world.open_round(quiz)
    newer = world.open_round(quiz)
    result = sit(world, ada, newer["id"])
    assert (result["review"], result["questions"]) == ("other_round", [])
    client = world.student_client(ada)
    review = f"/v1/me/quizzes/attempts/{result['id']}"
    sitting = world.post(client, f"/v1/me/quizzes/rounds/{older['id']}/attempt").json()
    assert {"correct", "explanation"}.isdisjoint(_keys(sitting))
    assert client.get(review).json()["review"] == "other_round"
    # With nothing left to sit, the newest round's review opens; the older
    # round's stays closed behind it.
    finished = sit(world, ada, older["id"])
    assert finished["review"] == "superseded"
    shown = client.get(review).json()
    assert shown["review"] == "shown" and shown["questions"]


def test_only_quiz_managers_move_the_account_of_someone_who_sits_quizzes(world):
    ada, ben = world.students[:2]
    quiz = world.ready(people=[ada])
    round_ = world.open_round(quiz)
    started = world.post(
        world.student_client(ada), f"/v1/me/quizzes/rounds/{round_['id']}/attempt"
    ).json()
    isms_user = world.keen.user("isms.manage")
    isms = world.keen.client(isms_user)
    path = f"/v1/isms/people/{ada.person.id}"
    # Linking Ada's record to another account would hand it her rounds and
    # results, so isms.manage alone cannot.
    refused = world.patch(isms, path, {"name": "Ada", "user_id": str(isms_user.id)})
    assert refused.status_code == 403
    assert refused.json()["detail"] == (
        "Changing the Keen account of someone who sits quizzes needs quiz.manage"
    )
    assert world.patch(isms, path, {"name": "Ada", "user_id": None}).status_code == 403
    # Someone without quiz rounds is linked as before.
    linked = world.patch(
        isms,
        f"/v1/isms/people/{ben.person.id}",
        {"name": "Ben", "user_id": str(isms_user.id)},
    )
    assert linked.status_code == 200
    # A quiz manager may move the link; the attempt still continues only from
    # the account that started it.
    mover = world.keen.client(world.keen.user("isms.manage", "quiz.manage"))
    fresh = world.keen.user()
    moved = world.patch(mover, path, {"name": "Ada", "user_id": str(fresh.id)})
    assert moved.status_code == 200
    client = world.keen.client(fresh)
    attempt_path = f"/v1/me/quizzes/attempts/{started['id']}"
    for response in (
        world.put(client, f"{attempt_path}/pages/0", {"answers": []}),
        world.post(client, f"{attempt_path}/submit", {"page": 0, "answers": []}),
        world.post(client, f"/v1/me/quizzes/rounds/{round_['id']}/attempt"),
    ):
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "This attempt was started from another account"
        )


def test_only_participants_of_an_open_round_can_sit_it(world):
    ada, ben = world.students[:2]
    quiz = world.ready(people=[ada])
    round_ = world.open_round(quiz)
    start = f"/v1/me/quizzes/rounds/{round_['id']}/attempt"
    assert world.post(world.student_client(ben), start).status_code == 404
    unlinked = world.keen.client(world.keen.user())
    refused = world.post(unlinked, start)
    assert refused.status_code == 409
    assert "not linked to a People record" in refused.json()["detail"]
    assert unlinked.get("/v1/me/quizzes").json()["linked"] is False

    today = today_for_schedule()
    later = world.post(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/rounds",
        {
            "opens_on": (today + timedelta(days=2)).isoformat(),
            "due_on": (today + timedelta(days=9)).isoformat(),
        },
    ).json()
    waiting = world.post(
        world.student_client(ada), f"/v1/me/quizzes/rounds/{later['id']}/attempt"
    )
    assert waiting.status_code == 409
    assert (
        waiting.json()["detail"]
        == f"This quiz round opens on {(today + timedelta(days=2)).isoformat()}"
    )

    client = world.student_client(ada)
    attempt = world.post(client, start).json()
    world.patch(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/rounds/{round_['id']}",
        {"status": "closed"},
    )
    late = world.post(
        client,
        f"/v1/me/quizzes/attempts/{attempt['id']}/submit",
        {"page": 0, "answers": []},
    )
    assert late.status_code == 409
    assert late.json()["detail"] == "This quiz round has closed"
    # The attempt stays readable, still unfinished.
    assert (
        client.get(f"/v1/me/quizzes/attempts/{attempt['id']}").json()["status"]
        == "in_progress"
    )
    other = world.open_round(quiz)
    world.db.query(QuizRound).filter_by(id=uuid.UUID(other["id"])).update(
        {
            QuizRound.due_on: today - timedelta(days=1),
            QuizRound.opens_on: today - timedelta(days=3),
        }
    )
    world.db.commit()
    overdue = world.post(client, f"/v1/me/quizzes/rounds/{other['id']}/attempt")
    assert (
        overdue.status_code == 409
        and overdue.json()["detail"] == "This quiz round has closed"
    )


def test_a_partial_draw_shows_unseen_questions_first(world):
    ada = world.students[0]
    four = QUESTIONS[:2] + [
        choice("Where are passwords kept?", "The vault", "A sticky note"),
        choice("Who reviews access?", "Owners, quarterly", "Nobody"),
    ]
    quiz = world.ready(questions=four, questions_per_attempt=2, people=[ada])
    first_round = world.open_round(quiz)
    first = sit(world, ada, first_round["id"])
    second_round = world.open_round(quiz)
    second = sit(world, ada, second_round["id"])

    def drawn(attempt):
        detail = world.manager.get(
            f"/v1/quizzes/{quiz['id']}/attempts/{attempt['id']}"
        ).json()
        return {q["prompt"] for q in detail["questions"]}

    seen_first, seen_second = drawn(first), drawn(second)
    assert len(seen_first) == len(seen_second) == 2
    assert seen_first.isdisjoint(seen_second)


# --- Grading ---------------------------------------------------------------------------


def test_grading_is_blind_in_half_points_and_never_of_your_own_answers(
    world, monkeypatch
):
    ada = world.students[0]
    quiz = world.ready(people=[ada])
    round_ = world.open_round(quiz)
    in_progress = world.post(
        world.student_client(ada), f"/v1/me/quizzes/rounds/{round_['id']}/attempt"
    ).json()
    text_answer = world.db.query(QuizAnswer).filter_by(kind="text").one()
    early = world.put(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/answers/{text_answer.id}/grade",
        {"grade": 3},
    )
    assert early.status_code == 409
    assert early.json()["detail"] == "This answer is not handed in yet"
    world.db.query(QuizAttempt).delete()
    world.db.commit()
    assert in_progress["status"] == "in_progress"

    sit(world, ada, round_["id"], choose={"Who may approve access?": "Anyone"})
    queue = world.manager.get(f"/v1/quizzes/{quiz['id']}/grading").json()
    [item] = queue["items"]
    assert queue["own_answers_waiting"] == 0
    assert set(item) == {
        "answer_id",
        "attempt_id",
        "round_id",
        "round_opens_on",
        "prompt",
        "guidance",
        "text",
        "submitted_at",
    }
    assert item["guidance"] == "Urgency, mismatched sender, odd links."
    grade = f"/v1/quizzes/{quiz['id']}/answers/{item['answer_id']}/grade"
    assert (
        world.put(world.manager, grade, {"grade": 3.3}).json()["detail"]
        == "Grades run from 1 to 5 in half points"
    )
    assert world.put(world.manager, grade, {"grade": 6}).status_code == 422
    assert world.put(world.manager, grade, {"grade": 0.5}).status_code == 422
    choice_answer = world.db.query(QuizAnswer).filter_by(kind="choice").first()
    refused = world.put(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/answers/{choice_answer.id}/grade",
        {"grade": 3},
    )
    assert refused.json()["detail"] == "Only written answers are graded"
    other_quiz = world.ready(title="Other", people=[ada])
    foreign = world.put(
        world.manager,
        f"/v1/quizzes/{other_quiz['id']}/answers/{item['answer_id']}/grade",
        {"grade": 3},
    )
    assert foreign.status_code == 404

    graded = world.put(
        world.manager, grade, {"grade": 2.5, "feedback": "Name one sign"}
    )
    assert graded.status_code == 200, graded.text
    body = graded.json()
    assert (body["answer"]["grade"], body["answer"]["points"]) == (2.5, 0.5)
    assert body["attempt"]["status"] == "complete"
    assert body["attempt"]["score"] == pytest.approx(50.0)
    # Re-grading updates the score.
    regraded = world.put(world.manager, grade, {"grade": 5}).json()
    assert regraded["attempt"]["score"] == pytest.approx(66.67)

    # A manager who sat the quiz waits for another manager's grade.
    manager_person = IsmsPerson(name="Manager", user_id=world.manager_user.id)
    world.db.add(manager_person)
    world.db.commit()
    mine = world.ready(title="Mine", people=[SimpleNamespace(person=manager_person)])
    mine_round = world.open_round(mine)
    sit(world, SimpleNamespace(user=world.manager_user), mine_round["id"])
    own = world.manager.get(f"/v1/quizzes/{mine['id']}/grading").json()
    assert (own["items"], own["own_answers_waiting"]) == ([], 1)
    own_answer = (
        world.db.query(QuizAnswer)
        .join(QuizAttempt)
        .filter(QuizAttempt.quiz_id == uuid.UUID(mine["id"]), QuizAnswer.kind == "text")
        .one()
    )
    blocked = world.put(
        world.manager,
        f"/v1/quizzes/{mine['id']}/answers/{own_answer.id}/grade",
        {"grade": 5},
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "Another quiz manager grades your own answers"
    other_manager = world.keen.client(world.keen.user("quiz.manage"))
    assert (
        world.put(
            other_manager,
            f"/v1/quizzes/{mine['id']}/answers/{own_answer.id}/grade",
            {"grade": 5},
        ).status_code
        == 200
    )


def test_a_blank_written_answer_takes_no_grade(world):
    ada = world.students[0]
    quiz = world.ready(people=[ada])
    round_ = world.open_round(quiz)
    sit(world, ada, round_["id"], texts={"Describe a phishing sign.": ""})
    blank = world.db.query(QuizAnswer).filter_by(kind="text").one()
    response = world.put(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/answers/{blank.id}/grade",
        {"grade": 3},
    )
    assert response.status_code == 400
    assert (
        response.json()["detail"] == "A blank answer scores nothing and takes no grade"
    )


# --- Results and the measure ---------------------------------------------------------------


def _close(world, quiz, round_):
    response = world.patch(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/rounds/{round_['id']}",
        {"status": "closed"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _entries(world):
    world.db.expire_all()
    return world.db.query(IsmsEffectivenessMetricEntry).all()


def test_readers_see_a_round_only_once_it_describes_a_group(world):
    ada, ben, cleo, dev = world.students
    quiz = world.ready(pass_mark=60)
    round_ = world.open_round(quiz)
    results = f"/v1/quizzes/{quiz['id']}/results"
    sit(world, ada, round_["id"])
    sit(world, ben, round_["id"], choose={"Who may approve access?": "Anyone"})
    grade_all(world, quiz["id"], grade=5)

    open_view = world.reader.get(results).json()
    assert open_view["rounds"] == [] and open_view["hidden_rounds"] == 1
    assert open_view["summary"]["responses"] == 0 and open_view["questions"] == []
    hidden = world.reader.get(f"{results}?round_id={round_['id']}")
    assert hidden.status_code == 404
    # Two completed responses stay with quiz.manage even once the round closes.
    _close(world, quiz, round_)
    assert world.reader.get(results).json()["rounds"] == []
    assert _entries(world) == []
    detail = world.reader.get(f"/v1/quizzes/{quiz['id']}").json()
    assert [(r["average"], r["results_shown"]) for r in detail["rounds"]] == [
        (None, False)
    ]

    managed = world.manager.get(f"{results}?round_id={round_['id']}").json()
    assert managed["summary"]["responses"] == 2
    assert managed["summary"]["average"] == pytest.approx(83.33)
    people = {p["name"]: p for p in managed["people"]}
    assert people["Cleo"]["status"] == "not_started"
    assert people["Ben"]["score"] == pytest.approx(66.67)
    assert people["Ada"]["trophy"] == "gold"

    # Reopen, let a third person finish and close: the group's result shows.
    world.patch(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/rounds/{round_['id']}",
        {"status": "open"},
    )
    sit(world, cleo, round_["id"], choose={"Report a lost laptop within?": "One week"})
    grade_all(world, quiz["id"], grade=1)
    _close(world, quiz, round_)
    view = world.reader.get(results).json()
    [shown] = view["rounds"]
    assert (shown["complete"], shown["participants"], shown["recorded"]) == (3, 4, True)
    # Ada 100, Ben 66.67 and Cleo 40 (right, wrong, and a written grade of 1).
    assert shown["average"] == pytest.approx(68.89)
    assert shown["pass_rate"] == pytest.approx(66.7)
    assert "people" not in view
    assert view["summary"]["trophies"] == {"bronze": 1, "silver": 0, "gold": 1}
    for question in view["questions"]:
        assert "options" not in question
    assert "common_wrong_answers" not in view["insights"]
    assert [e.metric_value for e in _entries(world)] == [pytest.approx(68.89)]
    entry = _entries(world)[0]
    assert entry.source_reference == f"quiz-round:{round_['id']}"
    assert entry.source_url == f"https://keen.example/quiz.html?id={quiz['id']}"
    assert entry.notes == (
        "Average of 3 completed responses from 4 assigned people. "
        "2 reached the 60% pass mark."
    )
    assert (entry.period_start.isoformat(), entry.period_end.isoformat()) == (
        round_["opens_on"],
        round_["due_on"],
    )

    managed = world.manager.get(results).json()
    insights = managed["insights"]
    assert [i["prompt"] for i in insights["hardest"]] == ["Who may approve access?"]
    assert [i["prompt"] for i in insights["easiest"]] == [
        "Describe a phishing sign.",
        "Report a lost laptop within?",
    ]
    wrong = {
        (w["prompt"], w["answer"]): w["chosen"]
        for w in insights["common_wrong_answers"]
    }
    assert wrong == {
        ("Who may approve access?", "Anyone"): 1,
        ("Report a lost laptop within?", "One week"): 1,
    }
    by_prompt = {q["prompt"]: q for q in managed["questions"]}
    options = {o["text"]: o for o in by_prompt["Who may approve access?"]["options"]}
    assert (options["Anyone"]["chosen"], options["Anyone"]["share"]) == (1, 33.3)
    assert options["The asset owner"]["correct"] is True
    assert by_prompt["Describe a phishing sign."]["average_grade"] == pytest.approx(
        3.67
    )


def test_a_round_waits_for_its_last_grade_and_a_recorded_round_stays_closed(world):
    quiz = world.ready()
    round_ = world.open_round(quiz)
    for student in world.students[:3]:
        sit(world, student, round_["id"])
    queue = world.manager.get(f"/v1/quizzes/{quiz['id']}/grading").json()["items"]
    for item in queue[:2]:
        world.put(
            world.manager,
            f"/v1/quizzes/{quiz['id']}/answers/{item['answer_id']}/grade",
            {"grade": 5},
        )
    closed = _close(world, quiz, round_)
    assert (closed["pending_grading"], closed["recorded"]) == (1, False)
    assert _entries(world) == []
    # The written question's success counts only the graded answers.
    written_stats = {
        q["prompt"]: q
        for q in world.manager.get(f"/v1/quizzes/{quiz['id']}/results").json()[
            "questions"
        ]
    }["Describe a phishing sign."]
    assert (written_stats["answered"], written_stats["success_rate"]) == (3, 100.0)
    last = queue[2]["answer_id"]
    world.put(
        world.manager, f"/v1/quizzes/{quiz['id']}/answers/{last}/grade", {"grade": 5}
    )
    assert [e.metric_value for e in _entries(world)] == [100.0]
    # A changed grade in a closed round updates the result.
    world.put(
        world.manager, f"/v1/quizzes/{quiz['id']}/answers/{last}/grade", {"grade": 2}
    )
    assert [e.metric_value for e in _entries(world)] == [pytest.approx(93.33)]
    # Reopening for a latecomer would let anyone compare the averages before
    # and after and read off the latecomer's score, so a recorded round stays
    # closed.
    reopened = world.patch(
        world.manager,
        f"/v1/quizzes/{quiz['id']}/rounds/{round_['id']}",
        {"status": "open"},
    )
    assert reopened.status_code == 409
    assert reopened.json()["detail"].startswith("This round's result is recorded")
    assert [e.metric_value for e in _entries(world)] == [pytest.approx(93.33)]


def test_a_manager_reads_each_attempt_in_full(world):
    ada = world.students[0]
    quiz = world.ready(people=[ada])
    round_ = world.open_round(quiz)
    sit(world, ada, round_["id"])
    attempt = world.db.query(QuizAttempt).one()
    detail = world.manager.get(f"/v1/quizzes/{quiz['id']}/attempts/{attempt.id}").json()
    assert detail["person"] == {"id": str(ada.person.id), "name": "Ada"}
    assert detail["review"] == "shown" and detail["score_visible"] is True
    assert all(q["answer_id"] for q in detail["questions"])
    other = world.ready(title="Other", people=[ada])
    assert (
        world.manager.get(
            f"/v1/quizzes/{other['id']}/attempts/{attempt.id}"
        ).status_code
        == 404
    )


def test_the_list_shows_each_quiz_with_its_all_time_average(world):
    quiz = world.ready(
        pass_mark=50,
        start_date=today_for_schedule().isoformat(),
        schedule_recurrence="quarterly",
    )
    round_ = world.open_round(quiz)
    for student in world.students[:3]:
        sit(world, student, round_["id"], texts={"Describe a phishing sign.": ""})
    world.create(title="Draft quiz")
    managed = world.manager.get(f"/v1/quizzes?framework={FW}").json()
    by_title = {item["title"]: item for item in managed["items"]}
    assert by_title["Security basics"]["average"] == pytest.approx(66.67)
    assert by_title["Security basics"]["open_rounds"] == 1
    assert by_title["Security basics"]["schedule"]["label"] == "Quarterly"
    assert by_title["Draft quiz"]["question_count"] == 0
    read = {
        item["title"]: item
        for item in world.reader.get(f"/v1/quizzes?framework={FW}").json()["items"]
    }
    assert read["Security basics"]["average"] is None
    _close(world, quiz, round_)
    read = {
        item["title"]: item
        for item in world.reader.get(f"/v1/quizzes?framework={FW}").json()["items"]
    }
    assert (
        read["Security basics"]["average"],
        read["Security basics"]["responses"],
    ) == (
        pytest.approx(66.67),
        3,
    )
    assert world.manager.get("/v1/quizzes?framework=fw-elsewhere").json()["items"] == []
    assert world.manager.get("/v1/quizzes?framework=bad%20slug").status_code == 400


# --- The worker ------------------------------------------------------------------------------


def test_the_schedule_opens_rounds_and_the_worker_closes_them(world):
    ada = world.students[0]
    today = today_for_schedule()
    quiz = world.ready(
        people=[ada],
        started=True,
        start_date=today.isoformat(),
        schedule_recurrence="weekly",
        due_days=3,
    )
    # Activating on a round date opens that round at once.
    [first] = quiz["rounds"]
    assert (first["opens_on"], first["due_on"], first["scheduled"]) == (
        today.isoformat(),
        (today + timedelta(days=3)).isoformat(),
        True,
    )
    world.db.expire_all()
    stored = world.db.get(Quiz, uuid.UUID(quiz["id"]))
    assert stored.schedule_next_run_date == today + timedelta(days=7)
    # The day's run opens nothing more.
    assert (
        rounds_service.materialize_due_quiz_rounds(world.db, today=today)["created"]
        == []
    )

    later = today + timedelta(days=4)
    assert rounds_service.close_due_quiz_rounds(world.db, today=later)["closed"] == [
        first["id"]
    ]
    world.db.expire_all()
    assert world.db.get(QuizRound, uuid.UUID(first["id"])).status == "closed"
    # A week on, the worker opens the next round and invites its people.
    week = today + timedelta(days=7)
    [created] = rounds_service.materialize_due_quiz_rounds(world.db, today=week)[
        "created"
    ]
    assert (created["opens_on"], created["due_on"], created["invited"]) == (
        week.isoformat(),
        (week + timedelta(days=3)).isoformat(),
        1,
    )
    assert world.db.get(QuizRound, uuid.UUID(created["round_id"])).scheduled is True
    # A draft or archived quiz opens no rounds.
    world.patch(world.manager, f"/v1/quizzes/{quiz['id']}", {"status": "archived"})
    assert (
        rounds_service.materialize_due_quiz_rounds(
            world.db, today=today + timedelta(days=14)
        )["created"]
        == []
    )


def test_participants_are_reminded_once_until_they_hand_in(world, monkeypatch):
    from app.services import schedules

    ada, ben, cleo = world.students[:3]
    remind = Mock()
    monkeypatch.setattr(schedules, "smtp_configured", lambda: True)
    monkeypatch.setattr(rounds_service, "send_quiz_reminder_email", remind)
    dev = world.students[3]
    quiz = world.ready(people=[ada, ben, cleo, dev])
    soon = world.open_round(quiz, days=1)
    world.open_round(quiz, days=20)  # too far off for a reminder
    dev.person.email = ""
    dev.user.email = None
    world.db.commit()
    sit(world, ada, soon["id"])
    client = world.student_client(ben)
    world.post(client, f"/v1/me/quizzes/rounds/{soon['id']}/attempt")  # started only

    report = rounds_service.send_quiz_reminders(world.db)
    assert report["sent"] == 2
    assert sorted(call.kwargs["to_email"] for call in remind.call_args_list) == [
        "ben@example.test",
        "cleo@example.test",
    ]
    assert remind.call_args.kwargs["uid"] == f"keen-quiz-round-{soon['id']}"
    remind.reset_mock()
    assert rounds_service.send_quiz_reminders(world.db)["sent"] == 0
    remind.assert_not_called()

    monkeypatch.setattr(schedules, "smtp_configured", lambda: False)
    assert rounds_service.send_quiz_reminders(world.db) == {"enabled": False, "sent": 0}


# --- A person's own quizzes -------------------------------------------------------------------


def test_my_quizzes_lists_rounds_by_what_to_do_next_with_progress(world):
    ada, ben = world.students[:2]
    today = today_for_schedule()
    shown = world.ready(
        title="Shown", questions=QUESTIONS[:2], show_scores=True, people=[ada, ben]
    )
    hidden = world.ready(title="Hidden", questions=QUESTIONS[:2], people=[ada, ben])
    written_quiz = world.ready(title="Written", people=[ada, ben])
    scheduled = world.ready(
        title="Scheduled",
        questions=QUESTIONS[:1],
        people=[ada],
        start_date=(today + timedelta(days=10)).isoformat(),
        schedule_recurrence="monthly",
    )
    done = world.open_round(shown)
    sit(world, ada, done["id"])
    secret = world.open_round(hidden)
    sit(world, ada, secret["id"])
    waiting = world.open_round(written_quiz)
    sit(world, ada, waiting["id"])
    missed = world.open_round(hidden)
    _close(world, hidden, missed)
    started = world.open_round(shown, days=3)
    world.post(
        world.student_client(ada), f"/v1/me/quizzes/rounds/{started['id']}/attempt"
    )
    fresh = world.open_round(shown, days=9)
    soon = world.post(
        world.manager,
        f"/v1/quizzes/{shown['id']}/rounds",
        {
            "opens_on": (today + timedelta(days=2)).isoformat(),
            "due_on": (today + timedelta(days=12)).isoformat(),
        },
    ).json()
    world.ready(
        title="Someone else's",
        questions=QUESTIONS[:1],
        people=[ben],
        start_date=(today + timedelta(days=5)).isoformat(),
        schedule_recurrence="monthly",
    )

    mine = world.student_client(ada).get("/v1/me/quizzes").json()
    assert mine["linked"] is True and mine["person"]["name"] == "Ada"
    states = [(item["title"], item["state"]) for item in mine["items"]]
    assert states[:3] == [
        ("Shown", "in_progress"),
        ("Shown", "available"),
        ("Shown", "upcoming"),
    ]
    assert set(states[3:]) == {
        ("Shown", "complete"),
        ("Hidden", "complete"),
        ("Written", "awaiting_grading"),
        ("Hidden", "missed"),
    }
    by_round = {item["round_id"]: item for item in mine["items"]}
    assert by_round[done["id"]]["result"] == {
        "score": 100.0,
        "pass_mark": None,
        "passed": None,
        "trophy": "gold",
    }
    assert by_round[secret["id"]]["result"] is None
    assert by_round[fresh["id"]]["days_left"] == 9
    assert by_round[soon["id"]]["days_left"] is None
    assert by_round[started["id"]]["attempt"]["pages_saved"] == 0
    # The scheduled quiz opens three rounds in the next 90 days.
    assert [entry["title"] for entry in mine["upcoming"]] == ["Scheduled"] * 3
    assert mine["upcoming"][0] == {
        "quiz_id": scheduled["id"],
        "title": "Scheduled",
        "date": (today + timedelta(days=10)).isoformat(),
    }
    progress = mine["progress"]
    assert progress["finished"] == 3
    # Three rounds finished promptly, one gold trophy shown.
    assert progress["points"] == 3 * 125 + 50
    assert (progress["level"], progress["name"], progress["next"]) == (
        3,
        "Defender",
        800,
    )
    # The missed round, due the same day, ends the run of three.
    assert (progress["streak"], progress["best_streak"]) == (0, 3)
    earned = {badge["key"] for badge in progress["badges"] if badge["earned"]}
    assert earned == {"first_quiz", "streak_3", "early_bird", "gold", "perfect"}


def test_the_calendar_shows_rounds_and_scheduled_openings(world):
    today = today_for_schedule()
    quiz = world.ready(
        start_date=(today + timedelta(days=3)).isoformat(), schedule_recurrence="weekly"
    )
    round_ = world.open_round(quiz)
    start = today.replace(day=1)
    end = start + timedelta(days=40)
    calendar = world.reader.get(
        f"/v1/quizzes/calendar?framework={FW}&start={start.isoformat()}&end={end.isoformat()}"
    ).json()
    assert calendar["rounds"][0]["id"] == round_["id"]
    dates = [o["date"] for o in calendar["occurrences"]]
    assert dates[0] == (today + timedelta(days=3)).isoformat()
    assert all(d <= end.isoformat() for d in dates)
    backwards = world.reader.get(
        f"/v1/quizzes/calendar?framework={FW}&start=2026-05-02&end=2026-05-01"
    )
    assert backwards.status_code == 400
    wide = world.reader.get(
        f"/v1/quizzes/calendar?framework={FW}&start=2026-01-01&end=2027-06-01"
    )
    assert wide.json()["detail"] == "The calendar covers up to 400 days"
    month = world.reader.get(f"/v1/quizzes/calendar?framework={FW}").json()
    assert month["start"] == today.replace(day=1).isoformat()


def test_two_starts_at_once_share_one_attempt(world, monkeypatch):
    from app.services.quizzes import attempts as attempts_service

    ada = world.students[0]
    quiz = world.ready(people=[ada])
    round_ = world.open_round(quiz)
    client = world.student_client(ada)
    first = world.post(client, f"/v1/me/quizzes/rounds/{round_['id']}/attempt").json()
    # The second request misses the first attempt on its initial look, as a
    # request racing the first would, and meets the unique constraint.
    real = attempts_service._existing_attempt
    looks = []

    def racing(db, round_id, person_id):
        looks.append(round_id)
        return None if len(looks) == 1 else real(db, round_id, person_id)

    monkeypatch.setattr(attempts_service, "_existing_attempt", racing)
    second = world.post(client, f"/v1/me/quizzes/rounds/{round_['id']}/attempt")
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first["id"] and len(looks) == 2
    world.db.expire_all()
    assert world.db.query(QuizAttempt).count() == 1


def test_an_address_reaches_a_person_through_their_account_while_it_is_active(world):
    ada = world.students[0]
    person = ada.person
    assert audience_service.person_email(person) == "ada@example.test"
    person.email = "not an address"
    ada.user.email = "ada.work@example.test"
    world.db.commit()
    assert audience_service.person_email(person) == "ada.work@example.test"
    ada.user.is_active = False
    world.db.commit()
    assert audience_service.person_email(person) is None
    assert audience_service.person_email(IsmsPerson(name="Nobody", email="")) is None
