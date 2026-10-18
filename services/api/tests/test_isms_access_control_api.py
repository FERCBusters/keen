"""The ISMS access control matrix through the assembled application.

Each matrix row records a task on a service asset, the hosting accounts it
covers, the organisation chart roles it applies to and its approval. Rows
need a service and at least one hosting account. Readers with isms.read
browse the matrix and isms.manage covers every change.
"""

import uuid
from types import SimpleNamespace

import pytest
from app.db.models import IsmsOrgNode, User

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

    def account(name, account_id=""):
        return manage("POST", "aws-accounts", {"name": name, "account_id": account_id})

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
    production = account("Production", "111111111111")
    staging = account("Staging", "222222222222")
    approver = keen.user("isms.read")

    def entry(**body):
        return manage(
            "POST",
            "access-control-matrix",
            {
                "task_action": "Deploy releases",
                "service_asset_id": service["id"],
                "aws_account_ids": [production["id"]],
                **body,
            },
        )

    return SimpleNamespace(
        keen=keen,
        manager=manager,
        reader=reader,
        call=call,
        manage=manage,
        account=account,
        role=role,
        service=service,
        production=production,
        staging=staging,
        approver=approver,
        entry=entry,
    )


def names(items):
    return [item["name"] for item in items]


# --- Hosting accounts --------------------------------------------------------------


def test_hosting_account_ids_are_unique(acm):
    blank = acm.account("  Sandbox  ")
    duplicate = acm.manage(
        "POST",
        "aws-accounts",
        {"name": "Copy", "account_id": "111111111111"},
        400,
    )
    clash = acm.manage(
        "PATCH",
        f"aws-accounts/{acm.staging['id']}",
        {"account_id": "111111111111"},
        400,
    )
    unnamed = acm.manage("POST", "aws-accounts", {"name": "  "}, 400)

    assert (blank["name"], blank["account_id"], blank["display"]) == (
        "Sandbox",
        "",
        "Sandbox",
    )
    assert duplicate["detail"] == clash["detail"] == "Hosting account id already exists"
    assert unnamed["detail"] == "hosting account name is required"
    # Accounts without an id do not clash, and an account keeps its own id.
    acm.account("Second sandbox")
    kept = acm.manage(
        "PATCH",
        f"aws-accounts/{acm.staging['id']}",
        {"account_id": "222222222222", "notes": "Pre-production"},
    )
    assert (kept["account_id"], kept["notes"]) == ("222222222222", "Pre-production")
    renamed = acm.manage(
        "PATCH", f"aws-accounts/{acm.staging['id']}", {"name": "Pre-production"}
    )
    assert (renamed["name"], renamed["account_id"], renamed["notes"]) == (
        "Pre-production",
        "222222222222",
        "Pre-production",
    )
    listed = acm.call(acm.reader, "GET", "aws-accounts")["items"]
    assert [(a["name"], a["account_id"]) for a in listed] == [
        ("Pre-production", "222222222222"),
        ("Production", "111111111111"),
        ("Sandbox", ""),
        ("Second sandbox", ""),
    ]


def test_deleting_a_hosting_account_removes_it_from_matrix_rows(acm):
    row = acm.entry(aws_account_ids=[acm.production["id"], acm.staging["id"]])
    counts = {
        a["name"]: a["entry_count"]
        for a in acm.call(acm.reader, "GET", "aws-accounts")["items"]
    }
    assert counts == {"Production": 1, "Staging": 1}

    assert acm.manage("DELETE", f"aws-accounts/{acm.staging['id']}") == {"ok": True}

    kept = acm.call(acm.reader, "GET", f"access-control-matrix/{row['id']}")
    assert kept["aws_account_ids"] == [acm.production["id"]]
    missing = acm.manage("DELETE", f"aws-accounts/{acm.staging['id']}", status=404)
    assert missing["detail"] == "hosting account not found"


# --- Matrix rows ------------------------------------------------------------------------


def test_a_matrix_row_records_its_service_accounts_roles_and_approval(acm):
    operator, auditor = acm.role("Operator"), acm.role("Auditor")

    row = acm.entry(
        task_action="  Deploy releases  ",
        aws_account_ids=[acm.staging["id"], acm.production["id"], acm.staging["id"]],
        role_org_node_ids=[operator, auditor, operator],
        approved_by_user_id=str(acm.approver.id),
        status="Approved",
        notes="  Reviewed quarterly  ",
    )

    assert row["task_action"] == "Deploy releases"
    assert (row["service_asset_id"], row["service"]["asset"]) == (
        acm.service["id"],
        "Payroll",
    )
    # Accounts and roles are listed once each, by name.
    assert row["aws_account_ids"] == [acm.production["id"], acm.staging["id"]]
    assert [r["name"] for r in row["roles"]] == ["Auditor", "Operator"]
    assert (row["status"], row["approved_by"]["id"], row["notes"]) == (
        "Approved",
        str(acm.approver.id),
        "Reviewed quarterly",
    )
    assert row["display"] == "Deploy releases → Payroll"
    assert acm.call(acm.reader, "GET", f"access-control-matrix/{row['id']}") == row


def test_a_row_waits_for_approval_unless_a_known_status_is_given(acm):
    pending = acm.entry(status=None)
    approved = acm.entry(status=" Approved ")
    reset = acm.manage(
        "PATCH", f"access-control-matrix/{approved['id']}", {"status": None}
    )

    assert (pending["status"], approved["status"], reset["status"]) == (
        "Pending Approval",
        "Approved",
        "Pending Approval",
    )
    blank = acm.manage(
        "PATCH", f"access-control-matrix/{approved['id']}", {"status": "  "}, 400
    )
    assert blank["detail"] == "Invalid access control status"


INVALID = [
    ("no-task", {"task_action": "  "}, "task/action is required"),
    ("no-service", {"service_asset_id": None}, "service_asset_id is required"),
    (
        "no-accounts",
        {"aws_account_ids": []},
        "At least one hosting account is required",
    ),
    (
        "unknown-service",
        {"service_asset_id": str(uuid.uuid4())},
        "Unknown service asset",
    ),
    (
        "unknown-approver",
        {"approved_by_user_id": str(uuid.uuid4())},
        "Unknown approved-by user",
    ),
    (
        "unknown-role",
        {"role_org_node_ids": [str(uuid.uuid4())]},
        "Unknown organisation chart role",
    ),
    ("unknown-status", {"status": "Rejected"}, "Invalid access control status"),
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
            "aws_account_ids": [acm.production["id"]],
            **change,
        },
        400,
    )
    updated = acm.manage(
        "PATCH", f"access-control-matrix/{original['id']}", change, 400
    )

    assert (created["detail"], updated["detail"]) == (detail, detail)
    assert acm.call(acm.reader, "GET", "access-control-matrix")["items"] == [original]


def test_an_unknown_or_inactive_account_holder_is_refused(acm):
    original = acm.entry()
    unknown = str(uuid.uuid4())
    inactive = User(username="departed", password_hash="unused", is_active=False)
    acm.keen.db.add(inactive)
    acm.keen.db.commit()

    account = acm.manage(
        "PATCH",
        f"access-control-matrix/{original['id']}",
        {"aws_account_ids": [unknown]},
        400,
    )
    approver = acm.manage(
        "PATCH",
        f"access-control-matrix/{original['id']}",
        {"approved_by_user_id": str(inactive.id)},
        400,
    )

    assert account["detail"] == f"Unknown hosting account: {unknown}"
    assert approver["detail"] == "Unknown approved-by user"
    assert acm.call(acm.reader, "GET", f"access-control-matrix/{original['id']}") == (
        original
    )


def test_an_update_changes_only_the_fields_it_names(acm):
    operator = acm.role("Operator")
    original = acm.entry(role_org_node_ids=[operator], notes="Reviewed")
    path = f"access-control-matrix/{original['id']}"

    approved = acm.manage(
        "PATCH",
        path,
        {"status": "Approved", "approved_by_user_id": str(acm.approver.id)},
    )
    moved = acm.manage(
        "PATCH",
        path,
        {"aws_account_ids": [acm.staging["id"]], "role_org_node_ids": []},
    )

    assert (approved["status"], approved["approved_by_user_id"]) == (
        "Approved",
        str(acm.approver.id),
    )
    assert (approved["notes"], approved["aws_account_ids"]) == (
        "Reviewed",
        [acm.production["id"]],
    )
    assert [r["id"] for r in approved["roles"]] == [operator]
    assert (moved["aws_account_ids"], moved["roles"]) == ([acm.staging["id"]], [])
    assert (moved["status"], moved["notes"], moved["task_action"]) == (
        "Approved",
        "Reviewed",
        "Deploy releases",
    )


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
        "aws-accounts",
        "access-control-matrix",
        f"access-control-matrix/{row['id']}",
    ):
        assert acm.reader.get("/v1/isms/" + path).status_code == 200
        assert outsider.get("/v1/isms/" + path).status_code == 403

    changes = [
        ("POST", "aws-accounts", {"name": "Shadow"}),
        ("PATCH", f"aws-accounts/{acm.production['id']}", {"name": "Renamed"}),
        ("DELETE", f"aws-accounts/{acm.production['id']}", None),
        (
            "POST",
            "access-control-matrix",
            {
                "task_action": "Escalate",
                "service_asset_id": acm.service["id"],
                "aws_account_ids": [acm.production["id"]],
            },
        ),
        ("PATCH", f"access-control-matrix/{row['id']}", {"status": "Approved"}),
        ("DELETE", f"access-control-matrix/{row['id']}", None),
    ]
    for method, path, body in changes:
        refused = acm.call(acm.reader, method, path, 403, body)
        assert refused["detail"] == MANAGE, path

    assert acm.call(acm.reader, "GET", "access-control-matrix")["items"] == [row]
    assert names(acm.call(acm.reader, "GET", "aws-accounts")["items"]) == [
        "Production",
        "Staging",
    ]
