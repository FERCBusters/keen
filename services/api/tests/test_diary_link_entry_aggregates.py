"""Hidden framework-link diary entries stay hidden in the views that list them.

Saving an ISMS record or a risk with framework links records an automatic
diary entry. Entries about risks are readable only with risk read or manage
permission, and entries about other ISMS records with ISMS read access. The
event list and event detail already enforce this; these tests hold the other
views and side doors to the same rule: source counters, facets, the
latest-event ticker, exports, graphs, questions and artifact downloads.
Aggregates are cached, so each reader asks after a reader with wider access
has populated the cache. Statistics count every entry, as SECURITY.md
describes, and a test pins that too.
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import Artifact, ControlItem, Event, EventQuestionThread, Mapping

from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401
from tests.test_isms_link_diary import FW, _entry, _risk

CONTROL = "A.5.9"


@pytest.fixture
def entries(keen, monkeypatch):  # noqa: F811 - fixture imported above
    # Aggregate caching stays on, as in production.
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 60)
    db = keen.db
    control = ControlItem(framework_slug=FW, ref=CONTROL, type="annex_control")
    db.add(control)
    db.commit()
    # One ordinary piece of evidence that every reader may see.
    ordinary = Event(
        source="ci",
        system="build",
        action="deploy",
        summary="Deployment approved",
        timestamp=_now(db),
        external_id=uuid.uuid4().hex,
    )
    db.add(ordinary)
    db.flush()
    db.add(
        Mapping(
            event_id=ordinary.id,
            control_item_id=control.id,
            method="manual",
            rationale="Reviewed",
        )
    )
    db.commit()

    isms_manager = keen.client(keen.user("isms.manage"))
    created = isms_manager.post(
        "/v1/isms/documents",
        params={"framework": FW},
        json={"title": "Records policy", "controls": [CONTROL]},
        headers=unsafe(isms_manager),
    )
    assert created.status_code == 200, created.text
    risk = _risk(db)
    db.commit()
    risk_manager = keen.client(keen.user("risk.manage"))
    linked = risk_manager.patch(
        f"/v1/risks/{risk.id}/controls",
        params={"framework": FW},
        json={"controls": [CONTROL]},
        headers=unsafe(risk_manager),
    )
    assert linked.status_code == 200, linked.text

    document_entry = _entry(db, "document", uuid.UUID(created.json()["id"]))
    risk_entry = _entry(db, "risk", risk.id)
    assert document_entry is not None and risk_entry is not None
    return SimpleNamespace(
        db=db,
        control=control,
        ordinary=ordinary,
        document=document_entry,
        risk=risk_entry,
    )


def _now(db):
    from app.core.datetime_utils import utc_now_naive

    return utc_now_naive()


# Widest access first, so narrower readers ask after the cache is populated.
READERS = [
    ("risk reader", ("events.read", "risk.read"), {"document", "risk"}),
    ("ISMS reader", ("events.read", "isms.read"), {"document"}),
    ("evidence reader", ("events.read",), set()),
]


def _readers(keen, entries, *extra):  # noqa: F811 - fixture imported above
    for name, codes, visible in READERS:
        client = keen.client(keen.user(*codes, *extra))
        ids = {getattr(entries, kind).id for kind in visible}
        hidden = {entries.document.id, entries.risk.id} - ids
        yield name, client, ids, hidden


def test_source_counters_and_metadata_count_only_visible_entries(keen, entries):  # noqa: F811 - fixture imported above
    for name, client, visible, _ in _readers(keen, entries):
        listing = client.get("/v1/sources", params={"framework": FW})
        assert listing.status_code == 200, (name, listing.text)
        rows = {item["source"]: item for item in listing.json()["items"]}
        meta = client.get("/v1/sources/meta").json()["items"]
        if visible:
            diary = rows["diary"]
            assert (diary["total_events"], diary["mapped_events"]) == (
                len(visible),
                len(visible),
            ), name
            assert diary["unmapped_events"] == 0, name
            assert "diary" in {item["source"] for item in meta}, name
        else:
            assert "diary" not in rows, name
            assert "diary" not in {item["source"] for item in meta}, name
        assert rows["ci"]["total_events"] == 1, name


def test_event_facets_count_only_visible_entries(keen, entries):  # noqa: F811 - fixture imported above
    for name, client, visible, _ in _readers(keen, entries):
        facets = client.get("/v1/events/facets", params={"framework": FW})
        assert facets.status_code == 200, (name, facets.text)
        sources = {row["source"]: row["count"] for row in facets.json()["sources"]}
        assert sources.get("diary", 0) == len(visible), name
        controls = {row["ref"]: row["count"] for row in facets.json()["controls"]}
        assert controls[CONTROL] == 1 + len(visible), name


def test_latest_events_and_exports_omit_hidden_entries(keen, entries):  # noqa: F811 - fixture imported above
    link_entries = {entries.document.id, entries.risk.id}
    for name, client, visible, _ in _readers(keen, entries):
        latest = client.get("/v1/evidence/latest", params={"limit": 25})
        assert latest.status_code == 200, (name, latest.text)
        listed = {uuid.UUID(item["id"]) for item in latest.json()["events"]}
        assert listed & link_entries == visible, name
        assert entries.ordinary.id in listed, name

        export = client.get(
            "/v1/events/export", params={"format": "json", "source": "diary"}
        )
        assert export.status_code == 200, (name, export.text)
        exported = json.loads(export.text)
        assert {uuid.UUID(item["id"]) for item in exported["items"]} == visible, name
        assert exported["total"] == len(visible), name


def test_statistics_count_every_entry_for_every_evidence_reader(keen, entries):  # noqa: F811 - fixture imported above
    # SECURITY.md: statistics can count entries a reader cannot open. The
    # all-time figures come from the durable aggregates, so every reader
    # receives the same totals. Each reader asks with an empty cache, so the
    # figures are computed for that reader.
    from app.services.control_evidence_stats import clear_stats_caches

    for name, client, _, _ in _readers(keen, entries):
        clear_stats_caches()
        summary = client.get("/v1/stats/summary", params={"framework": FW})
        assert summary.status_code == 200, (name, summary.text)
        assert summary.json()["events"] == {
            "total": 3,
            "mapped": 3,
            "unmapped": 0,
        }, name
        controls = client.get("/v1/stats/controls", params={"framework": FW})
        assert controls.status_code == 200, (name, controls.text)
        (item,) = [i for i in controls.json()["items"] if i["ref"] == CONTROL]
        assert item["evidence_count"] == 3, name


def test_source_graph_counts_only_visible_entries(keen, entries):  # noqa: F811 - fixture imported above
    if not keen.postgres:
        pytest.skip("The graph aggregates run on PostgreSQL only")
    for name, client, visible, _ in _readers(keen, entries):
        graph = client.get("/v1/graph/control_source", params={"framework": FW})
        assert graph.status_code == 200, (name, graph.text)
        nodes = {node["id"]: node for node in graph.json()["nodes"]}
        links = {
            (link["source"], link["target"]): link["value"]
            for link in graph.json()["links"]
        }
        target = f"control:{entries.control.id}"
        if visible:
            assert links[("source:diary", target)] == len(visible), name
        else:
            assert "source:diary" not in nodes, name
        assert nodes[target]["mapped_events"] == 1 + len(visible), name


def test_hidden_entries_accept_no_questions_and_serve_no_artifacts(
    keen,  # noqa: F811 - fixture imported above
    entries,
    monkeypatch,
):
    from app.worker import tasks

    # Record webhook fan-out instead of waiting on a broker.
    fanout = []
    monkeypatch.setattr(
        tasks.send_question_webhooks_task, "delay", lambda *ids: fanout.append(ids)
    )
    db = entries.db
    artifacts = {
        entry_id: [
            row.id for row in db.query(Artifact).filter(Artifact.event_id == entry_id)
        ]
        for entry_id in (entries.document.id, entries.risk.id)
    }
    assert all(artifacts.values()), "link entries record their content as artifacts"

    # Question authors pass the question permission check, so the visibility
    # rule is what decides.
    for name, client, visible, hidden in _readers(keen, entries, "question.create"):
        for entry_id in visible | hidden:
            expected = 200 if entry_id in visible else 404
            questions = client.get(f"/v1/events/{entry_id}/questions")
            assert questions.status_code == expected, (name, questions.text)
            for artifact_id in artifacts[entry_id]:
                download = client.get(f"/v1/artifacts/{artifact_id}/download")
                assert download.status_code == expected, (name, download.text)
            asked = client.post(
                f"/v1/events/{entry_id}/questions",
                data={"body": "Who linked this?"},
                headers=unsafe(client),
            )
            assert asked.status_code == expected, (name, asked.text)
    db.expire_all()
    asked_about = {
        event_id for (event_id,) in db.query(EventQuestionThread.event_id).all()
    }
    assert entries.risk.id in asked_about
    # Only the risk reader could see, and so ask about, the risk entry; the ISMS
    # and risk readers asked about the document entry.
    threads = db.query(EventQuestionThread.id, EventQuestionThread.event_id).all()
    asked = sorted(str(event_id) for _, event_id in threads)
    assert asked == sorted([str(entries.risk.id)] + [str(entries.document.id)] * 2)
    # Webhooks announce only the questions that were accepted.
    assert sorted(thread for thread, _ in fanout) == sorted(
        str(thread_id) for thread_id, _ in threads
    )
