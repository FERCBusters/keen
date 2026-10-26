"""Control evidence statistics: the durable table, its repair and its brief mirror.

Database triggers keep per-control evidence counts as mappings change. The
repair hook recomputes them from mappings and events, and Valkey mirrors them
briefly for dashboards. A missing or unreadable mirror falls back to the table.
"""

import importlib.util
import json
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import fakeredis
import pytest
from app.core.config import settings
from app.db.models import ControlItem, Event, Framework, Mapping
from app.services import control_evidence_stats as stats
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

FW = "fw-stats"


@pytest.fixture
def evidence(contract_db, monkeypatch):
    if contract_db.get_bind().dialect.name != "postgresql":
        pytest.skip("Evidence statistics are maintained by PostgreSQL triggers")
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(stats, "get_valkey", lambda: redis)
    monkeypatch.setattr("app.core.cache.get_valkey", lambda: redis)
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 60)
    db = contract_db
    db.add(Framework(slug=FW))
    controls = {
        ref: ControlItem(framework_slug=FW, ref=ref, type="annex_control")
        for ref in ("A.1", "A.2", "A.3")
    }
    db.add_all(controls.values())
    events = [
        Event(
            source="ci",
            summary=f"Deploy {day}",
            external_id=uuid.uuid4().hex,
            timestamp=datetime(2026, 3, day),
        )
        for day in (1, 5)
    ]
    db.add_all(events)
    db.flush()
    for control, event in (("A.1", 0), ("A.1", 1), ("A.2", 1)):
        db.add(
            Mapping(
                event_id=events[event].id,
                control_item_id=controls[control].id,
                method="rule",
            )
        )
    db.commit()

    def table():
        return {
            ref: (row["evidence_count"], row["last_evidence"])
            for ref, row in (
                (controls_by_id[cid], row)
                for cid, row in stats.get_control_evidence_stats_by_id(db, FW).items()
            )
        }

    controls_by_id = {c.id: c.ref for c in controls.values()}
    return SimpleNamespace(
        db=db, redis=redis, controls=controls, events=events, table=table
    )


EXPECTED = {"A.1": (2, "2026-03-05T00:00:00"), "A.2": (1, "2026-03-05T00:00:00")}


def test_triggers_keep_counts_and_the_repair_hook_restores_drifted_rows(evidence):
    assert evidence.table() == EXPECTED

    evidence.db.execute(text("UPDATE control_evidence_stats SET evidence_count = 99"))
    evidence.db.execute(
        text("DELETE FROM control_evidence_stats WHERE control_item_id = :id"),
        {"id": evidence.controls["A.2"].id},
    )
    stats.clear_stats_caches()

    rebuilt = stats.rebuild_control_evidence_stats(evidence.db)

    assert rebuilt == 2
    assert evidence.table() == EXPECTED


def test_the_maintenance_script_rebuilds_and_reports(evidence, monkeypatch, capsys):
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts/rebuild_control_evidence_stats.py"
    )
    spec = importlib.util.spec_from_file_location("rebuild_stats_script", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setattr(
        script,
        "SessionLocal",
        sessionmaker(
            bind=evidence.db.get_bind(), join_transaction_mode="create_savepoint"
        ),
    )
    evidence.db.execute(text("UPDATE control_evidence_stats SET evidence_count = 0"))
    evidence.db.commit()

    script.main()

    report = capsys.readouterr().out
    assert "'rebuilt_control_evidence_stats': 2" in report
    evidence.db.expire_all()
    stats.clear_stats_caches()
    assert evidence.table() == EXPECTED


def test_the_mirror_serves_repeat_reads_until_it_is_cleared(evidence):
    key = stats.control_evidence_stats_cache_key(FW)
    assert evidence.table() == EXPECTED
    assert json.loads(evidence.redis.hget(key, str(evidence.controls["A.1"].id))) == {
        "evidence_count": 2,
        "last_evidence": "2026-03-05T00:00:00",
    }

    # A change reaches readers once the framework's mirror is cleared.
    evidence.db.add(
        Mapping(
            event_id=evidence.events[0].id,
            control_item_id=evidence.controls["A.3"].id,
            method="manual",
        )
    )
    evidence.db.commit()
    assert "A.3" not in evidence.table()
    assert stats.clear_control_evidence_stats_cache(FW) == 1
    assert evidence.table()["A.3"] == (1, "2026-03-01T00:00:00")


def test_an_unreadable_mirror_falls_back_to_the_table(evidence):
    key = stats.control_evidence_stats_cache_key(FW)
    evidence.redis.hset(key, mapping={str(evidence.controls["A.1"].id): "{not json"})

    assert evidence.table() == EXPECTED
    # The rebuilt mirror replaces the unreadable one.
    assert (
        json.loads(evidence.redis.hget(key, str(evidence.controls["A.1"].id)))[
            "evidence_count"
        ]
        == 2
    )


def test_a_framework_without_evidence_is_mirrored_as_empty(evidence):
    evidence.db.add(Framework(slug="fw-empty"))
    evidence.db.commit()

    assert stats.get_control_evidence_stats_by_id(evidence.db, "fw-empty") == {}
    key = stats.control_evidence_stats_cache_key("fw-empty")
    assert evidence.redis.hgetall(key) == {"__empty__": "1"}
    assert stats.get_control_evidence_stats_by_id(evidence.db, "fw-empty") == {}


def test_clearing_every_mirror_works_in_batches(evidence):
    for n in range(501):
        evidence.redis.hset(f"keen:stats:v1:control_evidence:fw-{n}", "x", "1")

    deleted = stats.clear_stats_caches()

    assert deleted["control_evidence_stats_cache_keys_deleted"] == 501
    assert evidence.redis.keys("keen:stats:v1:control_evidence:*") == []


def test_statistics_work_while_valkey_is_unavailable(evidence, monkeypatch):
    def unavailable():
        raise ConnectionError("Valkey is down")

    monkeypatch.setattr(stats, "get_valkey", unavailable)
    monkeypatch.setattr("app.core.cache.get_valkey", unavailable)

    assert evidence.table() == EXPECTED
    assert stats.clear_control_evidence_stats_cache() == 0
    assert stats.get_framework_event_counts(evidence.db, FW) == {
        "total_events": 2,
        "mapped_events": 2,
        "unmapped_events": 0,
    }


def test_framework_event_counts_recover_from_an_unreadable_cache(evidence):
    counts = stats.refresh_framework_event_counts_cache(evidence.db, FW)
    assert counts == {FW: {"total_events": 2, "mapped_events": 2, "unmapped_events": 0}}
    from app.core.cache import make_cache_key

    key = make_cache_key(
        "stats:framework-event-counts",
        {"framework": FW, "counter_version": "celery-count-v1"},
    )
    evidence.redis.set(key, json.dumps({"total_events": "many"}))

    assert stats.get_framework_event_counts(evidence.db, FW)["total_events"] == 2
    assert json.loads(evidence.redis.get(key))["total_events"] == 2


def test_the_periodic_refresh_covers_enabled_frameworks_and_counts_others_on_demand(
    evidence, monkeypatch
):
    from app.db.models import ManagedConfiguration

    db = evidence.db
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    db.add(Framework(slug="fw-unused"))
    unused = ControlItem(framework_slug="fw-unused", ref="U.1", type="control")
    db.add(unused)
    db.flush()
    db.add(
        Mapping(
            event_id=evidence.events[0].id, control_item_id=unused.id, method="rule"
        )
    )
    db.add(
        ManagedConfiguration(
            name="organisation-frameworks",
            version=1,
            document={"enabled": [FW], "default": FW},
        )
    )
    db.commit()

    refreshed = stats.refresh_framework_event_counts_cache(db)
    assert set(refreshed) == {FW, settings.default_framework_slug}
    assert refreshed[FW]["mapped_events"] == 2

    # A framework outside the selection is counted on its first request.
    assert stats.get_framework_event_counts(db, "fw-unused") == {
        "total_events": 2,
        "mapped_events": 1,
        "unmapped_events": 1,
    }
