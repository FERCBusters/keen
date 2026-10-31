"""Home overview event counters at production scale.

The dashboard counters are recomputed whenever the brief Valkey mirror has
expired, so the request path must stay cheap on hundreds of thousands of
evidence events. Small fixtures pin the counters' exact semantics; a synthetic
250k-event SQLite database pins the cost of a cold (uncached) overview.
SQLite timings here are indicative: the equivalent PostgreSQL aggregates were
measured separately at 250k events.
"""

import time
import uuid
from datetime import datetime

import pytest
from app.core.config import settings
from app.db.models import (
    ControlEvidenceStats,
    ControlItem,
    CrossFrameworkControlLink,
    Event,
    Framework,
    ManagedConfiguration,
    Mapping,
    User,
)
from app.services import control_evidence_stats as ces
from app.services import home as home_service
from app.services import stats as stats_service
from app.services.context import ChangeContext
from sqlalchemy import create_engine, func, insert, select
from sqlalchemy import event as sql_event
from sqlalchemy.orm import Session

from tests.db_helpers import create_sqlite_schema

FW = "home-scale"
OTHER = "home-other"
TS = datetime(2026, 1, 1)


@pytest.fixture
def cache_off(monkeypatch):
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    monkeypatch.setattr(settings, "enabled_frameworks", "")


@pytest.fixture
def exact_db(contract_db, cache_off):
    """A small dataset whose counters are known by construction.

    Runs on in-memory SQLite locally and against migrated PostgreSQL when the
    contract database is supplied, so the counters are verified with the real
    trigger-maintained coverage table as well.
    """
    db = contract_db
    db.add_all([Framework(slug=FW), Framework(slug=OTHER)])
    db.add(
        ManagedConfiguration(
            name="organisation-frameworks",
            version=1,
            document={"enabled": [FW, OTHER], "default": FW},
        )
    )
    controls = {
        ref: ControlItem(framework_slug=FW, ref=ref, type="annex_control")
        for ref in ("A.1", "A.2", "A.3")
    }
    out_of_scope = ControlItem(
        framework_slug=FW, ref="B.1", type="annex_control", in_scope=False
    )
    other_control = ControlItem(framework_slug=OTHER, ref="X.1", type="control")
    db.add_all([*controls.values(), out_of_scope, other_control])
    db.flush()
    events = [
        Event(
            source="loki",
            external_id=f"e{n}",
            summary=f"evidence {n}",
            timestamp=TS,
        )
        for n in range(1, 7)
    ]
    db.add_all(events)
    db.flush()
    # e1, e2 and e4 map into the framework; e2 maps to two controls, so the
    # distinct event count must deduplicate them. e3 only reaches the other
    # framework; e5 and e6 stay unmapped.
    db.add_all(
        [
            Mapping(
                event_id=events[0].id, control_item_id=controls["A.1"].id, method="rule"
            ),
            Mapping(
                event_id=events[1].id, control_item_id=controls["A.1"].id, method="rule"
            ),
            Mapping(
                event_id=events[1].id,
                control_item_id=controls["A.2"].id,
                method="manual",
            ),
            Mapping(
                event_id=events[2].id, control_item_id=other_control.id, method="rule"
            ),
            Mapping(
                event_id=events[3].id, control_item_id=out_of_scope.id, method="rule"
            ),
        ]
    )
    if db.get_bind().dialect.name != "postgresql":
        # On PostgreSQL the mappings above maintain the coverage table through
        # its triggers; SQLite has no triggers, so the same aggregate rows are
        # inserted directly to exercise the read path.
        db.add_all(
            [
                ControlEvidenceStats(
                    control_item_id=controls["A.1"].id, evidence_count=2
                ),
                ControlEvidenceStats(
                    control_item_id=controls["A.2"].id, evidence_count=1
                ),
                ControlEvidenceStats(control_item_id=out_of_scope.id, evidence_count=1),
            ]
        )
    user = User(
        id=uuid.uuid4(),
        username="reader",
        password_hash="unused",
        role="admin",
        is_active=True,
    )
    db.add(user)
    db.commit()
    yield db


def overview(db, user, framework=FW):
    return home_service.home_overview(
        context=ChangeContext(method="GET", path="/v1/home", actor=user),
        framework=framework,
        db=db,
        user=user,
    )


def test_counters_deduplicate_events_and_scope_coverage(exact_db):
    db = exact_db
    user = db.query(User).one()
    result = overview(db, user)

    assert result["events"] == {"total": 6, "mapped": 3, "unmapped": 3}
    assert result["coverage"] == {
        "in_scope": 3,
        "with_evidence": 2,
        "without_evidence": 1,
    }
    assert result["control_count"] == 4
    gaps = [item for item in result["attention"] if item["kind"] == "gap"]
    assert [item["label"] for item in gaps] == ["A.3 None"]

    # The other framework counts its own mappings against the shared universe.
    assert ces.get_framework_event_counts(db, OTHER) == {
        "total_events": 6,
        "mapped_events": 1,
        "unmapped_events": 5,
    }


def test_framework_event_counts_accept_a_known_total(exact_db):
    db = exact_db
    counts = ces._compute_framework_event_counts(db, FW, total_events=99)
    assert counts == {"total_events": 99, "mapped_events": 3, "unmapped_events": 96}


def test_home_overview_reads_inheritance_and_coverage_once_per_request(exact_db):
    db = exact_db
    user = db.query(User).one()
    calls = {"inherited": 0, "stats": 0}
    real_inherited = home_service.has_inherited_evidence
    real_stats = home_service.get_control_evidence_stats_by_id

    def counted_inherited(*args, **kwargs):
        calls["inherited"] += 1
        return real_inherited(*args, **kwargs)

    def counted_stats(*args, **kwargs):
        calls["stats"] += 1
        return real_stats(*args, **kwargs)

    stats_inherited = stats_service.has_inherited_evidence
    stats_reads = {"count": 0}

    def counted_stats_inherited(*args, **kwargs):
        stats_reads["count"] += 1
        return stats_inherited(*args, **kwargs)

    home_service.has_inherited_evidence = counted_inherited
    home_service.get_control_evidence_stats_by_id = counted_stats
    stats_service.has_inherited_evidence = counted_stats_inherited
    try:
        result = overview(db, user)
    finally:
        home_service.has_inherited_evidence = real_inherited
        home_service.get_control_evidence_stats_by_id = real_stats
        stats_service.has_inherited_evidence = stats_inherited

    assert result["events"] == {"total": 6, "mapped": 3, "unmapped": 3}
    # The summary reuses the coverage reads the overview already made.
    assert calls == {"inherited": 1, "stats": 1}
    assert stats_reads == {"count": 0}


SCALE_EVENTS = 250_000
SCALE_CONTROLS = 100


@pytest.fixture(scope="module")
def scale_db():
    """A synthetic 250k-event database, one mapping per event plus overmaps.

    Seeded with Core bulk inserts so the suite pays seconds, not minutes, for
    the production-scale shape. SQLite timings are indicative, not PostgreSQL.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    create_sqlite_schema(engine)
    with Session(engine) as db:
        db.add(Framework(slug=FW))
        db.add(
            ManagedConfiguration(
                name="organisation-frameworks",
                version=1,
                document={"enabled": [FW], "default": FW},
            )
        )
        controls = [
            ControlItem(
                id=uuid.uuid4(),
                framework_slug=FW,
                ref=f"A.{n}",
                type="annex_control",
            )
            for n in range(SCALE_CONTROLS)
        ]
        db.add_all(controls)
        db.flush()
        control_ids = [control.id for control in controls]
        events = [
            {
                "id": uuid.uuid4(),
                "source": "loki",
                "external_id": f"e{n}",
                "timestamp": TS,
                "summary": f"evidence {n}",
            }
            for n in range(SCALE_EVENTS)
        ]
        # The last 5,000 events stay unmapped.
        mapped_count = SCALE_EVENTS - 5_000
        mappings = [
            {
                "event_id": events[n]["id"],
                "control_item_id": control_ids[n % SCALE_CONTROLS],
                "confidence": 1.0,
                "method": "rule",
            }
            for n in range(mapped_count)
        ]
        # Every twentieth mapped event also reaches a second control, so the
        # distinct-event counters must deduplicate 12,000 extra mapping rows.
        extra = [
            {
                "event_id": events[n]["id"],
                "control_item_id": control_ids[(n + 1) % SCALE_CONTROLS],
                "confidence": 1.0,
                "method": "manual",
            }
            for n in range(0, mapped_count, 20)
        ]
        db.execute(insert(Event), events)
        db.execute(insert(Mapping), mappings)
        db.execute(insert(Mapping), extra)
        # The trigger-maintained per-control coverage table, recomputed from
        # the seeded mappings exactly as the PostgreSQL triggers would.
        per_control = (
            select(
                Mapping.control_item_id,
                func.count(func.distinct(Mapping.event_id)),
            )
            .group_by(Mapping.control_item_id)
            .subquery()
        )
        db.execute(
            insert(ControlEvidenceStats).from_select(
                ["control_item_id", "evidence_count"],
                select(
                    per_control.c.control_item_id,
                    per_control.c[1],
                ),
            )
        )
        db.add(
            User(
                id=uuid.uuid4(),
                username="reader",
                password_hash="unused",
                role="admin",
                is_active=True,
            )
        )
        db.commit()
        yield db
    engine.dispose()


def test_scale_overview_counters_and_budget(scale_db, cache_off):
    db = scale_db
    user = db.query(User).one()
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sql_event.listen(db.bind, "before_cursor_execute", capture)
    start = time.perf_counter()
    result = overview(db, user)
    elapsed = time.perf_counter() - start
    sql_event.remove(db.bind, "before_cursor_execute", capture)

    overmaps = (SCALE_EVENTS - 5_000) // 20
    assert result["events"] == {
        "total": SCALE_EVENTS,
        "mapped": SCALE_EVENTS - 5_000,
        "unmapped": 5_000,
    }
    assert db.query(func.count(Mapping.id)).scalar() == SCALE_EVENTS - 5_000 + overmaps
    assert result["coverage"] == {
        "in_scope": SCALE_CONTROLS,
        "with_evidence": SCALE_CONTROLS,
        "without_evidence": 0,
    }
    # A cold (uncached) overview stays well inside a second at this scale:
    # measured ~0.4s on in-memory SQLite at 250k events; the generous budget
    # only fails when the request path regresses to unbounded aggregation.
    assert elapsed < 2.5, f"home overview took {elapsed:.2f}s at {SCALE_EVENTS} events"
    assert len(statements) <= 26, [sql[:120] for sql in statements]


TARGET, SOURCE = "home-target", "home-source"
INHERITED_EVENTS = 150_000
LINKED = 25


@pytest.fixture(scope="module")
def inherited_scale_db():
    """150k events whose evidence a target framework inherits by crosswalk.

    Every mapped event reaches one of 25 source controls, each linked to three
    target controls, and every tenth also maps directly to one of ten target
    controls. Counting by (target, event) pairs would expand the evidence more
    than threefold before deduplicating it.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    create_sqlite_schema(engine)
    with Session(engine) as db:
        db.add_all([Framework(slug=TARGET), Framework(slug=SOURCE)])
        db.add(
            ManagedConfiguration(
                name="organisation-frameworks",
                version=1,
                document={"enabled": [TARGET, SOURCE], "default": TARGET},
            )
        )
        targets = [
            ControlItem(
                id=uuid.uuid4(), framework_slug=TARGET, ref=f"T.{n}", type="control"
            )
            for n in range(SCALE_CONTROLS)
        ]
        sources = [
            ControlItem(
                id=uuid.uuid4(), framework_slug=SOURCE, ref=f"S.{n}", type="control"
            )
            for n in range(SCALE_CONTROLS)
        ]
        db.add_all([*targets, *sources])
        db.flush()
        db.add_all(
            CrossFrameworkControlLink(
                source_control_id=sources[n].id,
                target_control_id=targets[n + LINKED * hop].id,
            )
            for n in range(LINKED)
            for hop in range(3)
        )
        events = [
            {
                "id": uuid.uuid4(),
                "source": "loki",
                "external_id": f"i{n}",
                "timestamp": TS,
                "summary": f"inherited evidence {n}",
            }
            for n in range(INHERITED_EVENTS)
        ]
        mapped_count = INHERITED_EVENTS - 5_000
        mappings = [
            {
                "event_id": events[n]["id"],
                "control_item_id": sources[n % LINKED].id,
                "confidence": 1.0,
                "method": "rule",
            }
            for n in range(mapped_count)
        ] + [
            {
                "event_id": events[n]["id"],
                "control_item_id": targets[n % 10].id,
                "confidence": 1.0,
                "method": "manual",
            }
            for n in range(0, mapped_count, 10)
        ]
        db.execute(insert(Event), events)
        db.execute(insert(Mapping), mappings)
        db.add(
            User(
                id=uuid.uuid4(),
                username="reader",
                password_hash="unused",
                role="admin",
                is_active=True,
            )
        )
        db.commit()
        yield db
    engine.dispose()


def test_scale_inherited_overview_and_summary_stay_per_control(
    inherited_scale_db, cache_off
):
    db = inherited_scale_db
    user = db.query(User).one()
    start = time.perf_counter()
    result = overview(db, user, framework=TARGET)
    overview_elapsed = time.perf_counter() - start
    start = time.perf_counter()
    summary = stats_service.stats_summary(
        context=ChangeContext(method="GET", path="/v1/stats/summary", actor=user),
        framework=TARGET,
        db=db,
    )
    summary_elapsed = time.perf_counter() - start

    events = {
        "total": INHERITED_EVENTS,
        "mapped": INHERITED_EVENTS - 5_000,
        "unmapped": 5_000,
    }
    assert result["events"] == events
    assert summary["events"] == events
    assert result["coverage"] == {
        "in_scope": SCALE_CONTROLS,
        "with_evidence": 3 * LINKED,
        "without_evidence": SCALE_CONTROLS - 3 * LINKED,
    }
    assert summary["controls"]["with_evidence_all"] == 3 * LINKED
    # Each counter resolves controls first and semi-joins events once:
    # measured ~0.5s on in-memory SQLite. Expanding (target, event) pairs
    # first took several times longer.
    assert overview_elapsed < 2.0, f"overview took {overview_elapsed:.2f}s"
    assert summary_elapsed < 2.0, f"summary took {summary_elapsed:.2f}s"
