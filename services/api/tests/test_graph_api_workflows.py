"""Relationship graphs through the assembled application.

Each graph is checked for its permission rule, distinct-event counting,
date/source filters, crosswalk inheritance and the diary visibility rule.
"""

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    CrossFrameworkControlLink,
    Event,
    Framework,
    FrameworkClause,
    ManagedConfiguration,
    Mapping,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    RiskControlLink,
)

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen  # noqa: F401

HOSTILE = '<img src=x onerror="alert(1)">'


def _event(db, source, *controls, when=datetime(2026, 3, 1, 12, 0), **kw):
    row = Event(
        source=source,
        system="ci",
        action="deploy",
        outcome="success",
        severity=2,
        summary=kw.pop("summary", "Deploy"),
        timestamp=when,
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
        **kw,
    )
    db.add(row)
    db.flush()
    for control in controls:
        db.add(Mapping(event_id=row.id, control_item_id=control.id, method="rule"))
    db.flush()
    return row


@pytest.fixture
def graph(keen, monkeypatch):  # noqa: F811 - fixture imported above
    from app.core import source_meta

    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    # Configured source labels come from the operational configuration store.
    monkeypatch.setattr(
        source_meta,
        "_configured_source_meta",
        lambda _bucket: {"jenkins": {"label": "Jenkins CI", "color": "#336699"}},
    )
    db = keen.db
    db.add_all([Framework(slug="A"), Framework(slug="B")])
    db.flush()
    logging = ControlItem(
        framework_slug="A",
        ref="A.1",
        type="annex_control",
        title="Logging",
        meta={"upstream_url": "https://controls.example/A.1"},
    )
    change = ControlItem(
        framework_slug="A", ref="A.2", type="annex_control", title=HOSTILE
    )
    inherited = ControlItem(
        framework_slug="B", ref="B.1", type="annex_control", title="Monitoring"
    )
    db.add_all([logging, change, inherited])
    db.flush()
    db.add(
        CrossFrameworkControlLink(
            source_control_id=logging.id, target_control_id=inherited.id
        )
    )
    context = FrameworkClause(framework_slug="A", ref="4.1", title="Context")
    planning = FrameworkClause(framework_slug="A", ref="5.1", title="Leadership")
    db.add_all([context, planning])
    db.flush()
    db.add_all(
        [
            ControlClauseLink(
                control_item_id=logging.id,
                clause_id=context.id,
                applicability="applicable",
            ),
            ControlClauseLink(
                control_item_id=change.id,
                clause_id=context.id,
                applicability="partially_applicable",
            ),
            ControlClauseLink(
                control_item_id=change.id,
                clause_id=planning.id,
                applicability="applicable",
            ),
        ]
    )
    # e1 maps to both controls, so counts must not double-count it.
    _event(db, "jenkins", logging, change)
    _event(db, "jenkins", logging)
    _event(db, "github", change)
    _event(db, "diary", logging, summary="Private reflection")
    _event(db, "jenkins", logging, when=datetime(2025, 6, 1))
    _event(db, "jenkins")  # unmapped evidence is not part of any graph
    db.commit()
    return SimpleNamespace(
        keen=keen,
        db=db,
        logging=logging,
        change=change,
        inherited=inherited,
        context=context,
        planning=planning,
    )


def _nodes(payload):
    return {node["id"]: node for node in payload["nodes"]}


def _links(payload):
    return {
        (link["source"], link["target"]): link["value"] for link in payload["links"]
    }


def test_graphs_follow_their_permission_rules(graph):
    app = graph.keen
    anonymous = app.client()
    nobody = app.client(app.user())
    paths = {
        "/v1/graph/control_source": "events.read",
        "/v1/graph/clause_source": "events.read",
        "/v1/graph/risk_control": "risk.read",
    }
    for path, code in paths.items():
        assert anonymous.get(path).status_code == 401, path
        response = nobody.get(path, params={"framework": "A"})
        assert response.status_code == 403, path
        assert response.json()["detail"] == f"{code} permission required"
        allowed = app.client(app.user(code))
        assert allowed.get(path, params={"framework": "A"}).status_code == 200
    # The clause/control catalogue relationship is visible to any signed-in user.
    assert anonymous.get("/v1/graph/clause_control").status_code == 401
    assert (
        nobody.get("/v1/graph/clause_control", params={"framework": "A"}).status_code
        == 200
    )
    manager = app.client(app.user("risk.manage"))
    assert (
        manager.get("/v1/graph/risk_control", params={"framework": "A"}).status_code
        == 200
    )


def test_source_control_graph_counts_distinct_events(graph):
    client = graph.keen.client(graph.keen.user("events.read"))
    payload = client.get("/v1/graph/control_source", params={"framework": "A"}).json()
    assert payload["graph_kind"] == "source_control_events"
    nodes = _nodes(payload)
    logging = f"control:{graph.logging.id}"
    change = f"control:{graph.change.id}"
    assert {key: node["mapped_events"] for key, node in nodes.items()} == {
        "source:jenkins": 3,
        "source:github": 1,
        "source:diary": 1,
        logging: 4,
        change: 2,
    }
    assert _links(payload) == {
        ("source:jenkins", logging): 3,
        ("source:jenkins", change): 1,
        ("source:github", change): 1,
        ("source:diary", logging): 1,
    }
    assert nodes[logging]["upstream_url"] == "https://controls.example/A.1"
    assert nodes[change]["title"] == HOSTILE and nodes[change]["upstream_url"] is None
    assert nodes["source:jenkins"]["node_kind"] == "source"
    assert nodes["source:jenkins"]["label"] == "Jenkins CI"
    assert nodes["source:jenkins"]["color"] == "#336699"

    recent = client.get(
        "/v1/graph/control_source",
        params={"framework": "A", "start_date": "2026-01-01", "end_date": "2026-03-01"},
    ).json()
    assert _nodes(recent)["source:jenkins"]["mapped_events"] == 2
    assert _nodes(recent)[logging]["mapped_events"] == 3
    before = client.get(
        "/v1/graph/control_source",
        params={"framework": "A", "end_date": "2026-02-28"},
    ).json()
    assert _links(before) == {("source:jenkins", logging): 1}
    github = client.get(
        "/v1/graph/control_source", params={"framework": "A", "source": "github"}
    ).json()
    assert _links(github) == {("source:github", change): 1}
    # The graph follows the Events page filters: several sources, a control,
    # search text and a text condition.
    two = client.get(
        "/v1/graph/control_source", params={"framework": "A", "source": "github,diary"}
    ).json()
    assert _links(two) == {("source:github", change): 1, ("source:diary", logging): 1}
    scoped = client.get(
        "/v1/graph/control_source",
        params={
            "framework": "A",
            "control": "A.2",
            "f": "summary:not_contains:private",
        },
    ).json()
    assert _links(scoped) == {
        ("source:jenkins", logging): 1,
        ("source:jenkins", change): 1,
        ("source:github", change): 1,
    }
    assert _nodes(scoped)["source:jenkins"]["mapped_events"] == 1
    reflective = client.get(
        "/v1/graph/control_source", params={"framework": "A", "q": "reflection"}
    ).json()
    assert _links(reflective) == {("source:diary", logging): 1}
    refused = client.get(
        "/v1/graph/control_source", params={"framework": "A", "f": "actor:near:x"}
    )
    assert refused.status_code == 400
    assert (
        client.get(
            "/v1/graph/control_source", params={"start_date": "not-a-date"}
        ).status_code
        == 422
    )


def test_source_control_graph_includes_crosswalk_evidence(graph):
    client = graph.keen.client(graph.keen.user("events.read"))
    payload = client.get("/v1/graph/control_source", params={"framework": "B"}).json()
    inherited = f"control:{graph.inherited.id}"
    # B.1 inherits the four events mapped to A.1 through the explicit crosswalk.
    assert _links(payload) == {
        ("source:jenkins", inherited): 3,
        ("source:diary", inherited): 1,
    }
    assert _nodes(payload)[inherited]["mapped_events"] == 4

    # A crosswalk from a framework the organisation has disabled adds nothing.
    graph.db.add(
        ManagedConfiguration(
            name="organisation-frameworks",
            version=1,
            document={"enabled": ["B"], "default": "B"},
        )
    )
    graph.db.commit()
    disabled = client.get("/v1/graph/control_source", params={"framework": "B"}).json()
    assert disabled["links"] == [] and disabled["nodes"] == []


def test_source_colours_follow_the_viewers_preferences(graph):
    user = graph.keen.user("events.read")
    user.pref_source_colors = {"jenkins": "#123456", "github": "not-a-colour"}
    graph.db.commit()
    payload = (
        graph.keen.client(user)
        .get("/v1/graph/control_source", params={"framework": "A"})
        .json()
    )
    nodes = _nodes(payload)
    assert nodes["source:jenkins"]["color"] == "#123456"
    default = graph.keen.client(graph.keen.user("events.read"))
    defaults = _nodes(
        default.get("/v1/graph/control_source", params={"framework": "A"}).json()
    )
    assert nodes["source:github"]["color"] == defaults["source:github"]["color"]
    assert defaults["source:jenkins"]["color"] != "#123456"


def test_source_clause_graph_fans_events_out_through_clause_links(graph):
    client = graph.keen.client(graph.keen.user("events.read"))
    payload = client.get("/v1/graph/clause_source", params={"framework": "A"}).json()
    assert payload["graph_kind"] == "source_clause_events"
    context = f"clause:{graph.context.id}"
    planning = f"clause:{graph.planning.id}"
    assert _links(payload) == {
        ("source:jenkins", context): 3,
        ("source:jenkins", planning): 1,
        ("source:github", context): 1,
        ("source:github", planning): 1,
        ("source:diary", context): 1,
    }
    nodes = _nodes(payload)
    assert nodes[context]["mapped_events"] == 5
    assert nodes[planning]["mapped_events"] == 2
    assert nodes["source:jenkins"]["mapped_events"] == 3
    assert nodes[context]["node_kind"] == "clause"
    assert nodes[context]["color"] == nodes[context]["color"].lower()

    recent = client.get(
        "/v1/graph/clause_source",
        params={"framework": "A", "start_date": "2026-01-01"},
    ).json()
    assert _links(recent)[("source:jenkins", context)] == 2
    # Framework B has no clauses, so no clause evidence is drawn.
    assert client.get("/v1/graph/clause_source", params={"framework": "B"}).json() == {
        "nodes": [],
        "links": [],
        "graph_kind": "source_clause_events",
    }


def test_clause_control_graph_counts_relationships(graph):
    client = graph.keen.client(graph.keen.user())
    payload = client.get("/v1/graph/clause_control", params={"framework": "A"}).json()
    assert payload["graph_kind"] == "clause_control_relationship"
    nodes = _nodes(payload)
    context = f"clause:{graph.context.id}"
    planning = f"clause:{graph.planning.id}"
    logging = f"control:{graph.logging.id}"
    change = f"control:{graph.change.id}"
    assert {key: node["link_count"] for key, node in nodes.items()} == {
        context: 2,
        planning: 1,
        logging: 1,
        change: 2,
    }
    applicability = {
        (link["source"], link["target"]): link["applicability"]
        for link in payload["links"]
    }
    assert applicability == {
        (context, logging): "applicable",
        (context, change): "partially_applicable",
        (planning, change): "applicable",
    }
    assert nodes[change]["title"] == HOSTILE
    assert nodes[logging]["upstream_url"] == "https://controls.example/A.1"
    assert (
        client.get("/v1/graph/clause_control", params={"framework": "B"}).json()[
            "nodes"
        ]
        == []
    )


def test_risk_control_graph_weights_links_by_risk_score(graph):
    db = graph.db
    category = RiskCategory(name="Data")
    db.add(category)
    db.flush()
    subcategory = RiskAssetSubcategory(category_id=category.id, name="Databases")
    db.add(subcategory)
    db.flush()
    asset = RiskAsset(
        name="Payroll", category_id=category.id, subcategory_id=subcategory.id
    )
    db.add(asset)
    db.flush()
    severe = Risk(
        asset_id=asset.id,
        risk_types=["Confidentiality"],
        threat_summary=f"  {HOSTILE}  ",
        risk_score=20,
        residual_risk_score=4,
    )
    unrated = Risk(asset_id=asset.id, risk_types=["Integrity"], risk_score=0)
    unlinked = Risk(asset_id=asset.id, risk_types=["Availability"], risk_score=9)
    db.add_all([severe, unrated, unlinked])
    db.flush()
    db.add_all(
        [
            RiskControlLink(
                risk_id=severe.id, framework_slug="A", control_item_id=graph.logging.id
            ),
            RiskControlLink(
                risk_id=severe.id, framework_slug="A", control_item_id=graph.change.id
            ),
            RiskControlLink(
                risk_id=unrated.id, framework_slug="A", control_item_id=graph.change.id
            ),
            RiskControlLink(
                risk_id=unlinked.id,
                framework_slug="B",
                control_item_id=graph.inherited.id,
            ),
        ]
    )
    db.commit()

    client = graph.keen.client(graph.keen.user("risk.read"))
    payload = client.get("/v1/graph/risk_control", params={"framework": "A"}).json()
    assert payload["graph_kind"] == "risk_control_relationship"
    nodes = _nodes(payload)
    severe_id = f"risk:{severe.id}"
    unrated_id = f"risk:{unrated.id}"
    logging = f"control:{graph.logging.id}"
    change = f"control:{graph.change.id}"
    assert set(nodes) == {severe_id, unrated_id, logging, change}
    assert nodes[severe_id]["label"] == f"Payroll — {HOSTILE}"
    assert nodes[unrated_id]["label"] == "Payroll"
    assert (
        nodes[severe_id]["risk_score"],
        nodes[severe_id]["residual_risk_score"],
    ) == (20, 4)
    assert nodes[severe_id]["category"] == "Data"
    assert nodes[severe_id]["subcategory"] == "Databases"
    assert (nodes[severe_id]["link_count"], nodes[change]["link_count"]) == (2, 2)
    assert nodes[severe_id]["color"] == nodes[severe_id]["color"].lower()
    # Edges carry the risk score; unrated risks still draw with weight one.
    assert _links(payload) == {
        (severe_id, logging): 20,
        (severe_id, change): 20,
        (unrated_id, change): 1,
    }
    other = client.get("/v1/graph/risk_control", params={"framework": "B"}).json()
    assert set(_nodes(other)) == {
        f"risk:{unlinked.id}",
        f"control:{graph.inherited.id}",
    }
