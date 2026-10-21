"""PESTLE(E) register workflows through the assembled application.

Items, business processes, relevance links, visualisations and change history
are exercised with real sign-in, CSRF and permission resolution.
"""

import uuid
from types import SimpleNamespace

import pytest
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    EntityChangelog,
    Framework,
    FrameworkClause,
    PestleBusinessProcessRelevance,
    PestleItem,
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
        params=params,
        headers=unsafe(client) if method != "GET" else {},
        **({"json": body} if body is not None else {}),
    )
    assert response.status_code == status, response.text
    return response.json() if response.content else None


@pytest.fixture
def pestle(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add_all([Framework(slug="A"), Framework(slug="B")])
    context = FrameworkClause(
        framework_slug="A", ref="4.1", title="Context", sort_order=1
    )
    parties = FrameworkClause(
        framework_slug="A", ref="4.2", title="Interested parties", sort_order=1
    )
    improvement = FrameworkClause(
        framework_slug="A", ref="10", title="Improvement", sort_order=5
    )
    other = FrameworkClause(framework_slug="B", ref="1", title="Other framework")
    access = ControlItem(
        framework_slug="A", ref="A.2", type="annex_control", title="Access"
    )
    logging = ControlItem(
        framework_slug="A", ref="A.10", type="annex_control", title="Logging"
    )
    db.add_all([context, parties, improvement, other, access, logging])
    db.flush()
    db.add_all(
        [
            ControlClauseLink(
                control_item_id=logging.id,
                clause_id=context.id,
                applicability="applicable",
            ),
            ControlClauseLink(
                control_item_id=access.id,
                clause_id=context.id,
                applicability="applicable",
            ),
            ControlClauseLink(
                control_item_id=access.id,
                clause_id=parties.id,
                applicability="applicable",
            ),
        ]
    )
    db.commit()
    manager = keen.user("pestle.manage")
    manager_client = keen.client(manager)
    reader_client = keen.client(keen.user("pestle.read"))
    return SimpleNamespace(
        keen=keen,
        db=db,
        manager=manager,
        context=context,
        parties=parties,
        improvement=improvement,
        other=other,
        access=access,
        logging=logging,
        manage=lambda *a, **k: _send(manager_client, *a, **k),
        read=lambda *a, **k: _send(reader_client, *a, **k),
    )


def _item(pestle, **overrides):
    body = {
        "framework": "A",
        "type": "Legal",
        "lens": "External",
        "item": "New data protection regulation",
        "overall_relevance_code": "high",
        "rationale": "Fines for late breach notification",
    }
    body.update(overrides)
    return pestle.manage("POST", "/v1/pestle/items", body)


def test_pestle_routes_follow_the_read_and_manage_permissions(pestle):
    app = pestle.keen
    item = _item(pestle)
    process = pestle.manage(
        "POST", "/v1/pestle/business-processes", {"name": "Payroll"}
    )
    reads = (
        "/v1/pestle/meta",
        "/v1/pestle/items",
        f"/v1/pestle/items/{item['id']}",
        f"/v1/pestle/items/{item['id']}/business-processes",
        f"/v1/pestle/items/{item['id']}/clauses",
        f"/v1/pestle/items/{item['id']}/changelog",
        "/v1/pestle/business-processes",
        f"/v1/pestle/business-processes/{process['id']}/changelog",
        "/v1/pestle/visualisations",
        f"/v1/clauses/{pestle.context.id}/pestle",
    )
    anonymous = app.client()
    nobody = app.client(app.user())
    # Existing risk readers keep read access to the PESTLE(E) register.
    risk_reader = app.client(app.user("risk.read"))
    for path in reads:
        assert anonymous.get(path).status_code == 401, path
        _send(nobody, "GET", path, status=403, framework="A")
        pestle.read("GET", path, framework="A")
        _send(risk_reader, "GET", path, framework="A")

    writes = (
        (
            "POST",
            "/v1/pestle/items",
            {"type": "Legal", "lens": "External", "item": "X"},
        ),
        (
            "PATCH",
            f"/v1/pestle/items/{item['id']}",
            {"type": "Legal", "lens": "External", "item": "X"},
        ),
        ("DELETE", f"/v1/pestle/items/{item['id']}", None),
        ("POST", "/v1/pestle/business-processes", {"name": "Sales"}),
        ("PATCH", f"/v1/pestle/business-processes/{process['id']}", {"name": "Sales"}),
        ("DELETE", f"/v1/pestle/business-processes/{process['id']}", None),
        ("PATCH", f"/v1/pestle/items/{item['id']}/business-processes", {"items": []}),
        ("PATCH", f"/v1/pestle/items/{item['id']}/clauses", {"items": []}),
    )
    for method, path, body in writes:
        pestle.read(method, path, body, status=403)
        _send(risk_reader, method, path, body, status=403)
        _send(nobody, method, path, body, status=403)
    assert pestle.db.query(PestleItem).count() == 1

    # Risk managers can manage the register as well.
    risk_manager = app.client(app.user("risk.manage"))
    _send(
        risk_manager,
        "PATCH",
        f"/v1/pestle/business-processes/{process['id']}",
        {"name": "Payroll run"},
    )
    meta = pestle.read("GET", "/v1/pestle/meta")
    assert meta["permissions"] == {"can_view_pestle": True, "can_manage_pestle": False}
    assert [level["code"] for level in meta["relevance_levels"]] == [
        "na",
        "high",
        "medium",
        "low",
    ]
    assert meta["lenses"] == ["Internal", "External"] and "Ethical" in meta["types"]


def test_items_are_validated_and_normalised(pestle):
    created = _item(
        pestle,
        type="legal",
        lens="EXTERNAL",
        item=f"  {HOSTILE}  ",
        overall_relevance_code="1",
        rationale=f" {HOSTILE} ",
    )
    assert (created["type"], created["lens"]) == ("Legal", "External")
    assert created["item"] == HOSTILE and created["rationale"] == HOSTILE
    assert created["overall_relevance"]["code"] == "high"
    assert created["created_by"] == pestle.manager.username
    assert created["business_processes"] == [] and created["clauses"] == []

    defaulted = _item(pestle, overall_relevance_code=None, rationale=None)
    assert (
        defaulted["overall_relevance"]["code"] == "na" and defaulted["rationale"] == ""
    )
    medium = pestle.read("GET", "/v1/pestle/meta")["relevance_levels"][2]
    by_id = _item(
        pestle, overall_relevance_id=medium["id"], overall_relevance_code=None
    )
    assert by_id["overall_relevance"]["code"] == "medium"

    for overrides, status, detail in (
        ({"type": "Spiritual"}, 400, "type must be a valid PESTLE(E) type"),
        ({"lens": "Sideways"}, 400, "lens must be Internal or External"),
        ({"item": "   "}, 400, "item is required"),
        ({"overall_relevance_code": "critical"}, 400, "Unknown relevance_code"),
        ({"overall_relevance_id": str(uuid.uuid4())}, 400, "Unknown relevance_id"),
        ({"framework": "A B"}, 400, "Invalid framework"),
        ({"type": ""}, 422, None),
        ({"item": "x" * 20001}, 422, None),
    ):
        response = pestle.manage(
            "POST", "/v1/pestle/items", {**_base_item(), **overrides}, status=status
        )
        if detail:
            assert response["detail"] == detail
    assert pestle.db.query(PestleItem).count() == 3

    path = f"/v1/pestle/items/{created['id']}"
    updated = pestle.manage(
        "PATCH",
        path,
        {
            "type": "Ethical",
            "lens": "internal",
            "item": "Supplier ethics",
            "overall_relevance_code": "low",
        },
    )
    assert (updated["type"], updated["lens"], updated["item"]) == (
        "Ethical",
        "Internal",
        "Supplier ethics",
    )
    assert updated["overall_relevance"]["code"] == "low"
    assert updated["rationale"] == HOSTILE  # not sent, so unchanged
    pestle.manage("PATCH", path, {"item": "Missing type and lens"}, status=422)
    pestle.manage("PATCH", "/v1/pestle/items/nope", _base_item(), status=400)
    pestle.manage("PATCH", f"/v1/pestle/items/{uuid.uuid4()}", _base_item(), status=404)
    pestle.read("GET", f"/v1/pestle/items/{uuid.uuid4()}", status=404)

    changelog = pestle.read("GET", f"{path}/changelog")["items"]
    assert [entry["action"] for entry in changelog] == ["updated", "created"]
    assert pestle.manage("DELETE", path) == {"ok": True}
    pestle.read("GET", path, status=404)
    deleted = (
        pestle.db.query(EntityChangelog)
        .filter_by(entity_id=uuid.UUID(created["id"]), action="deleted")
        .one()
    )
    assert deleted.entity_type == "pestle_item"


def _base_item():
    return {"framework": "A", "type": "Legal", "lens": "External", "item": "Item"}


def test_items_are_filtered_searched_and_paged(pestle):
    legal = _item(pestle, item="Regulator fines", rationale="Late notification")
    social = _item(
        pestle,
        type="Social",
        lens="Internal",
        item="Hybrid working",
        overall_relevance_code="medium",
        rationale="",
    )
    tech = _item(
        pestle,
        type="Technological",
        item="AI adoption",
        overall_relevance_code="na",
        rationale="",
    )
    _item(pestle, framework="B", item="Other framework")

    def ids(**params):
        return [
            i["id"]
            for i in pestle.read("GET", "/v1/pestle/items", framework="A", **params)[
                "items"
            ]
        ]

    assert ids() == [
        legal["id"],
        social["id"],
        tech["id"],
    ]  # ordered by type, then lens
    assert ids(q="NOTIFICATION") == [legal["id"]]
    assert ids(q="internal") == [social["id"]]
    assert ids(type="social, technological") == [social["id"], tech["id"]]
    assert ids(lens="external") == [legal["id"], tech["id"]]
    assert ids(relevance_code="high,2") == [legal["id"], social["id"]]
    for params in (
        {"type": "Spiritual"},
        {"lens": "Up"},
        {"relevance_code": "critical"},
    ):
        pestle.read("GET", "/v1/pestle/items", status=400, framework="A", **params)
    page = pestle.read("GET", "/v1/pestle/items", framework="A", limit=1, offset=1)
    assert [i["id"] for i in page["items"]] == [social["id"]]
    assert (page["total"], page["limit"], page["offset"]) == (3, 1, 1)
    clamped = pestle.read("GET", "/v1/pestle/items", framework="A", limit=0, offset=-2)
    assert (clamped["limit"], clamped["offset"]) == (500, 0)


def test_business_processes_are_unique_and_tracked(pestle):
    created = pestle.manage(
        "POST", "/v1/pestle/business-processes", {"name": f" {HOSTILE} "}
    )
    assert (
        created["name"] == HOSTILE and created["created_by"] == pestle.manager.username
    )
    payroll = pestle.manage(
        "POST", "/v1/pestle/business-processes", {"name": "Payroll"}
    )
    response = pestle.manage(
        "POST", "/v1/pestle/business-processes", {"name": "PAYROLL"}, status=400
    )
    assert response["detail"] == "Business process already exists"
    pestle.manage("POST", "/v1/pestle/business-processes", {"name": "  "}, status=400)
    pestle.manage("POST", "/v1/pestle/business-processes", {"name": ""}, status=422)

    listed = pestle.read("GET", "/v1/pestle/business-processes")
    assert listed["total"] == 2
    assert [
        p["name"]
        for p in pestle.read("GET", "/v1/pestle/business-processes", q="pay")["items"]
    ] == ["Payroll"]
    page = pestle.read("GET", "/v1/pestle/business-processes", limit=1, offset=1)
    assert len(page["items"]) == 1 and page["total"] == 2

    path = f"/v1/pestle/business-processes/{payroll['id']}"
    renamed = pestle.manage("PATCH", path, {"name": "Payroll run"})
    assert renamed["name"] == "Payroll run"
    response = pestle.manage("PATCH", path, {"name": HOSTILE.upper()}, status=400)
    assert response["detail"] == "Business process already exists"
    pestle.manage(
        "PATCH", "/v1/pestle/business-processes/nope", {"name": "X"}, status=400
    )
    pestle.manage(
        "PATCH",
        f"/v1/pestle/business-processes/{uuid.uuid4()}",
        {"name": "X"},
        status=404,
    )
    history = pestle.read("GET", f"{path}/changelog")["items"]
    assert [entry["action"] for entry in history] == ["updated", "created"]
    assert pestle.manage("DELETE", path) == {"ok": True}
    pestle.read("GET", f"{path}/changelog", status=404)
    assert (
        pestle.db.query(EntityChangelog)
        .filter_by(entity_id=uuid.UUID(payroll["id"]), action="deleted")
        .count()
        == 1
    )


def test_items_link_business_processes_and_clauses_by_relevance(pestle):
    item = _item(pestle)
    payroll = pestle.manage(
        "POST", "/v1/pestle/business-processes", {"name": "Payroll"}
    )
    sales = pestle.manage("POST", "/v1/pestle/business-processes", {"name": "Sales"})
    support = pestle.manage(
        "POST", "/v1/pestle/business-processes", {"name": "Support"}
    )
    path = f"/v1/pestle/items/{item['id']}"

    initial = pestle.read("GET", f"{path}/business-processes")
    assert {row["relevance"]["code"] for row in initial["items"]} == {"na"}
    linked = pestle.manage(
        "PATCH",
        f"{path}/business-processes",
        {
            "items": [
                {"business_process_id": payroll["id"], "relevance_code": "high"},
                {
                    "business_process_id": payroll["id"],
                    "relevance_code": "low",
                },  # first wins
                {"business_process_id": sales["id"], "relevance_code": "2"},
                {"business_process_id": support["id"], "relevance_code": "n/a"},
                {"relevance_code": "high"},
            ]
        },
    )
    relevance = {
        row["business_process"]["name"]: row["relevance"]["code"]
        for row in linked["items"]
    }
    assert relevance == {"Payroll": "high", "Sales": "medium", "Support": "na"}
    assert linked["item"]["business_process_count"] == 2

    # A failed replacement leaves the existing links in place.
    response = pestle.manage(
        "PATCH",
        f"{path}/business-processes",
        {
            "items": [
                {"business_process_id": str(uuid.uuid4()), "relevance_code": "high"}
            ]
        },
        status=404,
    )
    assert response["detail"] == "Business process not found"
    pestle.manage(
        "PATCH",
        f"{path}/business-processes",
        {"items": [{"business_process_id": sales["id"], "relevance_code": "urgent"}]},
        status=400,
    )
    assert pestle.db.query(PestleBusinessProcessRelevance).count() == 2

    clauses = pestle.read("GET", f"{path}/clauses")
    assert [row["clause"]["ref"] for row in clauses["items"]] == ["4.1", "4.2", "10"]
    response = pestle.manage(
        "PATCH",
        f"{path}/clauses",
        {"items": [{"clause_id": str(pestle.other.id), "relevance_code": "high"}]},
        status=404,
    )
    assert response["detail"] == "Clause not found"
    pestle.manage(
        "PATCH", f"{path}/clauses", {"items": [{"clause_id": "nope"}]}, status=422
    )
    linked_clauses = pestle.manage(
        "PATCH",
        f"{path}/clauses",
        {
            "items": [
                {"clause_id": str(pestle.context.id), "relevance_code": "high"},
                {"clause_id": str(pestle.parties.id), "relevance_code": "na"},
                {"clause_id": str(pestle.improvement.id), "relevance_code": "low"},
            ]
        },
    )
    codes = {
        row["clause"]["ref"]: row["relevance"]["code"]
        for row in linked_clauses["items"]
    }
    assert codes == {"4.1": "high", "4.2": "na", "10": "low"}

    detail = pestle.read("GET", path)
    assert [bp["business_process"]["name"] for bp in detail["business_processes"]] == [
        "Payroll",
        "Sales",
    ]
    assert [c["clause"]["ref"] for c in detail["clauses"]] == ["4.1", "10"]
    # Related controls come through clauses with a relevance other than N/A.
    assert [c["ref"] for c in detail["related_controls"]] == ["A.2", "A.10"]
    assert detail["clause_count"] == 2

    clause_view = pestle.read("GET", f"/v1/clauses/{pestle.context.id}/pestle")
    assert clause_view["clause"]["ref"] == "4.1"
    assert [
        (row["pestle_item"]["id"], row["relevance"]["code"])
        for row in clause_view["items"]
    ] == [(item["id"], "high")]
    assert pestle.read("GET", f"/v1/clauses/{pestle.parties.id}/pestle")["items"] == []
    pestle.read("GET", f"/v1/clauses/{uuid.uuid4()}/pestle", status=404)
    pestle.read("GET", "/v1/clauses/nope/pestle", status=400)

    history = pestle.read("GET", f"{path}/changelog")["items"]
    paths = {entry["request_path"] for entry in history}
    assert {f"{path}/business-processes", f"{path}/clauses"} <= paths


def test_visualisations_summarise_items_and_links(pestle):
    legal = _item(pestle)
    _item(
        pestle,
        type="Social",
        lens="Internal",
        item="Hybrid working",
        overall_relevance_code="medium",
    )
    _item(pestle, framework="B", item="Elsewhere")
    process = pestle.manage(
        "POST", "/v1/pestle/business-processes", {"name": "Payroll"}
    )
    path = f"/v1/pestle/items/{legal['id']}"
    pestle.manage(
        "PATCH",
        f"{path}/business-processes",
        {"items": [{"business_process_id": process["id"], "relevance_code": "high"}]},
    )
    pestle.manage(
        "PATCH",
        f"{path}/clauses",
        {"items": [{"clause_id": str(pestle.context.id), "relevance_code": "medium"}]},
    )

    view = pestle.read("GET", "/v1/pestle/visualisations", framework="A")
    assert view["framework"] == "A" and len(view["items"]) == 2
    assert view["lens_type_counts"]["External"]["Legal"] == 1
    assert view["lens_type_counts"]["Internal"]["Social"] == 1
    assert view["lens_type_counts"]["Internal"]["Legal"] == 0
    counts = {row["code"]: row["count"] for row in view["relevance_counts"]}
    assert counts == {"na": 0, "high": 1, "medium": 1, "low": 0}
    assert [
        (link["clause_ref"], link["relevance_code"]) for link in view["clause_links"]
    ] == [("4.1", "medium")]
    assert [
        (link["business_process"], link["relevance_code"])
        for link in view["process_links"]
    ] == [("Payroll", "high")]
    empty = pestle.read("GET", "/v1/pestle/visualisations", framework="C")
    assert empty["items"] == [] and empty["clause_links"] == []
    pestle.read("GET", "/v1/pestle/visualisations", framework="A B", status=400)


def test_clause_links_skip_blank_entries_and_keep_the_first_of_duplicates(pestle):
    from app.db.models import PestleClauseRelevance

    item = _item(pestle)
    path = f"/v1/pestle/items/{item['id']}/clauses"
    linked = pestle.manage(
        "PATCH",
        path,
        {
            "items": [
                {"relevance_code": "high"},
                {"clause_id": str(pestle.context.id), "relevance_code": "high"},
                {"clause_id": str(pestle.context.id), "relevance_code": "low"},
                {"clause_id": str(pestle.improvement.id), "relevance_code": "low"},
            ]
        },
    )
    codes = {row["clause"]["ref"]: row["relevance"]["code"] for row in linked["items"]}
    assert codes == {"4.1": "high", "4.2": "na", "10": "low"}
    assert (
        pestle.db.query(PestleClauseRelevance)
        .filter_by(pestle_item_id=uuid.UUID(item["id"]))
        .count()
        == 2
    )


def test_pestle_access_and_text_helpers_guard_their_bounds(pestle, monkeypatch):
    # Anonymous and deactivated callers have no PESTLE access; the text and
    # framework cleaners refuse oversized or missing values.
    from app.core.config import settings
    from app.services import pestle as service
    from app.services.errors import DomainError

    assert service._can_read_pestle(pestle.db, None) is False
    assert service._can_manage_pestle(pestle.db, None) is False
    pestle.manager.is_active = False
    pestle.db.commit()
    assert service._can_read_pestle(pestle.db, pestle.manager) is False
    assert service._can_manage_pestle(pestle.db, pestle.manager) is False
    pestle.manager.is_active = True
    pestle.db.commit()
    assert service._can_manage_pestle(pestle.db, pestle.manager) is True
    with pytest.raises(DomainError, match="item is too long"):
        service._clean_text("x" * 11, max_len=10, label="item")
    monkeypatch.setattr(settings, "default_framework_slug", "")
    with pytest.raises(DomainError, match="framework is required"):
        service._clean_framework("  ")
    with pytest.raises(DomainError, match="Invalid framework"):
        service._clean_framework("bad slug")
