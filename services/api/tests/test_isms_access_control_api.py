"""The ISMS access control matrix through the assembled application.

Each matrix row records a task on a service asset and the organisation chart
roles it applies to. Rows need a service; cloud accounts and projects are
assets like any other service. Only isms.manage changes the matrix, so a saved
row is the access its editor approves. Readers with isms.read browse the
matrix and isms.manage covers every change.
"""

import uuid
from types import SimpleNamespace

import pytest
from app.db.models import IsmsOrgNode

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

MANAGE = "isms.manage permission required"


@pytest.fixture
def acm(keen):  # noqa: F811 - fixture imported above
    manager = keen.client(keen.user("isms.manage"))
    reader = keen.client(keen.user("isms.read"))

    def call(client, method, path, status=200, body=None):
        kwargs = {"json": body} if body is not None else {}
        if method != "GET":
            kwargs["headers"] = unsafe(client)
        response = client.request(method, "/v1/isms/" + path, **kwargs)
        assert response.status_code == status, response.text
        return response.json()

    def manage(method, path, body=None, status=200):
        return call(manager, method, path, status, body)

    def role(name):
        row = IsmsOrgNode(name=name, node_type="role")
        keen.db.add(row)
        keen.db.commit()
        return str(row.id)

    service = manage(
        "POST",
        "assets",
        {"asset": "Payroll", "category_name": "Software", "subcategory_name": "Apps"},
    )

    def entry(**body):
        return manage(
            "POST",
            "access-control-matrix",
            {
                "task_action": "Deploy releases",
                "service_asset_id": service["id"],
                **body,
            },
        )

    return SimpleNamespace(
        keen=keen,
        manager=manager,
        reader=reader,
        call=call,
        manage=manage,
        role=role,
        service=service,
        entry=entry,
    )


# --- Matrix rows ------------------------------------------------------------------------


def test_a_matrix_row_records_its_service_and_roles(acm):
    operator, auditor = acm.role("Operator"), acm.role("Auditor")

    # Whoever saves a row stands behind its state; no approver, status or
    # hosting account is recorded, and a client that still sends one changes
    # nothing.
    row = acm.entry(
        task_action="  Deploy releases  ",
        aws_account_ids=[str(uuid.uuid4())],
        role_org_node_ids=[operator, auditor, operator],
        approved_by_user_id=str(uuid.uuid4()),
        status="Approved",
        notes="  Reviewed quarterly  ",
    )

    assert row["task_action"] == "Deploy releases"
    assert (row["service_asset_id"], row["service"]["asset"]) == (
        acm.service["id"],
        "Payroll",
    )
    # Roles are listed once each, by name.
    assert [r["name"] for r in row["roles"]] == ["Auditor", "Operator"]
    assert row["notes"] == "Reviewed quarterly"
    retired = {"approved_by", "approved_by_user_id", "status", "aws_account_ids"}
    assert not retired & set(row)
    assert row["display"] == "Deploy releases × Payroll"
    assert acm.call(acm.reader, "GET", f"access-control-matrix/{row['id']}") == row


INVALID = [
    ("no-task", {"task_action": "  "}, "task/action is required"),
    ("no-service", {"service_asset_id": None}, "service_asset_id is required"),
    (
        "unknown-service",
        {"service_asset_id": str(uuid.uuid4())},
        "Unknown service asset",
    ),
    (
        "unknown-role",
        {"role_org_node_ids": [str(uuid.uuid4())]},
        "Unknown organisation chart role",
    ),
]


@pytest.mark.parametrize(
    "change,detail",
    [(change, detail) for _, change, detail in INVALID],
    ids=[name for name, _, _ in INVALID],
)
def test_invalid_rows_are_refused_on_create_and_update(acm, change, detail):
    original = acm.entry()

    created = acm.manage(
        "POST",
        "access-control-matrix",
        {
            "task_action": "Read logs",
            "service_asset_id": acm.service["id"],
            **change,
        },
        400,
    )
    updated = acm.manage(
        "PATCH", f"access-control-matrix/{original['id']}", change, 400
    )

    assert (created["detail"], updated["detail"]) == (detail, detail)
    assert acm.call(acm.reader, "GET", "access-control-matrix")["items"] == [original]


def test_an_update_changes_only_the_fields_it_names(acm):
    operator = acm.role("Operator")
    original = acm.entry(role_org_node_ids=[operator], notes="Reviewed")
    path = f"access-control-matrix/{original['id']}"

    renamed = acm.manage("PATCH", path, {"task_action": "Deploy hotfixes"})
    moved = acm.manage("PATCH", path, {"role_org_node_ids": []})

    assert (renamed["task_action"], renamed["notes"]) == ("Deploy hotfixes", "Reviewed")
    assert [r["id"] for r in renamed["roles"]] == [operator]
    assert moved["roles"] == []
    assert (moved["notes"], moved["task_action"]) == ("Reviewed", "Deploy hotfixes")


def test_the_matrix_lists_the_most_recently_changed_row_first(acm):
    first = acm.entry(task_action="Deploy releases")
    second = acm.entry(task_action="Read logs")
    acm.manage("PATCH", f"access-control-matrix/{first['id']}", {"notes": "Changed"})

    listed = acm.call(acm.reader, "GET", "access-control-matrix")["items"]

    assert [row["id"] for row in listed] == [first["id"], second["id"]]
    assert acm.manage("DELETE", f"access-control-matrix/{first['id']}") == {"ok": True}
    assert [
        r["id"] for r in acm.call(acm.reader, "GET", "access-control-matrix")["items"]
    ] == [second["id"]]
    missing = acm.call(acm.reader, "GET", f"access-control-matrix/{first['id']}", 404)
    assert missing["detail"] == "Access control matrix row not found"


def test_reading_the_matrix_needs_isms_read_and_changes_need_isms_manage(acm):
    row = acm.entry()
    outsider = acm.keen.client(acm.keen.user("events.read"))
    for path in (
        "access-control-matrix",
        f"access-control-matrix/{row['id']}",
    ):
        assert acm.reader.get("/v1/isms/" + path).status_code == 200
        answer = outsider.get("/v1/isms/" + path)
        assert (answer.status_code, answer.json()) == (
            403,
            {"detail": "isms.read permission required"},
        )

    changes = [
        (
            "POST",
            "access-control-matrix",
            {
                "task_action": "Escalate",
                "service_asset_id": acm.service["id"],
            },
        ),
        ("PATCH", f"access-control-matrix/{row['id']}", {"notes": "Reviewed"}),
        ("DELETE", f"access-control-matrix/{row['id']}", None),
    ]
    for method, path, body in changes:
        refused = acm.call(acm.reader, method, path, 403, body)
        assert refused["detail"] == MANAGE, path

    assert acm.call(acm.reader, "GET", "access-control-matrix")["items"] == [row]
    # The hosting-account routes are gone with the records they kept.
    answer = acm.manager.get("/v1/isms/aws-accounts")
    assert (answer.status_code, answer.json()) == (404, {"detail": "Not Found"})
