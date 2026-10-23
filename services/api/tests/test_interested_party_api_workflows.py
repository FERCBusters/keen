"""Interested parties register workflows through the assembled application.

Parties, their name and nature lookups, control links, communication rules,
visualisations and change history are exercised with real sign-in, CSRF and
permission resolution.
"""

import uuid
from types import SimpleNamespace

import pytest
from app.db.models import (
    ControlItem,
    EntityChangelog,
    Framework,
    InterestedParty,
    InterestedPartyName,
)

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

HOSTILE = '<img src=x onerror="alert(1)">'
BREACH = {
    "event": "notifiable event",
    "when": "within 72 HOURS",
    "with_whom": "ico",
    "methods": ["ICO Portal", "email", "Email"],
}


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
def parties(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add_all([Framework(slug="A"), Framework(slug="B")])
    controls = {
        ref: ControlItem(
            framework_slug=fw, ref=ref, type="annex_control", title=f"{ref} title"
        )
        for fw, ref in (("A", "A.5.10"), ("A", "A.5.2"), ("B", "B.1"))
    }
    db.add_all(controls.values())
    db.commit()
    manager = keen.user("interested_parties.manage")
    manager_client = keen.client(manager)
    reader_client = keen.client(keen.user("interested_parties.read"))
    return SimpleNamespace(
        keen=keen,
        db=db,
        controls=controls,
        manager=manager,
        manage=lambda *a, **k: _send(manager_client, *a, **k),
        read=lambda *a, **k: _send(reader_client, *a, **k),
    )


def _party(parties, **overrides):
    body = {
        "framework": "A",
        "name": "Customers",
        "nature": "Data subjects",
        "note": "Retail customers",
    }
    body.update(overrides)
    return parties.manage("POST", "/v1/interested-parties", body)


def test_party_routes_follow_the_read_and_manage_permissions(parties):
    app = parties.keen
    party = _party(parties)
    name_id, nature_id = party["name_id"], party["nature_id"]
    control = parties.controls["A.5.2"]
    reads = (
        "/v1/interested-parties",
        "/v1/interested-parties/names",
        "/v1/interested-parties/natures",
        "/v1/interested-parties/visualisations",
        f"/v1/interested-parties/{party['id']}",
        f"/v1/interested-parties/{party['id']}/controls",
        f"/v1/interested-parties/{party['id']}/communications",
        f"/v1/interested-parties/{party['id']}/changelog",
        f"/v1/controls/{control.id}/interested-parties",
    )
    anonymous = app.client()
    nobody = app.client(app.user())
    risk_reader = app.client(app.user("risk.read"))
    for path in reads:
        assert anonymous.get(path).status_code == 401, path
        _send(nobody, "GET", path, status=403, framework="A")
        parties.read("GET", path, framework="A")
        _send(risk_reader, "GET", path, framework="A")
    writes = (
        (
            "POST",
            "/v1/interested-parties",
            {"framework": "A", "name": "X", "nature": "Y"},
        ),
        ("PATCH", f"/v1/interested-parties/{party['id']}", {"note": "changed"}),
        ("DELETE", f"/v1/interested-parties/{party['id']}", None),
        ("PATCH", f"/v1/interested-parties/{party['id']}/controls", {"controls": []}),
        (
            "PATCH",
            f"/v1/interested-parties/{party['id']}/communications",
            {"items": []},
        ),
        ("POST", "/v1/interested-parties/names", {"name": "Regulators"}),
        ("PATCH", f"/v1/interested-parties/names/{name_id}", {"name": "Clients"}),
        ("DELETE", f"/v1/interested-parties/names/{name_id}", None),
        ("POST", "/v1/interested-parties/natures", {"name": "Enforcement"}),
        ("PATCH", f"/v1/interested-parties/natures/{nature_id}", {"name": "Owners"}),
        ("DELETE", f"/v1/interested-parties/natures/{nature_id}", None),
    )
    for method, path, body in writes:
        parties.read(method, path, body, status=403)
        _send(risk_reader, method, path, body, status=403)
        _send(nobody, method, path, body, status=403)
    current = parties.read("GET", f"/v1/interested-parties/{party['id']}")
    assert (
        current["note"] == "Retail customers" and current["name"]["name"] == "Customers"
    )

    # Signed-in users can read the choices and their own permissions.
    meta = _send(nobody, "GET", "/v1/interested-parties/meta", framework="A")
    assert (meta["can_view"], meta["can_manage"]) == (False, False)
    meta = parties.manage("GET", "/v1/interested-parties/meta", framework="A")
    assert (meta["can_view"], meta["can_manage"]) == (True, True)
    assert "ICO Portal" in meta["methods"] and "Notifiable Event" in meta["events"]
    _send(nobody, "GET", "/v1/interested-parties/meta", status=400, framework="A B")
    risk_manager = app.client(app.user("risk.manage"))
    _send(
        risk_manager,
        "PATCH",
        f"/v1/interested-parties/{party['id']}",
        {"note": "Reviewed"},
    )


def test_parties_are_created_with_lookups_controls_and_communications(parties):
    created = _party(
        parties,
        name=f"  {HOSTILE}  ",
        nature=" Data subjects ",
        note=f" {HOSTILE} ",
        controls=["a.5.10", str(parties.controls["A.5.2"].id), "A.5.10"],
        communications=[BREACH],
    )
    assert created["name"]["name"] == HOSTILE and created["note"] == HOSTILE
    assert created["label"] == f"{HOSTILE} - Data subjects"
    assert [c["ref"] for c in created["controls"]] == ["A.5.2", "A.5.10"]
    assert created["communications"][0] | {
        "id": None,
        "created_at": None,
        "updated_at": None,
    } == {
        "id": None,
        "event": "Notifiable Event",
        "when": "Within 72 hours",
        "with_whom": "ICO",
        "methods": ["ICO Portal", "Email"],
        "created_at": None,
        "updated_at": None,
    }
    assert (created["control_count"], created["communication_count"]) == (2, 1)

    # The same name and nature reuse their lookups case-insensitively, and a
    # party is one record for the organisation whichever framework is viewed.
    duplicate = parties.manage(
        "POST",
        "/v1/interested-parties",
        {"framework": "B", "name": HOSTILE.upper(), "nature": "DATA SUBJECTS"},
        status=400,
    )
    assert duplicate["detail"] == (
        "Interested party with this name and nature already exists"
    )
    by_id = _party(parties, name=None, name_id=created["name_id"], nature="Owners")
    assert by_id["name_id"] == created["name_id"]

    rejected = (
        (
            {"name": HOSTILE, "nature": "data subjects"},
            "Interested party with this name and nature already exists",
        ),
        ({"name_id": str(uuid.uuid4()), "name": None}, "Unknown name_id"),
        ({"nature_id": str(uuid.uuid4()), "nature": None}, "Unknown nature_id"),
        ({"name": "  "}, "name is required"),
        ({"controls": ["B.1"]}, "Unknown control: B.1"),
        ({"controls": [" "]}, "control id/ref cannot be blank"),
        ({"communications": [{**BREACH, "event": "Party"}]}, "event is not valid"),
        ({"communications": [{**BREACH, "when": "Never"}]}, "when is not valid"),
        (
            {"communications": [{**BREACH, "with_whom": "Press"}]},
            "with_whom is not valid",
        ),
        (
            {"communications": [{**BREACH, "methods": ["Pigeon"]}]},
            "method is not valid",
        ),
        (
            {"communications": [{**BREACH, "methods": []}]},
            "At least one method is required",
        ),
        ({"framework": "A B"}, "Invalid framework"),
    )
    before = parties.db.query(InterestedParty).count()
    for overrides, detail in rejected:
        body = {"framework": "A", "name": "Fresh", "nature": "Brand new", **overrides}
        response = parties.manage("POST", "/v1/interested-parties", body, status=400)
        assert response["detail"] == detail, overrides
    assert parties.db.query(InterestedParty).count() == before
    # Lookups created for a rejected party are not kept.
    assert parties.db.query(InterestedPartyName).filter_by(name="Fresh").count() == 0
    parties.manage(
        "POST",
        "/v1/interested-parties",
        {"name": "X", "nature": "Y", "note": "x" * 4001},
        status=422,
    )


def test_parties_are_updated_relinked_and_deleted(parties):
    party = _party(parties, controls=["A.5.2"], communications=[BREACH])
    path = f"/v1/interested-parties/{party['id']}"

    renamed = parties.manage(
        "PATCH",
        path,
        {"framework": "A", "name": "Clients", "nature": "Account holders", "note": ""},
    )
    assert (renamed["name"]["name"], renamed["nature"]["name"], renamed["note"]) == (
        "Clients",
        "Account holders",
        "",
    )
    assert renamed["control_count"] == 1  # controls not sent, so unchanged
    # The party is one record for the organisation; a framework in the payload
    # says which framework's control links are described and shown.
    in_b = parties.manage("PATCH", path, {"framework": "B"})
    assert (in_b["framework"], in_b["control_count"]) == ("B", 0)
    assert parties.read("GET", path, framework="A")["control_count"] == 1

    # Control links are replaced per framework; the framework is named with
    # the request, since the party itself belongs to none.
    controls = parties.manage(
        "PATCH", f"{path}/controls", {"controls": ["A.5.10"]}, framework="A"
    )
    assert [c["ref"] for c in controls["controls"]] == ["A.5.10"]
    assert [
        c["ref"]
        for c in parties.read("GET", f"{path}/controls", framework="A")["controls"]
    ] == ["A.5.10"]
    parties.manage("PATCH", f"{path}/controls", {"controls": ["A.5.10"]}, status=400)
    routine = {
        "event": "Business as Usual",
        "when": "Routine",
        "with_whom": "Sender",
        "methods": ["Email"],
    }
    comms = parties.manage(
        "PATCH", f"{path}/communications", {"items": [BREACH, routine]}
    )
    assert comms["communication_count"] == 2
    listed = parties.read("GET", f"{path}/communications")["items"]
    assert {c["event"] for c in listed} == {"Notifiable Event", "Business as Usual"}
    parties.manage(
        "PATCH",
        f"{path}/communications",
        {"items": [{**routine, "methods": ["Fax"]}]},
        status=400,
    )
    assert len(parties.read("GET", f"{path}/communications")["items"]) == 2

    cleared = parties.manage("PATCH", path, {"controls": [], "communications": []})
    assert (cleared["control_count"], cleared["communication_count"]) == (0, 0)
    moved = parties.manage("PATCH", path, {"framework": "B", "controls": ["B.1"]})
    assert moved["framework"] == "B" and [c["ref"] for c in moved["controls"]] == [
        "B.1"
    ]

    other = _party(parties, framework="B", name="Investors", nature="Owners")
    response = parties.manage(
        "PATCH",
        f"/v1/interested-parties/{other['id']}",
        {"name": "Clients", "nature": "Account holders"},
        status=400,
    )
    assert (
        response["detail"]
        == "Interested party with this name and nature already exists"
    )

    history = parties.read("GET", f"{path}/changelog")["items"]
    assert [entry["action"] for entry in history].count("updated") >= 5
    assert history[-1]["action"] == "created"
    assert parties.manage("DELETE", path) == {"ok": True, "id": party["id"]}
    parties.read("GET", path, status=404)
    parties.manage("DELETE", path, status=404)
    parties.read("GET", "/v1/interested-parties/nope", status=400)
    deleted = (
        parties.db.query(EntityChangelog)
        .filter_by(entity_id=uuid.UUID(party["id"]), action="deleted")
        .one()
    )
    assert deleted.entity_type == "interested_party"


def test_parties_are_listed_with_filters(parties):
    customers = _party(parties, controls=["A.5.2"])
    staff = _party(
        parties, name="Staff", nature="Employees", note="Includes contractors"
    )
    regulators = _party(parties, name="Regulators", nature="Enforcement")
    # A party created while viewing another framework is the same list.
    elsewhere = _party(parties, framework="B", name="Zed", nature="Other")

    def ids(**params):
        return [
            p["id"]
            for p in parties.read(
                "GET", "/v1/interested-parties", framework="A", **params
            )["items"]
        ]

    # Ordered by nature, then name.
    assert ids() == [customers["id"], staff["id"], regulators["id"], elsewhere["id"]]
    assert ids(q="contractor") == [staff["id"]]
    assert ids(q="ENFORCE") == [regulators["id"]]
    assert ids(name_id=f"{staff['name_id']},{regulators['name_id']}") == [
        staff["id"],
        regulators["id"],
    ]
    assert ids(nature_id=customers["nature_id"]) == [customers["id"]]
    assert ids(control="a.5.2") == [customers["id"]]
    assert ids(control=str(parties.controls["A.5.2"].id)) == [customers["id"]]
    for params in ({"name_id": "nope"}, {"nature_id": "nope"}, {"control": "Z.9"}):
        parties.read(
            "GET", "/v1/interested-parties", status=400, framework="A", **params
        )
    page = parties.read(
        "GET", "/v1/interested-parties", framework="A", limit=1, offset=2
    )
    assert [p["id"] for p in page["items"]] == [regulators["id"]] and page["total"] == 4

    by_control = parties.read(
        "GET",
        f"/v1/controls/{parties.controls['A.5.2'].id}/interested-parties",
        framework="A",
    )
    assert by_control["control"]["ref"] == "A.5.2"
    assert [p["id"] for p in by_control["items"]] == [customers["id"]]
    assert by_control["items"][0]["controls"] == []  # not repeated per party
    parties.read(
        "GET",
        f"/v1/controls/{parties.controls['B.1'].id}/interested-parties",
        status=404,
        framework="A",
    )
    parties.read(
        "GET", "/v1/controls/nope/interested-parties", status=400, framework="A"
    )


def test_name_and_nature_lookups_are_managed_and_protected(parties):
    party = _party(parties)
    names = "/v1/interested-parties/names"
    natures = "/v1/interested-parties/natures"
    regulators = parties.manage("POST", names, {"name": f" {HOSTILE} "})
    assert regulators["name"] == HOSTILE
    assert (
        parties.manage("POST", names, {"name": HOSTILE.upper()})["id"]
        == regulators["id"]
    )
    assert [n["name"] for n in parties.read("GET", names, q="CUSTOM")["items"]] == [
        "Customers"
    ]
    renamed = parties.manage(
        "PATCH", f"{names}/{regulators['id']}", {"name": "Regulators"}
    )
    assert renamed["name"] == "Regulators"
    response = parties.manage(
        "PATCH", f"{names}/{regulators['id']}", {"name": "customers"}, status=400
    )
    assert response["detail"] == "Name already exists"
    response = parties.manage("DELETE", f"{names}/{party['name_id']}", status=400)
    assert response["detail"] == "Name is used by an interested party"
    assert parties.manage("DELETE", f"{names}/{regulators['id']}") == {"ok": True}
    for method, body in (("PATCH", {"name": "X"}), ("DELETE", None)):
        parties.manage(method, f"{names}/nope", body, status=400)
        parties.manage(method, f"{names}/{uuid.uuid4()}", body, status=404)

    owners = parties.manage("POST", natures, {"name": "Owners"})
    assert [n["name"] for n in parties.read("GET", natures, q="own")["items"]] == [
        "Owners"
    ]
    renamed = parties.manage(
        "PATCH", f"{natures}/{owners['id']}", {"name": "Shareholders"}
    )
    assert renamed["name"] == "Shareholders"
    response = parties.manage(
        "PATCH", f"{natures}/{owners['id']}", {"name": "DATA SUBJECTS"}, status=400
    )
    assert response["detail"] == "Nature already exists"
    response = parties.manage("DELETE", f"{natures}/{party['nature_id']}", status=400)
    assert response["detail"] == "Nature is used by an interested party"
    assert parties.manage("DELETE", f"{natures}/{owners['id']}") == {"ok": True}
    for method, body in (("PATCH", {"name": "X"}), ("DELETE", None)):
        parties.manage(method, f"{natures}/nope", body, status=400)
        parties.manage(method, f"{natures}/{uuid.uuid4()}", body, status=404)
    parties.manage(
        "PATCH", f"{natures}/{party['nature_id']}", {"name": "  "}, status=400
    )


def test_visualisations_summarise_controls_and_communications(parties):
    routine = {
        "event": "Business as Usual",
        "when": "Routine",
        "with_whom": "Sender",
        "methods": ["Email"],
    }
    customers = _party(
        parties, controls=["A.5.2", "A.5.10"], communications=[BREACH, routine]
    )
    staff = _party(
        parties,
        name="Staff",
        nature="Data subjects",
        controls=["A.5.2"],
        communications=[routine],
    )
    _party(parties, name="Investors", nature="Owners")

    view = parties.read("GET", "/v1/interested-parties/visualisations", framework="A")
    assert view["framework"] == "A" and len(view["items"]) == 3
    assert view["nature_counts"] == [
        {"name": "Data subjects", "count": 2},
        {"name": "Owners", "count": 1},
    ]
    assert view["event_counts"] == [
        {"name": "Business as Usual", "count": 2},
        {"name": "Notifiable Event", "count": 1},
    ]
    assert {row["name"]: row["count"] for row in view["method_counts"]} == {
        "Email": 3,
        "ICO Portal": 1,
    }
    assert {row["name"]: row["count"] for row in view["with_whom_counts"]} == {
        "Sender": 2,
        "ICO": 1,
    }
    assert [
        (row["control"]["ref"], row["count"]) for row in view["control_counts"]
    ] == [
        ("A.5.2", 2),
        ("A.5.10", 1),
    ]
    assert len(view["control_links"]) == 3 and len(view["communication_links"]) == 3

    graph = view["bipartite_graphs"]["party_controls"]
    assert graph["stats"] == {"left_count": 2, "right_count": 2, "link_count": 3}
    assert [node["ref"] for node in graph["nodes"]["right"]] == ["A.5.2", "A.5.10"]
    assert {node["id"] for node in graph["nodes"]["left"]} == {
        customers["id"],
        staff["id"],
    }
    comms = view["bipartite_graphs"]["party_communications"]
    assert comms["stats"]["right_count"] == 2
    shared = next(node for node in comms["nodes"]["right"] if node["count"] == 2)
    assert shared["label"] == "Business as Usual × Sender"
    # Another framework lists the same parties with no control links of its own.
    empty = parties.read("GET", "/v1/interested-parties/visualisations", framework="C")
    assert len(empty["items"]) == len(view["items"])
    assert all(item["controls"] == [] for item in empty["items"])
    assert empty["bipartite_graphs"]["party_controls"]["links"] == []


def test_party_requests_need_a_framework_when_no_default_is_configured(
    parties, monkeypatch
):
    """An empty framework is refused once the deployment names no default."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "default_framework_slug", "")

    blank = parties.read("GET", "/v1/interested-parties", status=400, framework="")

    assert blank["detail"] == "framework is required"
    invalid = parties.read("GET", "/v1/interested-parties", status=400, framework="A B")
    assert invalid["detail"] == "Invalid framework"


def test_serialisers_and_graph_links_tolerate_missing_records(parties):
    """Missing rows serialise as empty objects and never enter a graph."""
    from app.services import interested_parties as service

    assert service._control_out(None) == {}
    assert service._communication_out(None) == {}
    assert service._party_changelog_state(parties.db, uuid.uuid4()) is None
    assert service._party_changelog_state(parties.db, str(uuid.uuid4())) is None

    graph = service._new_bipartite_graph(
        key="party_controls",
        title="Parties and controls",
        left_label="Parties",
        right_label="Controls",
        empty="None",
    )
    before = dict(graph)
    service._add_bipartite_link(
        graph, left={"id": "", "label": "Nobody"}, right={"id": "c1", "label": "C"}
    )
    service._add_bipartite_link(
        graph, left={"id": "p1", "label": "Party"}, right={"id": None, "label": "C"}
    )
    assert service._finalise_bipartite_graph(dict(graph)) == (
        service._finalise_bipartite_graph(before)
    )
    service._add_bipartite_link(
        graph, left={"id": "p1", "label": "Party"}, right={"id": "c1", "label": "C"}
    )
    assert graph["_links"][("p1", "c1")]["count"] == 1
