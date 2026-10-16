"""Framework catalogue, selection and editor workflows through the assembled app.

Any signed-in user lists the enabled frameworks; administrators choose the
enabled set and edit framework nodes.
"""

from datetime import datetime

import pytest
from app.core.config import settings
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    Event,
    Framework,
    FrameworkClause,
    Mapping,
)

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

HOSTILE = '<img src=x onerror="alert(1)">'


def _send(client, method, path, body=None, status=200):
    response = client.request(
        method,
        path,
        headers=unsafe(client) if method != "GET" else {},
        **({"json": body} if body is not None else {}),
    )
    assert response.status_code == status, response.text
    return response.json() if response.content else None


@pytest.fixture
def admin(keen, monkeypatch):  # noqa: F811 - fixture imported above
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    monkeypatch.setattr(settings, "default_framework_slug", "A")
    keen.db.add_all([Framework(slug="A", name="Framework A"), Framework(slug="B")])
    keen.db.commit()
    client = keen.client(keen.user(role="admin"))
    return lambda *args, **kwargs: _send(client, *args, **kwargs)


def _node(kind="annex_control", ref="A.1", title="Logging", **kw):
    return {"kind": kind, "ref": ref, "title": title, **kw}


def test_framework_administration_needs_an_administrator(keen, admin):  # noqa: F811
    user = keen.client(keen.user("risk.manage", "isms.manage"))
    assert [f["slug"] for f in _send(user, "GET", "/v1/frameworks")["items"]] == [
        "A",
        "B",
    ]
    assert keen.client().get("/v1/frameworks").status_code == 401
    for method, path, body in (
        ("GET", "/v1/admin/framework-selection", None),
        (
            "PUT",
            "/v1/admin/framework-selection",
            {"enabled": ["A"], "default": "A", "version": 0},
        ),
        ("POST", "/v1/frameworks", {"slug": "C"}),
        ("GET", "/v1/admin/frameworks/A/nodes", None),
        ("PUT", "/v1/admin/frameworks/A/nodes/annex_control/A.1", _node()),
        ("DELETE", "/v1/admin/frameworks/A/nodes/annex_control/A.1", None),
    ):
        _send(user, method, path, body, status=403)
    assert keen.db.query(Framework).count() == 2
    assert keen.db.query(ControlItem).count() == 0


def test_the_organisation_chooses_enabled_frameworks_and_a_default(keen, admin):  # noqa: F811
    selection = admin("GET", "/v1/admin/framework-selection")
    assert selection["version"] == 0 and selection["default"] == "A"
    assert {(i["slug"], i["enabled"], i["allowed"]) for i in selection["items"]} == {
        ("A", True, True),
        ("B", True, True),
    }
    saved = admin(
        "PUT",
        "/v1/admin/framework-selection",
        {"enabled": ["B"], "default": "B", "version": 0},
    )
    assert saved["version"] == 1 and saved["default"] == "B"
    listed = _send(keen.client(keen.user()), "GET", "/v1/frameworks")
    assert [(f["slug"], f["is_default"]) for f in listed["items"]] == [("B", True)]
    assert listed["default"] == "B"

    stale = admin(
        "PUT",
        "/v1/admin/framework-selection",
        {"enabled": ["A", "B"], "default": "A", "version": 0},
        status=409,
    )
    assert stale["detail"].startswith("Configuration changed in another session")
    for body, detail in (
        (
            {"enabled": ["A", "A"], "default": "A", "version": 1},
            "Choose distinct frameworks from the catalogue",
        ),
        (
            {"enabled": ["A", "Z"], "default": "A", "version": 1},
            "Choose distinct frameworks from the catalogue",
        ),
        (
            {"enabled": ["A"], "default": "B", "version": 1},
            "The default framework must be selected",
        ),
    ):
        assert (
            admin("PUT", "/v1/admin/framework-selection", body, status=400)["detail"]
            == detail
        )
    admin(
        "PUT",
        "/v1/admin/framework-selection",
        {"enabled": [], "default": "A", "version": 1},
        status=422,
    )


def test_the_environment_restriction_bounds_the_selection(keen, admin, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "enabled_frameworks", "A, Missing")
    selection = admin("GET", "/v1/admin/framework-selection")
    assert selection["environment_restricted"] is True
    assert selection["unknown_environment_slugs"] == ["Missing"]
    allowed = {i["slug"]: (i["enabled"], i["allowed"]) for i in selection["items"]}
    assert allowed == {"A": (True, True), "B": (False, False)}
    response = admin(
        "PUT",
        "/v1/admin/framework-selection",
        {"enabled": ["A", "B"], "default": "A", "version": 0},
        status=400,
    )
    assert (
        response["detail"]
        == "A selected framework is excluded by KEEN_ENABLED_FRAMEWORKS"
    )
    listed = _send(keen.client(keen.user()), "GET", "/v1/frameworks")
    assert [f["slug"] for f in listed["items"]] == ["A"]


def test_frameworks_are_created_and_updated_by_slug(keen, admin):  # noqa: F811
    created = admin(
        "POST",
        "/v1/frameworks",
        {
            "slug": " CUSTOM:1.0 ",
            "name": f" {HOSTILE} ",
            "version": " 1.0 ",
            "description": " Internal ",
            "upstream_url": " https://standards.example/custom ",
        },
    )
    assert created["created"] is True
    assert created["item"] == {
        "slug": "CUSTOM:1.0",
        "name": HOSTILE,
        "version": "1.0",
        "description": "Internal",
        "upstream_url": "https://standards.example/custom",
        "is_default": False,
    }
    updated = admin("POST", "/v1/frameworks", {"slug": "CUSTOM:1.0", "version": ""})
    assert updated["created"] is False
    assert (
        updated["item"]["name"] == "CUSTOM:1.0" and updated["item"]["version"] is None
    )
    assert (
        admin("POST", "/v1/frameworks", {"slug": "  "}, status=400)["detail"]
        == "slug is required"
    )
    assert keen.db.query(Framework).count() == 3


@pytest.mark.parametrize("slug", ["ISO 27001", "x" * 65, "a/b", HOSTILE])
def test_framework_slugs_follow_the_shared_slug_rule(keen, admin, slug):  # noqa: F811
    # Every framework parameter elsewhere accepts only letters, digits and
    # ". _ : -" up to 64 characters, so other slugs could never be selected.
    response = admin("POST", "/v1/frameworks", {"slug": slug}, status=400)
    assert response["detail"] == (
        "Framework slugs use up to 64 letters, digits, '.', '_', ':' or '-'"
    )
    assert keen.db.query(Framework).count() == 2


def test_framework_nodes_are_edited_with_parent_and_clause_rules(keen, admin):  # noqa: F811
    base = "/v1/admin/frameworks/A/nodes"
    admin("PUT", f"{base}/clause/4", _node("clause", "4", "Context", sort_order=1))
    child = admin(
        "PUT",
        f"{base}/clause/4.1",
        _node("clause", "4.1", f"  {HOSTILE}  ", parent_ref="4", sort_order=2),
    )
    assert child["created"] is True
    control = admin(
        "PUT",
        f"{base}/annex_control/A.1",
        _node(
            ref="A.1",
            clause_refs=["4.1", "4.1"],
            upstream_url="https://standards.example/A.1",
            description="Collect logs",
        ),
    )
    nodes = {(n["kind"], n["ref"]): n for n in admin("GET", base)["items"]}
    assert nodes[("clause", "4.1")]["parent_ref"] == "4"
    assert nodes[("clause", "4.1")]["title"] == HOSTILE
    assert nodes[("clause", "4.1")]["sort_order"] == 2
    assert nodes[("annex_control", "A.1")]["clause_refs"] == ["4.1"]
    assert (
        nodes[("annex_control", "A.1")]["upstream_url"]
        == "https://standards.example/A.1"
    )
    clause = (
        keen.db.query(FrameworkClause).filter_by(framework_slug="A", ref="4.1").one()
    )
    assert clause.parent.ref == "4" and clause.title == HOSTILE

    again = admin("PUT", f"{base}/annex_control/A.1", _node(ref="A.1", in_scope=False))
    assert again == {"ok": True, "created": False, "id": control["id"]}
    scope = {n["ref"]: n["in_scope"] for n in admin("GET", base)["items"]}
    assert scope["A.1"] is False and scope["4.1"] is True

    rejected = (
        (
            "annex_control/A.2",
            _node(ref="A.3"),
            "Node kind and reference must match the URL",
        ),
        (
            "annex_control/A.2",
            _node(ref="A.2", title="  "),
            "Invalid kind, reference or title",
        ),
        (
            "annex_control/A.2",
            _node(ref="A.2", parent_ref="A.2"),
            "A node cannot be its own parent",
        ),
        (
            "annex_control/A.2",
            _node(ref="A.2", parent_ref="4"),
            "Parent must exist in the same framework and category",
        ),
        (
            "annex_control/A.2",
            _node(ref="A.2", clause_refs=["9.9"]),
            "Unknown clause: 9.9",
        ),
        (
            "clause/5",
            _node("clause", "5", "Leadership", clause_refs=["4"]),
            "Only controls can link to clauses",
        ),
        (
            "annex_control/A.2",
            _node(ref="A.2", upstream_url="javascript:alert(1)"),
            "Upstream URL must be an absolute HTTP(S) URL",
        ),
    )
    for path, body, detail in rejected:
        assert admin("PUT", f"{base}/{path}", body, status=400)["detail"] == detail
    response = admin(
        "PUT",
        "/v1/admin/frameworks/NOPE/nodes/annex_control/X.1",
        _node(ref="X.1"),
        status=404,
    )
    assert response["detail"] == "Create the framework first"

    # Parents must not form a cycle.
    admin("PUT", f"{base}/custom/C.1", _node("custom", "C.1", "Root"))
    admin(
        "PUT", f"{base}/custom/C.2", _node("custom", "C.2", "Child", parent_ref="C.1")
    )
    response = admin(
        "PUT",
        f"{base}/custom/C.1",
        _node("custom", "C.1", "Root", parent_ref="C.2"),
        status=400,
    )
    assert response["detail"] == "Parent relationship would form a cycle"


def test_framework_nodes_are_deleted_only_without_dependants(keen, admin):  # noqa: F811
    base = "/v1/admin/frameworks/A/nodes"
    admin("PUT", f"{base}/clause/4", _node("clause", "4", "Context"))
    admin("PUT", f"{base}/clause/4.1", _node("clause", "4.1", "Needs", parent_ref="4"))
    admin("PUT", f"{base}/annex_control/A.1", _node(clause_refs=["4.1"]))

    for path, detail in (
        ("clause/4", "Move or delete child nodes first"),
        ("clause/4.1", "Remove control links first"),
        ("annex_control/A.1", "Remove clause links first"),
    ):
        assert admin("DELETE", f"{base}/{path}", status=409)["detail"] == detail
    admin("DELETE", f"{base}/annex_control/A.9", status=404)

    admin("PUT", f"{base}/annex_control/A.1", _node(clause_refs=[]))
    control = keen.db.query(ControlItem).filter_by(framework_slug="A", ref="A.1").one()
    event = Event(
        source="jenkins",
        system="ci",
        action="deploy",
        outcome="success",
        severity=2,
        summary="Deploy",
        timestamp=datetime(2026, 3, 1, 12, 0),
        external_id="evt-1",
        raw_pointer={},
        normalized_payload={},
    )
    keen.db.add(event)
    keen.db.flush()
    keen.db.add(Mapping(event_id=event.id, control_item_id=control.id, method="rule"))
    keen.db.commit()
    response = admin("DELETE", f"{base}/annex_control/A.1", status=409)
    assert response["detail"] == "Remove event mappings before deleting this node"

    assert admin("DELETE", f"{base}/clause/4.1") == {"ok": True}
    assert (
        keen.db.query(FrameworkClause).filter_by(framework_slug="A", ref="4.1").count()
        == 0
    )
    assert keen.db.query(ControlClauseLink).count() == 0
    admin("PUT", f"{base}/custom/C.1", _node("custom", "C.1", "Root"))
    admin(
        "PUT", f"{base}/custom/C.2", _node("custom", "C.2", "Child", parent_ref="C.1")
    )
    response = admin("DELETE", f"{base}/custom/C.1", status=409)
    assert response["detail"] == "Move or delete child nodes first"
