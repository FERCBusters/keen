"""Owners of controls and clauses, and each owner's My controls.

Administrators choose the active accounts answerable for a control or a
clause; everyone who opens it sees them, and owners find theirs under My
controls.
"""

import uuid

import pytest
from app.db.models import ControlItem, EntityChangelog, Framework, FrameworkClause
from app.services import features

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401


@pytest.fixture
def lab(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add_all([Framework(slug="A", name="Framework A"), Framework(slug="B")])
    rows = {
        "A.2": ControlItem(
            framework_slug="A", ref="A.2", type="annex_control", title="Two"
        ),
        "A.10": ControlItem(
            framework_slug="A", ref="A.10", type="annex_control", title="Ten"
        ),
        "B.1": ControlItem(framework_slug="B", ref="B.1", type="annex_control"),
        "4.1": FrameworkClause(framework_slug="A", ref="4.1", title="Context"),
    }
    db.add_all(rows.values())
    db.commit()
    admin = keen.client(keen.user(role="admin"))
    ada = keen.user()
    ben = keen.user()

    def put(kind, target, user_ids, client=admin):
        return client.put(
            f"/v1/admin/{kind}s/{target}/owners",
            json={"user_ids": [str(u) for u in user_ids]},
            headers=unsafe(client),
        )

    return keen, rows, admin, ada, ben, put


def test_administrators_choose_owners_whom_everyone_sees(lab):
    app, rows, admin, ada, ben, put = lab
    control = rows["A.2"].id
    response = put("control", control, [ben.id, ada.id, ben.id])
    assert response.status_code == 200, response.text
    owners = sorted([ada, ben], key=lambda u: u.username)
    expected = [{"id": str(u.id), "username": u.username} for u in owners]
    assert response.json() == {"owners": expected}
    reader = app.client(app.user())
    assert reader.get(f"/v1/controls/{control}").json()["owners"] == expected
    # The change is recorded with the owners' names.
    entry = (
        app.db.query(EntityChangelog)
        .filter_by(entity_type="control", entity_id=control)
        .one()
    )
    assert entry.after_state["owners"] == [u.username for u in owners]
    assert entry.before_state["owners"] == []

    clause = rows["4.1"].id
    assert put("clause", clause, [ada.id]).status_code == 200
    assert reader.get(f"/v1/clauses/{clause}").json()["owners"] == [
        {"id": str(ada.id), "username": ada.username}
    ]
    # Clearing leaves none.
    assert put("clause", clause, []).json() == {"owners": []}


def test_owners_must_be_active_accounts_chosen_by_an_administrator(lab):
    app, rows, admin, ada, ben, put = lab
    retired = app.user()
    retired.is_active = False
    app.db.commit()
    control = rows["A.2"].id
    for target, ids, status, detail in (
        (control, [retired.id], 400, "Unknown or inactive owner"),
        (control, [uuid.uuid4()], 400, "Unknown or inactive owner"),
        (control, [app.user().id for _ in range(21)], 400, "Choose at most 20 owners"),
        ("x", [], 400, "control_id must be a UUID"),
        (uuid.uuid4(), [], 404, "Control not found"),
    ):
        refused = put("control", target, ids)
        assert refused.status_code == status, refused.text
        assert refused.json()["detail"] == detail
    assert put("clause", uuid.uuid4(), []).json()["detail"] == "Clause not found"
    assert put("control", control, [ada.id], app.client(ada)).status_code == 403
    assert app.client(ada).get(f"/v1/controls/{control}").json()["owners"] == []


def test_owners_find_what_they_own_under_my_controls(lab):
    app, rows, admin, ada, ben, put = lab
    for ref in ("A.10", "A.2", "B.1"):
        put("control", rows[ref].id, [ada.id])
    put("clause", rows["4.1"].id, [ada.id, ben.id])
    mine = app.client(ada)
    items = mine.get("/v1/me/controls").json()["items"]
    # By framework name, clauses before controls, references in order.
    assert [(i["kind"], i["framework_name"], i["ref"]) for i in items] == [
        ("control", "B", "B.1"),
        ("clause", "Framework A", "4.1"),
        ("control", "Framework A", "A.2"),
        ("control", "Framework A", "A.10"),
    ]
    assert items[2] == {
        "kind": "control",
        "id": str(rows["A.2"].id),
        "framework": "A",
        "ref": "A.2",
        "title": "Two",
        "in_scope": True,
        "framework_name": "Framework A",
    }
    assert [
        i["ref"] for i in app.client(ben).get("/v1/me/controls").json()["items"]
    ] == ["4.1"]
    features.update(app.db, app.user(role="admin"), 0, {"clauses": False})
    assert [i["kind"] for i in mine.get("/v1/me/controls").json()["items"]] == [
        "control"
    ] * 3
    features.update(app.db, app.user(role="admin"), 1, {"controls": False})
    assert mine.get("/v1/me/controls").json() == {"items": []}
    refused = app.client().get("/v1/me/controls")
    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Not authenticated"},
    )
