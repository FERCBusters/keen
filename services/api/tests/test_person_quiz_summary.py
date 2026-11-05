"""A person's named quiz assignments, as quiz managers with ISMS access see them.

The operation checks ISMS read access itself, so a caller that reaches it
without the route's check still sees nothing. A person without a Keen account
is assigned through the organisation role their People record names; roles
held through an inactive account assign nothing.
"""

import uuid

import pytest
from app.db.models import IsmsOrgNode, IsmsOrgNodeUser, IsmsPerson
from app.services.errors import DomainError
from app.services.person_quizzes import person_quizzes

# Imported fixtures: the assembled application and the quiz world built on it.
from tests.test_assembled_authority_regressions import keen  # noqa: F401
from tests.test_quizzes_api import world  # noqa: F401


def test_the_summary_checks_isms_access_in_the_operation_itself(world):  # noqa: F811
    app, person = world.keen, world.students[0].person
    for codes in [("quiz.manage",), ("quiz.manage", "quiz.read")]:
        with pytest.raises(DomainError) as refused:
            person_quizzes(db=app.db, user=app.user(*codes), person_id=person.id)
        assert (refused.value.status_code, refused.value.detail) == (
            403,
            "ISMS read access required",
        )
    with pytest.raises(DomainError) as refused:
        person_quizzes(db=app.db, user=app.user("isms.read"), person_id=person.id)
    assert refused.value.detail == "quiz.manage permission required"

    summary = person_quizzes(
        db=app.db, user=app.user("risk.read", "quiz.manage"), person_id=person.id
    )
    assert (summary["eligible"], summary["items"]) == (True, [])


def test_a_person_without_an_account_is_assigned_through_their_role(world):  # noqa: F811
    app, db = world.keen, world.db
    role, other_role = IsmsOrgNode(name="Engineers"), IsmsOrgNode(name="Finance")
    db.add_all([role, other_role])
    db.flush()
    contractor = IsmsPerson(name="Contractor", org_node_id=role.id)
    # A former employee whose account still holds the Finance role.
    leaver_account = app.user()
    leaver_account.is_active = False
    leaver = IsmsPerson(name="Leaver", user_id=leaver_account.id)
    db.add_all([contractor, leaver])
    db.add(IsmsOrgNodeUser(org_node_id=other_role.id, user_id=leaver_account.id))
    db.commit()
    by_role = world.create(title="Secure coding")
    elsewhere = world.create(title="Expense policy")
    for quiz, node in ((by_role, role), (elsewhere, other_role)):
        assigned = world.put(
            world.manager,
            f"/v1/quizzes/{quiz['id']}/audience",
            {"org_node_ids": [str(node.id)]},
        )
        assert assigned.status_code == 200, assigned.text
    manager = app.client(app.user("isms.read", "quiz.manage"))

    response = manager.get(f"/v1/isms/people/{contractor.id}/quizzes")

    assert response.status_code == 200, response.text
    summary = response.json()
    # Without an account nobody can sit the quiz, but the assignment is shown.
    assert summary["eligible"] is False
    assert summary["assignments"] == [{"id": by_role["id"], "title": "Secure coding"}]
    assert summary["assignments_total"] == 1
    # Roles held through an inactive account assign nothing.
    former = manager.get(f"/v1/isms/people/{leaver.id}/quizzes").json()
    assert (former["eligible"], former["assignments"]) == (False, [])
    missing = manager.get(f"/v1/isms/people/{uuid.uuid4()}/quizzes")
    assert (missing.status_code, missing.json()["detail"]) == (
        404,
        "Person not found",
    )
