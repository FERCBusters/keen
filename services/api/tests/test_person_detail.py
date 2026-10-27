"""Detail pages retain ISMS authority; named quiz history requires quiz.manage."""

import uuid
from datetime import timedelta

import pytest
from app.core.datetime_utils import utc_now_naive
from app.db.models import IsmsPerson, IsmsVendor, QuizAttempt, QuizRound
from app.services import features

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_quizzes_api import world as world


def test_record_detail_permissions_and_missing_records(keen):
    person, vendor = IsmsPerson(name="Person"), IsmsVendor(name="Vendor")
    keen.db.add_all([person, vendor])
    keen.db.commit()
    for kind, record in [("people", person), ("vendors", vendor)]:
        path = f"/v1/isms/{kind}/{record.id}"
        assert keen.client().get(path).status_code == 401
        assert keen.client(keen.user()).get(path).status_code == 403
        for code in ["isms.read", "risk.read", "isms.manage"]:
            response = keen.client(keen.user(code)).get(path)
            assert response.status_code == 200, response.text
            assert response.json()["name"] == record.name
            assert response.json()["assets"] == []
        reader = keen.client(keen.user("isms.read"))
        assert reader.get(f"/v1/isms/{kind}/{uuid.uuid4()}").status_code == 404
        assert reader.get(f"/v1/isms/{kind}/not-a-uuid").status_code == 422


def test_named_quiz_history_never_uses_cohort_reader_permission(world):
    keen = world.keen
    person = world.students[0].person
    path = f"/v1/isms/people/{person.id}/quizzes"
    assert keen.client().get(path).status_code == 401
    for codes in [(), ("isms.read",), ("quiz.read", "isms.read"), ("quiz.manage",)]:
        assert keen.client(keen.user(*codes)).get(path).status_code == 403
    response = keen.client(keen.user("quiz.manage", "isms.read")).get(path)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["assignments"] == []


def test_assignments_and_history_are_person_scoped_without_answers(world):
    student = world.students[0]
    other = world.students[1]
    quiz = world.ready(people=[student], pass_mark=None)
    round_ = world.open_round(quiz)
    keen = world.keen
    manager = keen.client(keen.user("isms.read", "quiz.manage"))
    path = f"/v1/isms/people/{student.person.id}/quizzes"
    initial = manager.get(path).json()
    assert [q["id"] for q in initial["assignments"]] == [quiz["id"]]
    assert initial["items"][0]["status"] == "not_started"
    assert (
        manager.get(f"/v1/isms/people/{other.person.id}/quizzes").json()["items"] == []
    )
    attempt = QuizAttempt(
        quiz_id=uuid.UUID(quiz["id"]),
        round_id=uuid.UUID(round_["id"]),
        person_id=student.person.id,
        status="complete",
        score=85,
        completed_at=utc_now_naive(),
        layout=[{"prompt": "private answer material"}],
    )
    world.db.add(attempt)
    world.db.commit()
    response = manager.get(path)
    row = response.json()["items"][0]
    assert row["score"] == 85 and row["passed"] is None
    assert row["completed_at"] and row["status"] == "complete"
    assert "private answer material" not in response.text
    assert "layout" not in row and "answers" not in row
    attempt.status = "submitted"
    world.db.commit()
    assert manager.get(path).json()["items"][0]["score"] is None


def test_round_history_paginates_and_rejects_unbounded_offsets(world):
    student = world.students[0]
    quiz = world.ready(people=[student])
    keen = world.keen
    for i in range(52):
        day = utc_now_naive().date() - timedelta(days=i)
        round_ = QuizRound(quiz_id=uuid.UUID(quiz["id"]), opens_on=day, due_on=day)
        world.db.add(round_)
        world.db.flush()
        world.db.add(
            QuizAttempt(
                quiz_id=uuid.UUID(quiz["id"]),
                round_id=round_.id,
                person_id=student.person.id,
            )
        )
    world.db.commit()
    client = keen.client(keen.user("isms.read", "quiz.manage"))
    path = f"/v1/isms/people/{student.person.id}/quizzes"
    first = client.get(path).json()
    second = client.get(path + "?offset=50").json()
    assert (
        first["total"] == 52
        and len(first["items"]) == 50
        and first["next_offset"] == 50
    )
    assert len(second["items"]) == 2 and second["next_offset"] is None
    assert not {r["round_id"] for r in first["items"]} & {
        r["round_id"] for r in second["items"]
    }
    assert client.get(path + "?offset=-1").status_code == 400
    assert client.get(path + "?assignments_offset=100001").status_code == 400
    assert client.get(f"/v1/isms/people/{uuid.uuid4()}/quizzes").status_code == 404


@pytest.mark.parametrize("feature", ["people", "quizzes", "isms"])
def test_quiz_summary_obeys_feature_flags_even_for_admin(world, feature):
    keen = world.keen
    admin = keen.user(role="admin")
    features.update(keen.db, admin, 0, {feature: False})
    response = keen.client(admin).get(
        f"/v1/isms/people/{world.students[0].person.id}/quizzes"
    )
    assert response.status_code == 404


def test_person_and_vendor_detail_obey_separate_features(keen):
    person, vendor = IsmsPerson(name="P"), IsmsVendor(name="V")
    keen.db.add_all([person, vendor])
    keen.db.commit()
    admin = keen.user(role="admin")
    features.update(keen.db, admin, 0, {"people": False})
    client = keen.client(admin)
    assert client.get(f"/v1/isms/people/{person.id}").status_code == 404
    assert client.get(f"/v1/isms/vendors/{vendor.id}").status_code == 200
