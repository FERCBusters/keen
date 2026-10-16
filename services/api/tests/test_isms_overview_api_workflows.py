"""ISMS overview and personal views through the assembled application.

Records are created through the ISMS API by a manager, then read back through
the overview (meta and summary) and the signed-in user's personal views.
"""

from types import SimpleNamespace

import pytest
from app.db.models import (
    ControlItem,
    Framework,
    FrameworkClause,
    ManagedConfiguration,
)

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

HOSTILE = '<img src=x onerror="alert(1)">'


def _send(client, method, path, body=None, status=200, **params):
    response = client.request(
        method,
        path,
        params={"framework": "A", **params},
        headers=unsafe(client) if method != "GET" else {},
        **({"json": body} if body is not None else {}),
    )
    assert response.status_code == status, response.text
    return response.json() if response.content else None


@pytest.fixture
def isms(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add_all([Framework(slug="A"), Framework(slug="B")])
    controls = [
        ControlItem(
            framework_slug="A", ref=ref, type="annex_control", title=f"{ref} title"
        )
        for ref in ("A.5.10", "A.5.2")
    ]
    clauses = [
        FrameworkClause(
            framework_slug="A", ref=ref, title=f"Clause {ref}", sort_order=order
        )
        for ref, order in (("4.10", 1), ("4.2", 1), ("10", 9))
    ]
    db.add_all([*controls, *clauses])
    db.commit()
    manager = keen.user("isms.manage")
    member = keen.user()
    retired = keen.user()
    retired.is_active = False
    db.commit()
    manager_client = keen.client(manager)

    def manage(method, path, body=None, status=200, **params):
        return _send(manager_client, method, path, body, status, **params)

    role = manage(
        "POST",
        "/v1/isms/org-nodes",
        {"name": "Security officer", "node_type": "role", "user_ids": [str(member.id)]},
    )
    analyst = manage(
        "POST",
        "/v1/isms/org-nodes",
        {
            "name": HOSTILE,
            "node_type": "team",
            "parent_id": role["id"],
            "user_ids": [str(member.id)],
            "relationship_type": "deputy",
        },
    )
    objective = manage(
        "POST",
        "/v1/isms/objectives",
        {
            "requirement": "Protect records",
            "goal": "All records protected",
            "owner_user_id": str(member.id),
            "resource_user_ids": [str(member.id), str(manager.id)],
            "completion_target_date": "2026-12-31",
            "controls": [str(controls[0].id)],
            "clauses": [str(clauses[0].id)],
        },
    )
    other_objective = manage(
        "POST", "/v1/isms/objectives", {"requirement": "Train staff", "goal": "Annual"}
    )
    document = manage("POST", "/v1/isms/documents", {"title": "Security policy"})
    process = manage(
        "POST",
        "/v1/isms/business-processes",
        {"name": f"  {HOSTILE}  ", "description": "Monthly", "sort_order": 2},
    )
    assert process["name"] == HOSTILE
    manage("POST", "/v1/isms/business-processes", {"name": "  "}, status=400)
    listed = manage("GET", "/v1/isms/business-processes")["items"]
    assert [item["name"] for item in listed] == [HOSTILE]
    licence = manage("POST", "/v1/isms/licenses", {"name": "Enterprise seat"})
    asset = manage(
        "POST",
        "/v1/isms/assets",
        {
            "asset": "Payroll database",
            "category_name": "Data",
            "subcategory_name": "Databases",
            "license_id": licence["id"],
            "owner_org_node_id": role["id"],
            "register_held_by_org_node_id": analyst["id"],
        },
    )
    unowned_asset = manage(
        "POST",
        "/v1/isms/assets",
        {"asset": "Kiosk", "category_name": "Hardware", "subcategory_name": "Devices"},
    )
    account = manage(
        "POST",
        "/v1/isms/aws-accounts",
        {"name": "Production", "account_id": "123456789012"},
    )
    entry = manage(
        "POST",
        "/v1/isms/access-control-matrix",
        {
            "task_action": "Deploy",
            "service_asset_id": asset["id"],
            "aws_account_ids": [account["id"]],
            "role_org_node_ids": [role["id"]],
        },
    )
    measure = manage(
        "POST",
        "/v1/isms/effectiveness-measures",
        {
            "summary": "Agent coverage",
            "metric": "Coverage",
            "effectiveness_measure": "Count",
        },
    )
    manage(
        "POST",
        f"/v1/isms/effectiveness-measures/{measure['id']}/metrics",
        {"metric_value": 96, "source_type": "other"},
    )
    manage(
        "POST",
        "/v1/isms/effectiveness-measures",
        {
            "summary": "Other framework",
            "metric": "Count",
            "effectiveness_measure": "Count",
        },
        framework="B",
    )
    meeting = manage(
        "POST",
        "/v1/isms/meetings",
        {
            "title": "Management review",
            "date": "2026-02-01",
            "attendee_user_ids": [str(member.id)],
            "apology_user_ids": [str(member.id)],
        },
    )
    manage(
        "POST",
        "/v1/isms/meetings",
        {
            "title": "Unrelated",
            "date": "2026-03-01",
            "attendee_user_ids": [str(manager.id)],
        },
    )
    return SimpleNamespace(
        keen=keen,
        db=db,
        manager=manager,
        member=member,
        retired=retired,
        manage=manage,
        role=role,
        analyst=analyst,
        objective=objective,
        other_objective=other_objective,
        document=document,
        asset=asset,
        unowned_asset=unowned_asset,
        entry=entry,
        meeting=meeting,
    )


def test_overview_requires_isms_read_access(isms):
    app = isms.keen
    anonymous = app.client()
    nobody = app.client(app.user())
    for path in ("/v1/isms/meta", "/v1/isms/summary"):
        assert anonymous.get(path).status_code == 401
        _send(nobody, "GET", path, status=403)
    for code, can_manage in (
        ("isms.read", False),
        ("risk.read", False),
        ("isms.manage", True),
        ("risk.manage", True),
    ):
        client = app.client(app.user(code))
        meta = _send(client, "GET", "/v1/isms/meta")
        assert (meta["can_view"], meta["can_manage"]) == (True, can_manage), code
        assert _send(client, "GET", "/v1/isms/summary")["can_manage"] is can_manage


def test_meta_lists_the_choices_for_each_section(isms):
    reader = isms.keen.client(isms.keen.user("isms.read"))
    meta = _send(reader, "GET", "/v1/isms/meta")
    assert meta["framework"] == "A" and meta["section"] == "all"
    usernames = [user["username"] for user in meta["users"]]
    assert isms.member.username in usernames and isms.retired.username not in usernames
    assert usernames == sorted(usernames)
    assert [c["ref"] for c in meta["controls"]] == ["A.5.2", "A.5.10"]
    assert [c["ref"] for c in meta["clauses"]] == ["4.2", "4.10", "10"]
    assert {node["name"] for node in meta["org_nodes"]} == {"Security officer", HOSTILE}
    assert [doc["title"] for doc in meta["documents"]] == ["Security policy"]
    assert {a["name"] for a in meta["assets"]} == {"Payroll database", "Kiosk"}
    assert [lic["name"] for lic in meta["licenses"]] == ["Enterprise seat"]
    assert [acc["name"] for acc in meta["aws_accounts"]] == ["Production"]
    assert {c["name"] for c in meta["asset_categories"]} >= {"Data", "Hardware"}
    assert isinstance(meta["effectiveness_metric_sources"], list)
    assert meta["effectiveness_metric_source_types"] == [
        source["id"] for source in meta["effectiveness_metric_sources"]
    ]
    assert meta["application_values"] == ["Low", "Medium", "High"]

    documents = _send(reader, "GET", "/v1/isms/meta", section="Policies")
    assert documents["section"] == "documents"
    assert {"controls", "clauses"} <= set(documents)
    assert not {"users", "org_nodes", "assets", "licenses", "aws_accounts"} & set(
        documents
    )
    access = _send(reader, "GET", "/v1/isms/meta", section="access-control")
    assert access["section"] == "access"
    assert {"users", "org_nodes", "assets", "aws_accounts"} <= set(access)
    assert "controls" not in access
    overview = _send(reader, "GET", "/v1/isms/meta", section="summary")
    assert overview["section"] == "overview" and "users" not in overview
    response = _send(reader, "GET", "/v1/isms/meta", section="payroll", status=400)
    assert response["detail"] == "Unknown ISMS section"
    _send(reader, "GET", "/v1/isms/meta", framework="A B", status=400)


def test_summary_counts_and_lists_each_register(isms):
    reader = isms.keen.client(isms.keen.user("isms.read"))
    summary = _send(reader, "GET", "/v1/isms/summary")
    assert summary["counts"] == {
        "objectives": 2,
        "documents": 1,
        "org_nodes": 2,
        "assets": 2,
        "licenses": 1,
        "aws_accounts": 1,
        "access_control_matrix": 1,
        "effectiveness_measures": 1,  # framework A only
        "effectiveness_metric_entries": 1,
        "meetings": 2,
        "business_processes": 1,
    }
    assert {o["id"] for o in summary["objectives"]} == {
        isms.objective["id"],
        isms.other_objective["id"],
    }
    assert [d["id"] for d in summary["documents"]] == [isms.document["id"]]
    assert [a["name"] for a in summary["assets"]] == ["Kiosk", "Payroll database"]
    assert [e["id"] for e in summary["access_control_matrix"]] == [isms.entry["id"]]
    assert [m["summary"] for m in summary["effectiveness_measures"]] == [
        "Agent coverage"
    ]
    assert [m["title"] for m in summary["meetings"]] == [
        "Unrelated",
        "Management review",
    ]
    assert {n["name"] for n in summary["org_nodes"]} == {"Security officer", HOSTILE}

    objectives = _send(reader, "GET", "/v1/isms/summary", section="objectives")
    assert objectives["section"] == "objectives"
    assert "objectives" in objectives and "documents" not in objectives
    assert objectives["counts"]["documents"] == 1
    other = _send(reader, "GET", "/v1/isms/summary", framework="B")
    assert other["counts"]["effectiveness_measures"] == 1
    assert other["counts"]["effectiveness_metric_entries"] == 0


def test_disabled_isms_features_are_hidden_from_the_overview(isms):
    isms.db.add(
        ManagedConfiguration(
            name="features", version=1, document={"meetings": False, "assets": False}
        )
    )
    isms.db.commit()
    reader = isms.keen.client(isms.keen.user("isms.read"))
    summary = _send(reader, "GET", "/v1/isms/summary")
    assert not {"meetings", "assets", "licenses"} & set(summary["counts"])
    assert "meetings" not in summary and "assets" not in summary
    assert summary["counts"]["objectives"] == 2
    meta = _send(reader, "GET", "/v1/isms/meta")
    assert not {"assets", "asset_categories", "licenses"} & set(meta)
    for path, section in (
        ("/v1/isms/meta", "meetings"),
        ("/v1/isms/summary", "assets"),
    ):
        response = _send(reader, "GET", path, section=section, status=404)
        assert response["detail"] == "This feature is disabled by an administrator."
    member = isms.keen.client(isms.member)
    _send(member, "GET", "/v1/me/isms-meetings", status=404)
    _send(member, "GET", "/v1/me/isms-assets", status=404)
    _send(member, "GET", "/v1/me/isms-objectives")


def test_personal_views_show_only_the_signed_in_users_records(isms):
    app = isms.keen
    member = app.client(isms.member)
    assert app.client().get("/v1/me/isms-objectives").status_code == 401

    objectives = _send(member, "GET", "/v1/me/isms-objectives")
    # Owner and resource at once still lists the objective once.
    assert [o["id"] for o in objectives["items"]] == [isms.objective["id"]]
    assert objectives["framework"] == "A"

    roles = _send(member, "GET", "/v1/me/isms-role")["items"]
    by_name = {item["org_node"]["name"]: item for item in roles}
    assert by_name["Security officer"]["relationship_type"] == "member"
    assert by_name[HOSTILE]["relationship_type"] == "deputy"
    assert [step["name"] for step in by_name[HOSTILE]["path"]] == [
        "Security officer",
        HOSTILE,
    ]

    meetings = _send(member, "GET", "/v1/me/isms-meetings")["items"]
    assert [m["title"] for m in meetings] == ["Management review"]
    assert sorted(meetings[0]["my_attendance_types"]) == ["apology", "attendee"]
    assert set(meetings[0]["my_attendance_label"].split(", ")) == {
        "Attendee",
        "Apology",
    }

    assets = _send(member, "GET", "/v1/me/isms-assets")["items"]
    assert [(a["name"], a["is_owner"], a["is_register_holder"]) for a in assets] == [
        ("Payroll database", True, True)
    ]

    stranger = app.client(app.user())
    for path in (
        "/v1/me/isms-objectives",
        "/v1/me/isms-role",
        "/v1/me/isms-meetings",
        "/v1/me/isms-assets",
    ):
        assert _send(stranger, "GET", path)["items"] == [], path
    _send(member, "GET", "/v1/me/isms-objectives", framework="A B", status=400)
