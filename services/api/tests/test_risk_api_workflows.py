"""Risk register workflows through the assembled application.

Requests use real password sign-in, CSRF and permission resolution against
persisted SQL state (PostgreSQL when KEEN_CONTRACT_TEST_DATABASE_URL is set).
"""

import uuid
from types import SimpleNamespace

import pytest
from app.db.models import (
    ControlItem,
    EntityChangelog,
    Framework,
    IsmsLicense,
    IsmsOrgNode,
    IsmsOrgNodeUser,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
)

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

HOSTILE = '<img src=x onerror="alert(1)">'


def _send(client, method, path, body=None, status=200, **kwargs):
    headers = unsafe(client) if method != "GET" else {}
    response = client.request(
        method,
        path,
        headers=headers,
        **({"json": body} if body is not None else {}),
        **kwargs,
    )
    assert response.status_code == status, response.text
    return response.json() if response.content else None


@pytest.fixture
def reg(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add_all([Framework(slug="A"), Framework(slug="B")])
    db.commit()
    manager = keen.user("risk.manage")
    reader = keen.user("risk.read")
    manager_client = keen.client(manager)
    reader_client = keen.client(reader)

    def as_user(user):
        client = keen.client(user)
        return lambda *args, **kwargs: _send(client, *args, **kwargs)

    return SimpleNamespace(
        keen=keen,
        db=db,
        manager=manager,
        reader=reader,
        manage=lambda *args, **kwargs: _send(manager_client, *args, **kwargs),
        read=lambda *args, **kwargs: _send(reader_client, *args, **kwargs),
        as_user=as_user,
    )


def _risk_body(**overrides):
    body = {
        "asset_name": "Payroll database",
        "category_name": "Data",
        "subcategory_name": "Databases",
        "risk_types": ["Confidentiality"],
        "threat_summary": "Unauthorised export of salary records",
        "register_likelihood": 3,
        "register_impact": 4,
        "register_residual_likelihood": 2,
        "register_residual_impact": 2,
        "framework": "A",
    }
    body.update(overrides)
    return body


def _create(reg, **overrides):
    return reg.manage("POST", "/v1/risks", _risk_body(**overrides))


def _control(db, ref, *, framework="A", kind="annex_control", title=None):
    row = ControlItem(
        framework_slug=framework, ref=ref, type=kind, title=title or f"{ref} title"
    )
    db.add(row)
    db.commit()
    return row


def _org_node(db, name, *, node_type="role", members=()):
    node = IsmsOrgNode(name=name, node_type=node_type)
    db.add(node)
    db.flush()
    for user in members:
        db.add(IsmsOrgNodeUser(org_node_id=node.id, user_id=user.id))
    db.commit()
    return node


def _ids(payload):
    return [item["id"] for item in payload["items"]]


# --- Authorisation ------------------------------------------------------------


def test_risk_routes_require_sign_in_and_the_matching_permission(reg):
    app = reg.keen
    risk = _create(reg)
    control = _control(reg.db, "A.5.1")
    anonymous = app.client()
    for path in ("/v1/risks", "/v1/me/risks", f"/v1/risks/{risk['id']}"):
        assert anonymous.get(path).status_code == 401, path

    nobody = app.client(app.user())
    read_paths = (
        "/v1/risks",
        "/v1/risks/categories",
        "/v1/risks/subcategories",
        "/v1/risks/assets",
        "/v1/risks/controls-without-risks",
        "/v1/risks/users",
        f"/v1/risks/{risk['id']}",
        f"/v1/risks/{risk['id']}/changelog",
        f"/v1/risks/{risk['id']}/controls",
        f"/v1/controls/{control.id}/risks?framework=A",
    )
    for path in read_paths:
        assert nobody.get(path).status_code == 403, path
    _send(nobody, "POST", "/v1/risks/mitigator/analyse", {"issue": "x"}, status=403)

    # Readers see the register but cannot change it or list owner choices.
    for path in read_paths:
        if path != "/v1/risks/users":
            reg.read("GET", path)
    reg.read("GET", "/v1/risks/users", status=403)
    category_id = risk["category"]["id"]
    subcategory_id = risk["subcategory"]["id"]
    writes = (
        ("POST", "/v1/risks", _risk_body()),
        ("PATCH", f"/v1/risks/{risk['id']}", {"note": "changed"}),
        ("DELETE", f"/v1/risks/{risk['id']}", None),
        ("PATCH", f"/v1/risks/{risk['id']}/controls", {"controls": []}),
        ("POST", "/v1/risks/categories", {"name": "Facilities"}),
        ("PATCH", f"/v1/risks/categories/{category_id}", {"name": "Renamed"}),
        ("DELETE", f"/v1/risks/categories/{category_id}", None),
        (
            "POST",
            "/v1/risks/subcategories",
            {"name": "Backups", "category_id": category_id},
        ),
        ("PATCH", f"/v1/risks/subcategories/{subcategory_id}", {"name": "Renamed"}),
        ("DELETE", f"/v1/risks/subcategories/{subcategory_id}", None),
        (
            "POST",
            "/v1/risks/assets",
            {"name": "CRM", "category_id": category_id},
        ),
        ("PATCH", f"/v1/risks/assets/{risk['asset_id']}", {"name": "Renamed"}),
        ("DELETE", f"/v1/risks/assets/{risk['asset_id']}", None),
    )
    for method, path, body in writes:
        reg.read(method, path, body, status=403)
        _send(nobody, method, path, body, status=403)

    # Nothing changed despite the refused writes.
    current = reg.read("GET", f"/v1/risks/{risk['id']}", params={"framework": "A"})
    assert current["note"] == "" and current["asset"] == "Payroll database"
    assert reg.db.query(Risk).count() == 1
    assert reg.db.query(RiskCategory).count() == 1
    assert reg.db.query(RiskAsset).count() == 1


def test_risk_owners_read_their_own_risks_without_register_access(reg):
    app = reg.keen
    owner = app.user()
    member = app.user()
    stranger = app.user()
    role = _org_node(reg.db, "Data protection lead", members=[member])
    owned = _create(reg, risk_owner_user_id=str(owner.id), register_impact=5)
    owned_low = _create(
        reg,
        asset_name="Expense tool",
        risk_owner_user_id=str(owner.id),
        register_likelihood=1,
        register_impact=1,
    )
    by_role = _create(reg, asset_name="Laptop fleet", risk_owner_role_id=str(role.id))
    unowned = _create(reg, asset_name="Website")

    as_owner = reg.as_user(owner)
    as_member = reg.as_user(member)
    as_stranger = reg.as_user(stranger)
    for path in ("", "/changelog", "/controls"):
        as_owner("GET", f"/v1/risks/{owned['id']}{path}")
        as_member("GET", f"/v1/risks/{by_role['id']}{path}")
        as_owner("GET", f"/v1/risks/{by_role['id']}{path}", status=403)
        as_member("GET", f"/v1/risks/{owned['id']}{path}", status=403)
        as_stranger("GET", f"/v1/risks/{unowned['id']}{path}", status=403)

    # Ownership grants read access to the owned risk alone; the register and
    # edits stay forbidden.
    as_owner("GET", "/v1/risks", status=403)
    as_owner("PATCH", f"/v1/risks/{owned['id']}", {"note": "mine"}, status=403)

    mine = as_owner("GET", "/v1/me/risks", params={"framework": "A"})
    assert _ids(mine) == [owned["id"], owned_low["id"]]  # highest score first
    assert mine["total"] == 2 and mine["framework"] == "A"
    assert _ids(as_member("GET", "/v1/me/risks")) == [by_role["id"]]
    assert as_stranger("GET", "/v1/me/risks")["items"] == []
    paged = as_owner("GET", "/v1/me/risks", params={"limit": 1, "offset": 1})
    assert _ids(paged) == [owned_low["id"]] and paged["total"] == 2
    as_owner("GET", "/v1/me/risks", params={"framework": "../A"}, status=400)


# --- Creating, reading and changing risks -------------------------------------


def test_new_risks_normalise_types_and_derive_scores_from_register_ratings(reg):
    created = _create(
        reg,
        risk_types=["integrity", " Integrity ", "AVAILABILITY", ""],
        threat_summary=f"  {HOSTILE}  ",
        note=HOSTILE,
        register_likelihood=3,
        register_impact=4,
        register_residual_likelihood=2,
        register_residual_impact=1,
        mitigator_context={"source": HOSTILE},
    )
    assert created["risk_types"] == ["Integrity", "Availability"]
    assert created["threat_summary"] == HOSTILE  # stored as text, trimmed
    assert created["note"] == HOSTILE
    assert created["mitigator_context"] == {"source": HOSTILE}
    assert (created["risk_score"], created["risk_score_class"]) == (12, "medium")
    assert (created["residual_risk_score"], created["residual_risk_score_class"]) == (
        2,
        "low",
    )
    # Legacy score columns mirror the register ratings for older consumers.
    assert (
        created["threat_score"],
        created["vulnerability_score"],
        created["impact_score"],
        created["residual_vulnerability_score"],
        created["residual_impact_score"],
    ) == (1, 3, 4, 2, 1)
    assert created["created_by"] == reg.manager.username
    assert created["asset"] == "Payroll database"
    assert created["category"]["name"] == "Data"
    assert created["subcategory"]["name"] == "Databases"
    assert created["framework"] == "A" and created["controls"] == []
    assert created["risk_owner"] == {
        "id": None,
        "type": "user",
        "name": None,
        "username": None,
    }

    severe = _create(reg, asset_name="Core network", register_likelihood=5)
    assert severe["register_impact"] == 4 and severe["risk_score"] == 20
    assert severe["risk_score_class"] == "high"
    # The same asset, category and subcategory are reused case-insensitively.
    again = _create(
        reg,
        asset_name="PAYROLL DATABASE",
        category_name="data",
        subcategory_name="databases",
    )
    assert again["asset_id"] == created["asset_id"]
    categories = reg.read("GET", "/v1/risks/categories")["items"]
    assert [c["name"] for c in categories] == ["Data"]
    assert [s["name"] for s in categories[0]["subcategories"]] == ["Databases"]

    changelog = reg.read("GET", f"/v1/risks/{created['id']}/changelog")
    assert [item["action"] for item in changelog["items"]] == ["created"]
    entry = changelog["items"][0]
    assert entry["changed_by_username"] == reg.manager.username
    assert entry["request_path"] == "/v1/risks"


@pytest.mark.parametrize(
    "overrides,status,detail",
    [
        (
            {"risk_types": ["Confidentiality", "Safety"]},
            400,
            "risk_types must contain only Confidentiality, Integrity or Availability",
        ),
        ({"register_likelihood": 6}, 422, None),
        ({"register_impact": 0}, 422, None),
        (
            {"vulnerability_score": 9},
            400,
            "vulnerability_score must be between 0 and 5",
        ),
        ({"residual_impact_score": -1}, 400, "residual_impact_score must be between"),
        ({"framework": "A B"}, 400, "Invalid framework"),
        ({"framework": "../A"}, 400, "Invalid framework"),
        ({"framework": "x" * 65}, 422, None),
        ({"asset_name": "   "}, 400, "asset is required"),
        ({"category_name": None}, 400, "asset category is required"),
        ({"category_name": "  "}, 400, "asset category is required"),
        ({"subcategory_name": None}, 400, "asset subcategory is required"),
        (
            {"category_id": "00000000-0000-4000-8000-000000000000"},
            400,
            "Unknown asset category",
        ),
        (
            {"risk_owner_user_id": "00000000-0000-4000-8000-000000000000"},
            400,
            "Unknown or inactive risk owner",
        ),
        (
            {"risk_owner_username": "nobody-by-this-name"},
            400,
            "Unknown or inactive risk owner",
        ),
        (
            {"risk_owner_role_id": "00000000-0000-4000-8000-000000000000"},
            400,
            "Unknown organisational role",
        ),
        ({"controls": ["NOT-A-CONTROL"]}, 400, "Unknown control for A: NOT-A-CONTROL"),
        (
            {"controls": [f"C-{n}" for n in range(1001)]},
            400,
            "Too many controls",
        ),
        ({"threat_summary": "x" * 12001}, 422, None),
        (
            {"mitigator_context": {"answers": "x" * 50001}},
            400,
            "mitigator_context is too large",
        ),
        (
            {"library_template_id": "00000000-0000-4000-8000-000000000000"},
            400,
            "Unknown risk library template",
        ),
        ({"asset_id": "not-a-uuid"}, 422, None),
    ],
)
def test_invalid_risks_are_rejected_without_saving_anything(
    reg, overrides, status, detail
):
    response = reg.manage("POST", "/v1/risks", _risk_body(**overrides), status=status)
    if detail:
        assert response["detail"].startswith(detail)
    # Assets and categories found or created for the rejected risk roll back too.
    assert reg.db.query(Risk).count() == 0
    assert reg.db.query(RiskAsset).count() == 0
    assert reg.db.query(RiskCategory).count() == 0


def test_risks_for_an_unknown_asset_id_are_rejected(reg):
    body = {
        "asset_id": str(uuid.uuid4()),
        "risk_types": ["Integrity"],
        "framework": "A",
    }
    response = reg.manage("POST", "/v1/risks", body, status=404)
    assert response["detail"] == "Asset not found"
    assert reg.db.query(Risk).count() == 0


def test_risks_can_reuse_an_existing_asset_by_id(reg):
    first = _create(reg)
    body = {
        "asset_id": first["asset_id"],
        "risk_types": ["Availability"],
        "threat_summary": "Storage outage",
        "framework": "A",
    }
    second = reg.manage("POST", "/v1/risks", body)
    assert second["asset_id"] == first["asset_id"]
    assert second["category"] == first["category"]
    assert reg.db.query(RiskAsset).count() == 1


def test_risk_updates_change_only_the_fields_sent(reg):
    owner = reg.keen.user()
    created = _create(reg, risk_owner_user_id=str(owner.id), note="Initial note")
    path = f"/v1/risks/{created['id']}"

    noted = reg.manage("PATCH", path, {"note": HOSTILE, "framework": "A"})
    assert noted["note"] == HOSTILE
    for field in (
        "risk_owner",
        "risk_types",
        "asset_id",
        "threat_summary",
        "register_likelihood",
        "register_impact",
        "risk_score",
        "residual_risk_score",
    ):
        assert noted[field] == created[field], field

    rerated = reg.manage("PATCH", path, {"register_impact": 5})
    assert rerated["register_likelihood"] == 3
    assert (rerated["risk_score"], rerated["risk_score_class"]) == (15, "high")
    assert rerated["impact_score"] == 5
    assert rerated["residual_risk_score"] == created["residual_risk_score"]

    retyped = reg.manage("PATCH", path, {"risk_types": ["availability"]})
    assert retyped["risk_types"] == ["Availability"]
    # Clearing the CIA associations makes it a general register entry.
    assert reg.manage("PATCH", path, {"risk_types": []})["method"] == "general"

    # Older clients that still send the three-factor scores update the ratings.
    legacy = reg.manage(
        "PATCH",
        path,
        {"vulnerability_score": 4, "residual_impact_score": 3},
    )
    assert legacy["register_likelihood"] == 4
    assert legacy["register_residual_impact"] == 3
    assert legacy["risk_score"] == 20
    assert legacy["residual_risk_score"] == 2 * 3

    reg.manage("PATCH", path, {"framework": "A B"}, status=400)
    reg.manage("PATCH", "/v1/risks/not-a-uuid", {"note": "x"}, status=400)
    reg.manage("PATCH", f"/v1/risks/{uuid.uuid4()}", {"note": "x"}, status=404)

    final = reg.read("GET", path, params={"framework": "A"})
    assert final["risk_owner"]["id"] == str(owner.id)
    assert final["note"] == HOSTILE
    changelog = reg.read("GET", f"{path}/changelog")
    actions = [item["action"] for item in changelog["items"]]
    assert actions.count("updated") == 5 and actions[-1] == "created"
    note_change = next(
        change
        for item in changelog["items"]
        for change in item["changes"]
        if change["field"] == "note"
    )
    assert (note_change["before"], note_change["after"]) == ("Initial note", HOSTILE)


def test_risk_owner_is_either_a_user_or_an_organisational_role(reg):
    app = reg.keen
    first = app.user()
    second = app.user()
    inactive = app.user()
    inactive.is_active = False
    reg.db.commit()
    role = _org_node(reg.db, "Security officer")
    team = _org_node(reg.db, "Platform team", node_type="team")

    created = _create(reg, risk_owner_username=f"  {first.username}  ")
    assert created["risk_owner"]["id"] == str(first.id)
    path = f"/v1/risks/{created['id']}"

    by_role = reg.manage("PATCH", path, {"risk_owner_role_id": str(role.id)})
    assert by_role["risk_owner"] == {
        "id": str(role.id),
        "type": "role",
        "name": "Security officer",
        "username": "Security officer (role)",
    }
    both = {"risk_owner_role_id": str(role.id), "risk_owner_user_id": str(first.id)}
    response = reg.manage("PATCH", path, both, status=400)
    assert response["detail"] == "Choose one owner: a user or an organisational role"
    response = reg.manage(
        "PATCH", path, {"risk_owner_role_id": str(team.id)}, status=400
    )
    assert response["detail"] == "Unknown organisational role"
    response = reg.manage(
        "PATCH", path, {"risk_owner_user_id": str(inactive.id)}, status=400
    )
    assert response["detail"] == "Unknown or inactive risk owner"
    assert reg.read("GET", path)["risk_owner"]["type"] == "role"

    by_user = reg.manage("PATCH", path, {"risk_owner_user_id": str(second.id)})
    assert by_user["risk_owner"]["id"] == str(second.id)
    assert by_user["risk_owner"]["type"] == "user"
    row = reg.db.get(Risk, uuid.UUID(created["id"]))
    reg.db.refresh(row)
    assert row.risk_owner_role_id is None

    cleared = reg.manage("PATCH", path, {"risk_owner_user_id": None})
    assert cleared["risk_owner"]["id"] is None


def test_owner_choices_list_active_users_and_organisational_roles(reg):
    app = reg.keen
    active = app.user()
    inactive = app.user()
    inactive.is_active = False
    reg.db.commit()
    role = _org_node(reg.db, HOSTILE)
    _org_node(reg.db, "Engineering", node_type="team")

    choices = reg.manage("GET", "/v1/risks/users")
    usernames = [item["username"] for item in choices["items"]]
    assert active.username in usernames and reg.manager.username in usernames
    assert inactive.username not in usernames
    assert usernames == sorted(usernames)
    assert choices["roles"] == [{"id": str(role.id), "name": HOSTILE}]


def test_changing_a_risk_asset_reassigns_the_risk_without_renaming_the_shared_asset(
    reg,
):
    first = _create(reg)
    second = _create(reg, threat_summary="Backup restore failure")
    assert first["asset_id"] == second["asset_id"]
    path = f"/v1/risks/{first['id']}"

    moved = reg.manage("PATCH", path, {"asset_name": "HR portal"})
    assert moved["asset"] == "HR portal"
    assert moved["asset_id"] != first["asset_id"]
    assert moved["category"]["name"] == "Data"
    assert moved["subcategory"]["name"] == "Databases"
    unchanged = reg.read("GET", f"/v1/risks/{second['id']}")
    assert unchanged["asset"] == "Payroll database"

    back = reg.manage("PATCH", path, {"asset_id": first["asset_id"]})
    assert back["asset_id"] == first["asset_id"]

    # A subcategory belongs to one category, so a category change needs a new one.
    response = reg.manage("PATCH", path, {"category_name": "Facilities"}, status=400)
    assert response["detail"] == "asset subcategory is required"
    recategorised = reg.manage(
        "PATCH",
        path,
        {"category_name": "Facilities", "subcategory_name": "Buildings"},
    )
    assert recategorised["asset"] == "Payroll database"
    assert recategorised["category"]["name"] == "Facilities"
    assert recategorised["subcategory"]["name"] == "Buildings"
    assert recategorised["asset_id"] not in {first["asset_id"], moved["asset_id"]}
    assert reg.read("GET", f"/v1/risks/{second['id']}")["category"]["name"] == "Data"


def test_deleting_a_risk_records_the_removal_and_keeps_its_asset(reg):
    created = _create(reg, threat_summary=HOSTILE)
    path = f"/v1/risks/{created['id']}"
    assert reg.manage("DELETE", path) == {"ok": True}
    reg.read("GET", path, status=404)
    reg.manage("DELETE", path, status=404)
    reg.manage("DELETE", "/v1/risks/not-a-uuid", status=400)
    reg.read("GET", "/v1/risks/not-a-uuid", status=400)

    removed = (
        reg.db.query(EntityChangelog)
        .filter_by(entity_id=uuid.UUID(created["id"]), action="deleted")
        .one()
    )
    assert removed.entity_type == "risk"
    assert removed.changed_by_username == reg.manager.username
    assert reg.db.get(RiskAsset, uuid.UUID(created["asset_id"])) is not None


# --- Controls -----------------------------------------------------------------


def test_risk_controls_are_linked_by_id_or_reference_within_one_framework(reg):
    a10 = _control(reg.db, "A.5.10")
    a2 = _control(reg.db, "A.5.2")
    custom = _control(reg.db, "C-1", kind="custom")
    b1 = _control(reg.db, "B.1", framework="B")

    created = _create(
        reg, controls=["A.5.10", str(a2.id), " A.5.10 ", "", "C-1", str(a2.id)]
    )
    assert [c["ref"] for c in created["controls"]] == ["A.5.2", "A.5.10", "C-1"]
    assert created["control_count"] == 3
    path = f"/v1/risks/{created['id']}"

    in_b = reg.read("GET", path, params={"framework": "B"})
    assert in_b["control_count"] == 0 and in_b["controls"] == []
    response = reg.manage(
        "PATCH",
        f"{path}/controls",
        {"controls": ["A.5.2"]},
        status=400,
        params={"framework": "B"},
    )
    assert response["detail"] == "Unknown control for B: A.5.2"

    linked_b = reg.manage(
        "PATCH",
        f"{path}/controls",
        {"controls": [str(b1.id)]},
        params={"framework": "B"},
    )
    assert linked_b["framework"] == "B"
    assert [c["id"] for c in linked_b["controls"]] == [str(b1.id)]
    listed = reg.read("GET", f"{path}/controls", params={"framework": "A"})
    assert [c["ref"] for c in listed["items"]] == ["A.5.2", "A.5.10", "C-1"]

    # Replacing one framework's links leaves the other framework's links alone.
    trimmed = reg.manage("PATCH", path, {"framework": "A", "controls": ["C-1"]})
    assert [c["id"] for c in trimmed["controls"]] == [str(custom.id)]
    assert reg.read("GET", f"{path}/controls", params={"framework": "B"})["items"] == [
        linked_b["controls"][0]
    ]
    kept = reg.manage("PATCH", path, {"framework": "A", "note": "no control change"})
    assert kept["control_count"] == 1

    changelog = reg.read("GET", f"{path}/changelog")["items"]
    assert changelog[0]["request_path"] == f"/v1/risks/{created['id']}"
    control_change = next(
        item for item in changelog if item["request_path"].endswith("/controls")
    )
    assert any(change["field"] == "controls" for change in control_change["changes"])

    reg.manage("PATCH", "/v1/risks/not-a-uuid/controls", {"controls": []}, status=400)
    reg.read("GET", f"/v1/risks/{uuid.uuid4()}/controls", status=404)
    reg.read("GET", f"{path}/controls", params={"framework": "A B"}, status=400)
    assert a10.id  # linked above by reference


def test_control_risk_lists_follow_the_requested_framework(reg):
    control = _control(reg.db, "A.8.1")
    other = _control(reg.db, "B.8.1", framework="B")
    low = _create(reg, asset_name="Kiosk", controls=[str(control.id)])
    high = _create(
        reg, asset_name="Mail server", register_likelihood=5, controls=["A.8.1"]
    )
    _create(reg, asset_name="Unlinked")

    listed = reg.read(
        "GET", f"/v1/controls/{control.id}/risks", params={"framework": "A"}
    )
    assert listed["control"]["ref"] == "A.8.1"
    assert _ids(listed) == [high["id"], low["id"]]
    reg.read(
        "GET", f"/v1/controls/{other.id}/risks", params={"framework": "A"}, status=404
    )
    reg.read("GET", "/v1/controls/not-a-uuid/risks", status=400)


def test_controls_without_risks_exclude_clauses_and_linked_controls(reg):
    linked = _control(reg.db, "A.5.1")
    _control(reg.db, "A.5.10", title="Acceptable use of information")
    _control(reg.db, "A.5.2", title="Information security roles")
    _control(reg.db, "C-1", kind="custom", title=HOSTILE)
    _control(reg.db, "4.1", kind="clause")
    _control(reg.db, "B.1", framework="B")
    _create(reg, controls=[str(linked.id)])

    path = "/v1/risks/controls-without-risks"
    unlinked = reg.read("GET", path, params={"framework": "A"})
    assert [c["ref"] for c in unlinked["items"]] == ["A.5.2", "A.5.10", "C-1"]
    assert unlinked["total"] == 3
    assert unlinked["items"][2]["title"] == HOSTILE

    by_title = reg.read("GET", path, params={"framework": "A", "q": "ACCEPTABLE"})
    assert [c["ref"] for c in by_title["items"]] == ["A.5.10"]
    by_type = reg.read("GET", path, params={"framework": "A", "q": "custom"})
    assert [c["ref"] for c in by_type["items"]] == ["C-1"]
    page = reg.read("GET", path, params={"framework": "A", "limit": 1, "offset": 1})
    assert [c["ref"] for c in page["items"]] == ["A.5.10"]
    assert (page["total"], page["limit"], page["offset"]) == (3, 1, 1)
    other = reg.read("GET", path, params={"framework": "B"})
    assert [c["ref"] for c in other["items"]] == ["B.1"]


# --- Register listing -----------------------------------------------------------


@pytest.fixture
def register(reg):
    owner = reg.keen.user()
    role = _org_node(reg.db, "Facilities manager")
    severe = _create(
        reg,
        risk_owner_user_id=str(owner.id),
        register_likelihood=5,
        register_impact=5,
    )
    laptops = _create(
        reg,
        asset_name="Laptop fleet",
        category_name="Hardware",
        subcategory_name="Endpoints",
        risk_types=["Availability"],
        threat_summary="Theft while travelling",
        note="Battery swelling noted in audit",
        risk_owner_role_id=str(role.id),
        register_likelihood=2,
        register_impact=2,
    )
    website = _create(
        reg,
        asset_name="Website",
        category_name="Software",
        subcategory_name="Web",
        risk_types=["Integrity", "Availability"],
        threat_summary="Defacement",
        register_likelihood=3,
        register_impact=3,
    )
    return SimpleNamespace(
        owner=owner, role=role, severe=severe, laptops=laptops, website=website
    )


def test_register_lists_risks_by_score_with_text_search(reg, register):
    def listed(**params):
        return reg.read("GET", "/v1/risks", params={"framework": "A", **params})

    everything = listed()
    assert _ids(everything) == [
        register.severe["id"],
        register.website["id"],
        register.laptops["id"],
    ]
    assert everything["total"] == 3
    assert _ids(listed(q="BATTERY")) == [register.laptops["id"]]  # note
    assert _ids(listed(q="defacement")) == [register.website["id"]]  # threat
    assert _ids(listed(q="hardware")) == [register.laptops["id"]]  # category
    assert _ids(listed(q="endpoints")) == [register.laptops["id"]]  # subcategory
    assert _ids(listed(q="payroll")) == [register.severe["id"]]  # asset
    assert listed(q=HOSTILE)["items"] == []

    page = listed(limit=1, offset=1)
    assert _ids(page) == [register.website["id"]]
    assert (page["total"], page["limit"], page["offset"]) == (3, 1, 1)
    clamped = listed(limit=5000, offset=-5)
    assert (clamped["limit"], clamped["offset"]) == (1000, 0)
    reg.read("GET", "/v1/risks", params={"framework": "A B"}, status=400)


def test_register_filters_by_category_owner_and_risk_type(reg, register):
    def listed(status=200, **params):
        return reg.read(
            "GET", "/v1/risks", params={"framework": "A", **params}, status=status
        )

    categories = (
        f"{register.severe['category']['id']}, {register.laptops['category']['id']}"
    )
    assert _ids(listed(category_id=categories)) == [
        register.severe["id"],
        register.laptops["id"],
    ]
    assert _ids(listed(subcategory_id=register.website["subcategory"]["id"])) == [
        register.website["id"]
    ]
    assert _ids(listed(owner_user_id=str(register.owner.id))) == [register.severe["id"]]
    assert _ids(listed(owner_role_id=str(register.role.id))) == [register.laptops["id"]]

    rejected = {
        "category_id": "not-a-uuid",
        "subcategory_id": f"{uuid.uuid4()},nope",
        "owner_user_id": "nope",
        "owner_role_id": "nope",
        "risk_type": "Safety",
    }
    for name, value in rejected.items():
        listed(status=400, **{name: value})

    if reg.keen.postgres:  # JSON containment runs on PostgreSQL only.
        assert _ids(listed(risk_type="availability")) == [
            register.website["id"],
            register.laptops["id"],
        ]
        assert _ids(listed(risk_type="Integrity, CONFIDENTIALITY")) == [
            register.severe["id"],
            register.website["id"],
        ]


# --- Asset categories, subcategories and assets ---------------------------------


def test_asset_categories_are_unique_and_start_with_a_general_subcategory(reg):
    created = reg.manage("POST", "/v1/risks/categories", {"name": f" {HOSTILE} "})
    assert created["name"] == HOSTILE
    listed = reg.read("GET", "/v1/risks/categories")["items"]
    assert [(c["name"], [s["name"] for s in c["subcategories"]]) for c in listed] == [
        (HOSTILE, ["General"])
    ]
    duplicate = reg.manage(
        "POST", "/v1/risks/categories", {"name": HOSTILE.upper()}, status=400
    )
    assert duplicate["detail"] == "Asset category already exists"
    blank = reg.manage("POST", "/v1/risks/categories", {"name": "   "}, status=400)
    assert blank["detail"] == "name is required"
    reg.manage("POST", "/v1/risks/categories", {"name": "x" * 129}, status=422)

    data = reg.manage("POST", "/v1/risks/categories", {"name": "Data"})
    path = f"/v1/risks/categories/{created['id']}"
    renamed = reg.manage("PATCH", path, {"name": "Facilities"})
    assert renamed == {"id": created["id"], "name": "Facilities"}
    conflict = reg.manage("PATCH", path, {"name": "DATA"}, status=400)
    assert conflict["detail"] == "Asset category already exists"
    # Renaming a category to a different case of its own name is allowed.
    assert reg.manage("PATCH", path, {"name": "FACILITIES"})["name"] == "FACILITIES"
    reg.manage("PATCH", "/v1/risks/categories/nope", {"name": "X"}, status=400)
    reg.manage(
        "PATCH", f"/v1/risks/categories/{uuid.uuid4()}", {"name": "X"}, status=404
    )
    names = [c["name"] for c in reg.read("GET", "/v1/risks/categories")["items"]]
    assert names == ["Data", "FACILITIES"]
    assert data["name"] == "Data"


def test_asset_categories_in_use_cannot_be_deleted(reg):
    risk = _create(reg)
    category_path = f"/v1/risks/categories/{risk['category']['id']}"
    subcategory_path = f"/v1/risks/subcategories/{risk['subcategory']['id']}"
    asset_path = f"/v1/risks/assets/{risk['asset_id']}"

    response = reg.manage("DELETE", category_path, status=400)
    assert response["detail"] == "Cannot delete a category used by assets"
    response = reg.manage("DELETE", subcategory_path, status=400)
    assert response["detail"] == "Cannot delete a subcategory used by assets"
    response = reg.manage("DELETE", asset_path, status=400)
    assert response["detail"] == "Cannot delete an asset used by risks"

    reg.manage("DELETE", f"/v1/risks/{risk['id']}")
    assert reg.manage("DELETE", asset_path) == {"ok": True}
    assert reg.manage("DELETE", subcategory_path) == {"ok": True}
    general = reg.manage(
        "POST",
        "/v1/risks/subcategories",
        {"name": "Archive", "category_id": risk["category"]["id"]},
    )
    assert reg.manage("DELETE", category_path) == {"ok": True}
    # Deleting a category removes its remaining subcategories.
    assert reg.db.get(RiskAssetSubcategory, uuid.UUID(general["id"])) is None
    assert reg.read("GET", "/v1/risks/categories")["items"] == []

    for kind in ("categories", "subcategories", "assets"):
        reg.manage("DELETE", f"/v1/risks/{kind}/not-a-uuid", status=400)
        reg.manage("DELETE", f"/v1/risks/{kind}/{uuid.uuid4()}", status=404)


def test_subcategories_are_unique_within_their_category(reg):
    data = reg.manage("POST", "/v1/risks/categories", {"name": "Data"})
    hardware = reg.manage("POST", "/v1/risks/categories", {"name": "Hardware"})
    create = "/v1/risks/subcategories"

    hostile = reg.manage("POST", create, {"name": HOSTILE, "category_id": data["id"]})
    assert hostile == {"id": hostile["id"], "name": HOSTILE, "category_id": data["id"]}
    response = reg.manage(
        "POST", create, {"name": HOSTILE.upper(), "category_id": data["id"]}, status=400
    )
    assert response["detail"] == "Asset subcategory already exists"
    servers = reg.manage("POST", create, {"name": "Servers", "category_id": data["id"]})
    elsewhere = reg.manage(
        "POST", create, {"name": "servers", "category_id": hardware["id"]}
    )
    response = reg.manage("POST", create, {"name": "Orphan"}, status=400)
    assert response["detail"] == "asset category is required"
    response = reg.manage(
        "POST", create, {"name": "Orphan", "category_id": str(uuid.uuid4())}, status=400
    )
    assert response["detail"] == "Unknown asset category"
    reg.manage("POST", create, {"name": "  ", "category_id": data["id"]}, status=400)
    reg.manage("DELETE", f"{create}/{hostile['id']}")

    listed = reg.read("GET", create, params={"category_id": data["id"]})["items"]
    assert [s["name"] for s in listed] == ["General", "Servers"]
    assert all(s["category"]["name"] == "Data" for s in listed)
    everything = reg.read("GET", create)["items"]
    assert [(s["category"]["name"], s["name"]) for s in everything] == [
        ("Data", "General"),
        ("Data", "Servers"),
        ("Hardware", "General"),
        ("Hardware", "servers"),
    ]
    reg.read("GET", create, params={"category_id": "nope"}, status=400)

    # Moving into a category that already has the name is refused.
    path = f"{create}/{servers['id']}"
    response = reg.manage(
        "PATCH", path, {"name": "SERVERS", "category_id": hardware["id"]}, status=400
    )
    assert response["detail"] == "Asset subcategory already exists"
    moved = reg.manage(
        "PATCH", path, {"name": "Storage", "category_id": hardware["id"]}
    )
    assert moved == {
        "id": servers["id"],
        "name": "Storage",
        "category_id": hardware["id"],
    }
    # Without a category, a rename stays in the current category.
    renamed = reg.manage("PATCH", f"{create}/{elsewhere['id']}", {"name": "Network"})
    assert renamed["category_id"] == hardware["id"]
    reg.manage(
        "PATCH",
        path,
        {"name": "Storage", "category_id": str(uuid.uuid4())},
        status=400,
    )
    reg.manage("PATCH", f"{create}/nope", {"name": "X"}, status=400)
    # An unknown subcategory in the path is missing, for edits and deletions alike.
    for method, body in (("PATCH", {"name": "X"}), ("DELETE", None)):
        response = reg.manage(method, f"{create}/{uuid.uuid4()}", body, status=404)
        assert response["detail"] == "Asset subcategory not found"
    names = [
        s["name"]
        for s in reg.read("GET", create, params={"category_id": hardware["id"]})[
            "items"
        ]
    ]
    assert names == ["General", "Network", "Storage"]


def test_assets_carry_licences_owners_and_descriptions(reg):
    team = _org_node(reg.db, "Finance", node_type="team")
    holder = _org_node(reg.db, "Records office", node_type="team")
    create = "/v1/risks/assets"
    crm = reg.manage(
        "POST",
        create,
        {
            "name": " CRM ",
            "category_name": "Software",
            "subcategory_name": "SaaS",
            "license_name": "Enterprise seat",
            "owner_org_node_id": str(team.id),
            "register_held_by_org_node_id": str(holder.id),
            "description": HOSTILE,
        },
    )
    assert crm["name"] == "CRM" and crm["asset"] == "CRM"
    assert crm["license"] == crm["license_name"] == "Enterprise seat"
    assert crm["license_entity"] == {
        "id": crm["license_id"],
        "name": "Enterprise seat",
    }
    assert crm["owner_org_node"] == {
        "id": str(team.id),
        "name": "Finance",
        "node_type": "team",
    }
    assert crm["register_held_by_org_node"]["name"] == "Records office"
    assert crm["description"] == HOSTILE
    assert crm["category"]["name"] == "Software"
    assert crm["subcategory"]["name"] == "SaaS"

    # The same asset is not duplicated, and licences are reused by name.
    reg.manage(
        "POST",
        create,
        {"name": "crm", "category_name": "software", "subcategory_name": "saas"},
    )
    assert reg.db.query(RiskAsset).count() == 1
    erp = reg.manage(
        "POST",
        create,
        {
            "name": "ERP",
            "category_id": crm["category"]["id"],
            "subcategory_id": crm["subcategory"]["id"],
            "license_name": "ENTERPRISE SEAT",
        },
    )
    assert erp["license_id"] == crm["license_id"]
    assert reg.db.query(IsmsLicense).count() == 1
    missing = reg.manage(
        "POST",
        create,
        {
            "name": "Ghost",
            "category_name": "Software",
            "subcategory_name": "SaaS",
            "license_id": str(uuid.uuid4()),
        },
        status=404,
    )
    assert missing["detail"] == "License not found"
    response = reg.manage(
        "POST",
        create,
        {"name": "Ghost", "category_id": crm["category"]["id"]},
        status=400,
    )
    assert response["detail"] == "asset subcategory is required"
    response = reg.manage(
        "POST",
        create,
        {
            "name": "Ghost",
            "category_id": crm["category"]["id"],
            "subcategory_id": str(uuid.uuid4()),
        },
        status=400,
    )
    assert response["detail"] == "Unknown asset subcategory"
    assert reg.db.query(RiskAsset).count() == 2


def test_assets_are_searchable_and_editable(reg):
    create = "/v1/risks/assets"
    base = {"category_name": "Software", "subcategory_name": "SaaS"}
    crm = reg.manage(
        "POST", create, {**base, "name": "CRM", "license_name": "Enterprise seat"}
    )
    erp = reg.manage("POST", create, {**base, "name": "ERP"})
    laptop = reg.manage(
        "POST",
        create,
        {
            "name": "Laptop",
            "category_name": "Hardware",
            "subcategory_name": "Endpoints",
        },
    )

    def names(**params):
        return [a["name"] for a in reg.read("GET", create, params=params)["items"]]

    assert names() == ["CRM", "ERP", "Laptop"]
    assert names(q="ENTERPRISE") == ["CRM"]  # licence name
    assert names(q="saas") == ["CRM", "ERP"]  # subcategory
    assert names(q="hardware") == ["Laptop"]  # category
    assert names(category_id=laptop["category"]["id"]) == ["Laptop"]
    assert names(subcategory_id=crm["subcategory"]["id"]) == ["CRM", "ERP"]
    reg.read("GET", create, params={"category_id": "nope"}, status=400)
    reg.read("GET", create, params={"subcategory_id": "nope"}, status=400)

    path = f"{create}/{crm['id']}"
    renamed = reg.manage("PATCH", path, {"name": "CRM platform"})
    assert renamed["name"] == "CRM platform"
    assert renamed["license"] == "Enterprise seat"  # licence fields not sent
    unlicensed = reg.manage(
        "PATCH", path, {"name": "CRM platform", "license_name": None}
    )
    assert (unlicensed["license"], unlicensed["license_id"]) == ("", None)
    described = reg.manage(
        "PATCH", path, {"name": "CRM platform", "description": f" {HOSTILE} "}
    )
    assert described["description"] == HOSTILE
    moved = reg.manage(
        "PATCH",
        path,
        {
            "name": "CRM platform",
            "category_id": laptop["category"]["id"],
            "subcategory_id": laptop["subcategory"]["id"],
        },
    )
    assert moved["category"]["name"] == "Hardware"
    assert moved["subcategory"]["name"] == "Endpoints"
    assert names(category_id=laptop["category"]["id"]) == ["CRM platform", "Laptop"]
    assert names(subcategory_id=crm["subcategory"]["id"]) == ["ERP"]

    response = reg.manage(
        "PATCH", f"{create}/{erp['id']}", {"name": "erp", **base}, status=200
    )
    assert response["name"] == "erp"
    response = reg.manage(
        "PATCH",
        path,
        {
            "name": "LAPTOP",
            "category_id": laptop["category"]["id"],
            "subcategory_id": laptop["subcategory"]["id"],
        },
        status=400,
    )
    assert response["detail"] == "Asset already exists"
    response = reg.manage("PATCH", path, {"name": "   "}, status=400)
    assert response["detail"] == "asset is required"
    reg.manage("PATCH", f"{create}/nope", {"name": "X"}, status=400)
    reg.manage("PATCH", f"{create}/{uuid.uuid4()}", {"name": "X"}, status=404)
    assert reg.db.get(RiskAsset, uuid.UUID(crm["id"])).name == "CRM platform"


# --- Mitigator analysis -----------------------------------------------------------


def test_mitigator_analysis_validates_references_without_creating_records(reg):
    # The register's write gate covers the analysis endpoint (see access_policy).
    reg.read("POST", "/v1/risks/mitigator/analyse", {"issue": "x"}, status=403)
    data = reg.manage("POST", "/v1/risks/categories", {"name": "Data"})
    hardware = reg.manage("POST", "/v1/risks/categories", {"name": "Hardware"})
    hardware_general = reg.read(
        "GET", "/v1/risks/subcategories", params={"category_id": hardware["id"]}
    )["items"][0]
    path = "/v1/risks/mitigator/analyse"

    cases = [
        ({"issue": "   "}, 400, "issue is required"),
        (
            {"issue": "x", "category_id": str(uuid.uuid4())},
            400,
            "Unknown asset category",
        ),
        (
            {
                "issue": "x",
                "category_id": data["id"],
                "subcategory_id": hardware_general["id"],
            },
            400,
            "Unknown asset subcategory",
        ),
        ({"issue": "x", "risk_types": ["Safety"]}, 400, "risk_types must contain only"),
        ({"issue": "x", "framework": "A B"}, 400, "Invalid framework"),
        ({"issue": "x", "limit": 26}, 422, None),
        ({"issue": "x" * 12001}, 422, None),
    ]
    for body, status, detail in cases:
        response = reg.manage("POST", path, body, status=status)
        if detail:
            assert response["detail"].startswith(detail), body

    analysis = reg.manage(
        "POST",
        path,
        {
            "issue": HOSTILE,
            "asset_name": "Brand new asset",
            "category_name": "Brand new category",
            "subcategory_name": "Brand new subcategory",
            "framework": "A",
            "risk_types": ["integrity"],
        },
    )
    assert analysis["framework"] == "A"
    assert analysis["draft_risk"]["threat_summary"] == HOSTILE
    assert analysis["draft_risk"]["risk_types"] == ["Integrity"]
    # Analysis suggests; it does not create assets or categories.
    assert reg.db.query(RiskCategory).count() == 2
    assert reg.db.query(RiskAsset).count() == 0
    assert reg.db.query(Risk).count() == 0


def test_a_general_risk_has_a_title_and_no_cia_associations(reg):
    # A simple register entry needs no CIA types; its title names it.
    created = _create(
        reg,
        title="  Payroll data leaves the building  ",
        risk_types=[],
        treatment_strategy="Mitigate",
        treatment_status="in_progress",
        treatment_plan="Encrypt exports",
        treatment_due_at="2026-12-01",
    )
    assert created["title"] == "Payroll data leaves the building"
    assert created["display"] == "Payroll data leaves the building"
    assert created["method"] == "general"
    assert created["risk_types"] == []
    assert (
        created["treatment_strategy"],
        created["treatment_status"],
        created["treatment_plan"],
        created["treatment_due_at"],
    ) == ("mitigate", "in_progress", "Encrypt exports", "2026-12-01")
    # Without a title the register shows the asset and threat.
    untitled = _create(reg, risk_types=None)
    assert untitled["display"] == (
        "Payroll database - Unauthorised export of salary records"
    )
    assert untitled["method"] == "general"
    cia = _create(reg, risk_types=["availability", "Confidentiality"])
    assert cia["method"] == "cia"
    assert cia["risk_types"] == ["Availability", "Confidentiality"]
    # Treatment choices come from the register's fixed lists.
    for body, detail in (
        ({"treatment_strategy": "ignore"}, "Invalid treatment strategy"),
        ({"treatment_status": "done"}, "Invalid treatment status"),
    ):
        response = reg.manage("POST", "/v1/risks", _risk_body(**body), status=400)
        assert response["detail"] == detail
    # The title is searchable and travels through the register.
    found = reg.read("GET", "/v1/risks", params={"framework": "A", "q": "leaves the"})
    assert [r["id"] for r in found["items"]] == [created["id"]]
    register = reg.read("GET", "/v1/risks/register", params={"framework": "A"})
    assert {r["id"]: r["title"] for r in register["items"]}[created["id"]] == (
        "Payroll data leaves the building"
    )
    changelog = reg.read("GET", f"/v1/risks/{created['id']}/changelog")
    assert changelog["items"][0]["entity_title"] == "Payroll data leaves the building"


def test_a_risk_names_the_parties_it_affects(reg):
    from app.db.models import (
        InterestedParty,
        InterestedPartyName,
        InterestedPartyNature,
        IsmsPerson,
        IsmsVendor,
    )

    db = reg.db
    name = InterestedPartyName(name="Customers")
    nature = InterestedPartyNature(name="External")
    db.add_all([name, nature])
    db.flush()
    party = InterestedParty(name_id=name.id, nature_id=nature.id)
    person = IsmsPerson(name="Dana", position="DPO", email="dana@example.test")
    vendor = IsmsVendor(
        name="Cloud host",
        contact="support@cloud.example",
        website="https://cloud.example",
    )
    db.add_all([party, person, vendor])
    db.commit()

    created = _create(
        reg,
        interested_parties=[
            {"interested_party_id": str(party.id)},
            {"person_id": str(person.id)},
            {"vendor_id": str(vendor.id)},
            {"vendor_id": str(vendor.id)},  # repeated references collapse
            # A party that does not exist yet is created along the way, and a
            # name with a nature that already exists is reused.
            {"name": "Regulator", "nature": "External"},
            {"name": "Customers", "nature": "external"},
        ],
    )
    parties = created["interested_parties"]
    # Labels carry a position or website, never an email address or contact.
    assert [(p["kind"], p["label"], p["detail"]) for p in parties] == [
        ("interested_party", "Customers", "External"),
        ("person", "Dana", "DPO"),
        ("vendor", "Cloud host", "https://cloud.example"),
        ("interested_party", "Regulator", "External"),
    ]
    assert "dana@example.test" not in str(parties)
    assert "support@cloud.example" not in str(parties)
    assert parties[0]["id"] == str(party.id)
    assert parties[0]["href"] == f"/interested_party.html?id={party.id}"
    # The new interested party is a first-class record in the framework.
    listed = reg.read("GET", "/v1/interested-parties", params={"framework": "A"})
    assert sorted(p["name"]["name"] for p in listed["items"]) == [
        "Customers",
        "Regulator",
    ]
    regulator = next(p for p in listed["items"] if p["name"]["name"] == "Regulator")
    assert regulator["id"] == parties[3]["id"]
    # The change history names the parties, and a later edit replaces them.
    changelog = reg.read("GET", f"/v1/risks/{created['id']}/changelog")
    created_state = changelog["items"][0]
    assert created_state["action"] == "created"
    updated = reg.manage(
        "PATCH",
        f"/v1/risks/{created['id']}",
        {"framework": "A", "interested_parties": [{"person_id": str(person.id)}]},
    )
    assert [p["kind"] for p in updated["interested_parties"]] == ["person"]
    changelog = reg.read("GET", f"/v1/risks/{created['id']}/changelog")
    change = next(
        c
        for c in changelog["items"][0]["changes"]
        if c["field"] == "interested_parties"
    )
    assert change["label"] == "Affected parties"
    assert change["after"] == ["Dana (person)"]
    # An edit that does not mention parties leaves them alone.
    reg.manage("PATCH", f"/v1/risks/{created['id']}", {"framework": "A", "note": "n"})
    assert [
        p["kind"]
        for p in reg.read("GET", f"/v1/risks/{created['id']}")["interested_parties"]
    ] == ["person"]
    # Deleting the person removes the link but not the risk.
    db.delete(db.get(IsmsPerson, person.id))
    db.commit()
    assert reg.read("GET", f"/v1/risks/{created['id']}")["interested_parties"] == []


@pytest.mark.parametrize(
    "party,detail",
    [
        ({}, "Each affected party names one interested party"),
        ({"person_id": str(uuid.uuid4()), "name": "x", "nature": "y"}, "names one"),
        ({"interested_party_id": str(uuid.uuid4())}, "Unknown interested party"),
        ({"person_id": str(uuid.uuid4())}, "Unknown person"),
        ({"vendor_id": str(uuid.uuid4())}, "Unknown vendor"),
        ({"name": "Regulator"}, "A new interested party needs a nature"),
    ],
)
def test_affected_parties_are_checked(reg, party, detail):
    response = reg.manage(
        "POST", "/v1/risks", _risk_body(interested_parties=[party]), status=400
    )
    assert detail in response["detail"]
    # Nothing was created along the way.
    assert reg.read("GET", "/v1/risks", params={"framework": "A"})["items"] == []
    assert (
        reg.read("GET", "/v1/interested-parties", params={"framework": "A"})["items"]
        == []
    )


def test_the_wizard_lists_who_a_risk_can_affect(reg):
    from app.db.models import (
        InterestedParty,
        InterestedPartyName,
        InterestedPartyNature,
        IsmsPerson,
        IsmsVendor,
    )

    db = reg.db
    name = InterestedPartyName(name="Customers")
    other = InterestedPartyName(name="Staff")
    nature = InterestedPartyNature(name="External")
    internal = InterestedPartyNature(name="Internal")
    db.add_all([name, other, nature, internal])
    db.flush()
    db.add_all(
        [
            InterestedParty(name_id=name.id, nature_id=nature.id),
            InterestedParty(name_id=other.id, nature_id=internal.id),
            IsmsPerson(name="Dana", position="DPO", email="dana@example.test"),
            IsmsVendor(name="Cloud host", contact="support@cloud.example"),
        ]
    )
    db.commit()
    options = reg.manage("GET", "/v1/risks/parties", params={"framework": "A"})
    # Interested parties are organisation-wide, so every one is offered.
    assert [(p["label"], p["detail"]) for p in options["interested_parties"]] == [
        ("Customers", "External"),
        ("Staff", "Internal"),
    ]
    # Names only: no addresses or contacts leave the directory through here.
    assert options["people"] == [
        {
            "id": options["people"][0]["id"],
            "kind": "person",
            "label": "Dana",
            "detail": "DPO",
        }
    ]
    assert options["vendors"][0]["label"] == "Cloud host"
    assert "contact" not in options["vendors"][0]
    assert options["natures"] == ["External", "Internal"]
    narrowed = reg.manage(
        "GET", "/v1/risks/parties", params={"framework": "A", "q": "dan"}
    )
    assert (
        narrowed["interested_parties"],
        [p["label"] for p in narrowed["people"]],
    ) == (
        [],
        ["Dana"],
    )
    # Reading risks is not enough to browse the directory.
    reg.read("GET", "/v1/risks/parties", params={"framework": "A"}, status=403)


# --- Bounds the schema does not already enforce ------------------------------------


def test_party_searches_need_a_bounded_query_and_a_framework(reg, monkeypatch):
    """A search term over 256 characters is refused; an empty framework is refused."""
    too_long = reg.manage(
        "GET",
        "/v1/risks/parties",
        params={"framework": "A", "q": "x" * 257},
        status=400,
    )
    assert too_long["detail"] == "q is too long"

    from app.core.config import settings

    monkeypatch.setattr(settings, "default_framework_slug", "")
    blank = reg.manage("GET", "/v1/risks/parties", params={"framework": ""}, status=400)
    assert blank["detail"] == "framework is required"


# --- Licences and ownership on assets -------------------------------------------------


def test_assets_take_an_existing_licence_by_id_and_drop_a_blank_licence_name(reg):
    licence = IsmsLicense(name="Site licence", description="")
    reg.db.add(licence)
    reg.db.commit()
    create = "/v1/risks/assets"

    licensed = reg.manage(
        "POST",
        create,
        {
            "name": "Mail gateway",
            "category_name": "Software",
            "subcategory_name": "SaaS",
            "license_id": str(licence.id),
        },
    )
    assert licensed["license_entity"] == {"id": str(licence.id), "name": "Site licence"}
    assert reg.db.query(IsmsLicense).count() == 1

    cleared = reg.manage(
        "PATCH",
        f"{create}/{licensed['id']}",
        {"name": "Mail gateway", "license_name": "   "},
    )
    assert (cleared["license"], cleared["license_id"]) == ("", None)
    assert reg.db.query(IsmsLicense).count() == 1


def test_asset_edits_move_ownership_between_organisational_nodes(reg):
    team = _org_node(reg.db, "Finance", node_type="team")
    other = _org_node(reg.db, "Operations", node_type="team")
    create = "/v1/risks/assets"
    asset = reg.manage(
        "POST",
        create,
        {
            "name": "Ledger",
            "category_name": "Software",
            "subcategory_name": "SaaS",
            "owner_org_node_id": str(team.id),
        },
    )
    assert asset["owner_org_node"]["name"] == "Finance"

    moved = reg.manage(
        "PATCH",
        f"{create}/{asset['id']}",
        {
            "name": "Ledger",
            "owner_org_node_id": str(other.id),
            "register_held_by_org_node_id": str(team.id),
        },
    )
    assert moved["owner_org_node"] == {
        "id": str(other.id),
        "name": "Operations",
        "node_type": "team",
    }
    assert moved["register_held_by_org_node"]["name"] == "Finance"
    unknown = reg.manage(
        "PATCH",
        f"{create}/{asset['id']}",
        {"name": "Ledger", "owner_org_node_id": str(uuid.uuid4())},
        status=400,
    )
    assert "org" in unknown["detail"].lower()
    assert reg.db.get(RiskAsset, uuid.UUID(asset["id"])).owner_org_node_id == other.id


# --- Parties whose records are gone ---------------------------------------------------


def test_affected_parties_whose_records_are_gone_are_left_out(reg):
    """A link to a deleted vendor or interested party is omitted from the risk."""
    from app.db.models import (
        InterestedParty,
        InterestedPartyName,
        InterestedPartyNature,
        IsmsVendor,
        RiskInterestedParty,
    )

    db = reg.db
    name = InterestedPartyName(name="Customers")
    nature = InterestedPartyNature(name="External")
    db.add_all([name, nature])
    db.flush()
    party = InterestedParty(name_id=name.id, nature_id=nature.id)
    vendor = IsmsVendor(name="Cloud host", website="https://cloud.example")
    db.add_all([party, vendor])
    db.commit()
    created = _create(
        reg,
        interested_parties=[
            {"interested_party_id": str(party.id)},
            {"vendor_id": str(vendor.id)},
        ],
    )
    assert [p["kind"] for p in created["interested_parties"]] == [
        "interested_party",
        "vendor",
    ]

    db.delete(db.get(IsmsVendor, vendor.id))
    db.delete(db.get(InterestedParty, party.id))
    db.commit()
    db.expire_all()

    assert reg.read("GET", f"/v1/risks/{created['id']}")["interested_parties"] == []
    # Whatever the database did with the link rows, the risk itself remains.
    assert db.query(RiskInterestedParty).filter_by(
        risk_id=uuid.UUID(created["id"])
    ).count() in (0, 2)
    assert db.get(Risk, uuid.UUID(created["id"])) is not None


# --- Library templates for the Keen framework ---------------------------------------


def test_a_keen_af_risk_from_a_template_links_the_template_controls(reg):
    """Without explicit controls, a KEEN-AF risk takes its template's control links."""
    from app.db.models import RiskLibraryEntry

    db = reg.db
    db.add(Framework(slug="KEEN-AF:1.0"))
    db.commit()
    _control(db, "NET-1", framework="KEEN-AF:1.0", kind="custom")
    template = RiskLibraryEntry(
        name="Network exposure",
        threat_summary="Example risk",
        suggested_assessment={"keen_af_control_refs": ["NET-1", "REMOVED"]},
    )
    db.add(template)
    db.commit()

    created = _create(
        reg, framework="KEEN-AF:1.0", library_template_id=str(template.id)
    )

    assert created["control_count"] == 1
    linked = reg.read(
        "GET",
        f"/v1/risks/{created['id']}/controls",
        params={"framework": "KEEN-AF:1.0"},
    )
    assert [c["ref"] for c in linked["items"]] == ["NET-1"]


def test_a_risk_without_a_title_or_threat_is_known_by_its_asset(reg):
    """The display name falls back to the asset when no threat is recorded."""
    created = _create(reg, threat_summary=None)

    assert created["display"] == "Payroll database"
    titled = reg.manage(
        "PATCH", f"/v1/risks/{created['id']}", {"framework": "A", "title": "Leak"}
    )
    assert titled["display"] == "Leak"
