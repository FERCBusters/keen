"""Bounded event-page inheritance with dense, overlapping cross-framework links."""

import uuid

import pytest
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    ControlItem,
    CrossFrameworkControlLink,
    EffectiveCrossFrameworkControlLink,
    Event,
    Framework,
    ManagedConfiguration,
    Mapping,
    OsaControlMapping,
    effective_cross_framework_links,
)
from app.services.control_inheritance import evidence_pairs, page_controls
from sqlalchemy import create_engine, select
from sqlalchemy import event as sql_event
from sqlalchemy.orm import Session

from tests.db_helpers import create_sqlite_schema


@pytest.fixture
def catalogue(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "enabled_frameworks", "")
    engine = create_engine("sqlite://")
    create_sqlite_schema(engine)
    with Session(engine) as db:
        controls = []
        for i in range(12):
            slug = f"F{i}"
            db.add(Framework(slug=slug))
            group = [
                ControlItem(framework_slug=slug, ref=f"C{j}", type="control")
                for j in range(8)
            ]
            db.add_all(group)
            controls.append(group)
        db.flush()
        # Multiple common references deliberately create duplicate join paths.
        for group in controls:
            for control in group:
                db.add_all(
                    [
                        OsaControlMapping(control_id=control.id, nist_ref=ref)
                        for ref in ["AC-1", "AC-2", "AC-3"]
                    ]
                )
        events = [
            Event(
                source="test",
                external_id=str(i),
                summary="Test",
                timestamp=utc_now_naive(),
            )
            for i in range(60)
        ]
        db.add_all(events)
        selection = ManagedConfiguration(
            name="organisation-frameworks",
            version=1,
            document={"enabled": [f"F{i}" for i in range(12)], "default": "F0"},
        )
        db.add(selection)
        db.flush()
        for evt in events[:-1]:
            for group in controls:
                db.add(
                    Mapping(event_id=evt.id, control_item_id=group[0].id, method="test")
                )
        db.add(
            CrossFrameworkControlLink(
                source_control_id=controls[1][0].id,
                target_control_id=controls[0][1].id,
                rationale="Reviewed",
            )
        )
        db.commit()
        yield db, controls, events, selection
    engine.dispose()


def test_dense_page_deduplicates_and_preserves_direct_evidence(catalogue):
    db, controls, events, _ = catalogue
    ids = [e.id for e in events[:50]]
    result = page_controls(db, "F0", ids)
    expected = {str(c.id) for c in controls[0]}
    assert set(result) == set(ids)
    for items in result.values():
        assert len(items) == 8
        assert {item["id"] for item in items} == expected
        direct = next(item for item in items if item["id"] == str(controls[0][0].id))
        assert "inherited_from_control_id" not in direct
    pairs = evidence_pairs("F0", db, ids)
    assert {(str(cid), eid) for cid, eid in db.execute(select(pairs))} == {
        (cid, eid) for cid in expected for eid in ids
    }
    assert page_controls(db, "F0", [events[-1].id]) == {}
    assert page_controls(db, "F0", []) == {}


def test_disabled_sources_and_target_do_not_inherit(catalogue):
    db, controls, events, selection = catalogue
    selection.document = {"enabled": ["F0"], "default": "F0"}
    db.flush()
    result = page_controls(db, "F0", [events[0].id])
    assert [x["id"] for x in result[events[0].id]] == [str(controls[0][0].id)]
    selection.document = {"enabled": ["F1"], "default": "F1"}
    db.flush()
    assert page_controls(db, "F0", [events[0].id]) == result


def test_scoped_links_match_catalogue_and_keep_reviewed_precedence(catalogue):
    db, controls, _, _ = catalogue
    ids = [controls[1][0].id, controls[2][0].id]
    scoped = effective_cross_framework_links(
        source_ids=ids, target_framework="F0", enabled={"F0", "F1", "F2"}
    )
    rows = db.execute(select(scoped)).all()
    assert len(rows) == 16
    reviewed = next(
        row
        for row in rows
        if row.source_control_id == ids[0]
        and row.target_control_id == controls[0][1].id
    )
    assert reviewed.rationale == "Reviewed" and not reviewed.derived
    global_pairs = (
        db.query(EffectiveCrossFrameworkControlLink)
        .filter(
            EffectiveCrossFrameworkControlLink.source_control_id.in_(ids),
            EffectiveCrossFrameworkControlLink.target_control_id.in_(
                [c.id for c in controls[0]]
            ),
        )
        .all()
    )
    assert {(r.source_control_id, r.target_control_id) for r in rows} == {
        (r.source_control_id, r.target_control_id) for r in global_pairs
    }


def test_query_count_does_not_grow_with_page_or_sources(catalogue):
    db, _, events, _ = catalogue
    ids = [e.id for e in events[:50]]
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sql_event.listen(db.bind, "before_cursor_execute", capture)
    try:
        page_controls(db, "F0", ids[:1])
        count = len(statements)
        statements.clear()
        page_controls(db, "F0", ids)
        assert len(statements) <= count
        assert not any("control_items.metadata" in sql for sql in statements)
    finally:
        sql_event.remove(db.bind, "before_cursor_execute", capture)


def test_event_endpoints_keep_labels_and_provenance(catalogue, monkeypatch):
    from app.api.routes import events as routes
    from app.core.config import settings
    from app.db.models import User
    from starlette.requests import Request

    db, controls, events, _ = catalogue
    user = User(id=uuid.uuid4(), username="test", is_active=True)
    monkeypatch.setattr(routes, "require_events_read", lambda db, request: user)
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    page = routes.list_events(request, framework="F0", limit=50, conditions=[], db=db)
    assert page["total"] == 60 and len(page["items"]) == 50
    detail = routes.get_event(request, str(events[0].id), framework="F0", db=db)
    assert len(detail["controls"]) == 8
    reviewed = next(c for c in detail["controls"] if c["id"] == str(controls[0][1].id))
    # A different mapped source may also supply this target; provenance must
    # describe the selected source and never introduce a duplicate badge.
    assert reviewed["method"] == "cross_framework"
    assert reviewed["inherited_from"]["control_id"] in {
        str(g[0].id) for g in controls[1:]
    }
    unmapped = routes.list_events(
        request, framework="F0", unmapped=True, conditions=[], db=db
    )
    assert unmapped["total"] == 1
    assert unmapped["items"][0]["id"] == str(events[-1].id)


def _event_sql(db, predicate):
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sql_event.listen(db.bind, "before_cursor_execute", capture)
    try:
        ids = {row.id for row in db.query(Event.id).filter(predicate)}
    finally:
        sql_event.remove(db.bind, "before_cursor_execute", capture)
    return ids, statements[-1]


def test_control_filter_reads_events_by_resolved_ids(catalogue, monkeypatch):
    """The crosswalk is resolved once; the events query never re-derives it."""
    from app.services import control_inheritance
    from app.services.event_filters import event_predicates, parse_event_filters

    db, controls, events, _ = catalogue
    target = controls[0][0].id
    filters = parse_event_filters(framework="F0", control=str(target))
    (predicate,) = event_predicates(filters, db)
    ids, sql = _event_sql(db, predicate)
    # Every event except the last maps to a crosswalked control of each framework.
    assert ids == {e.id for e in events[:-1]}
    assert "osa_control_mappings" not in sql
    assert "cross_framework_control_links" not in sql
    assert "mappings" not in sql

    # Common evidence is read through the mappings of the resolved controls.
    monkeypatch.setattr(control_inheritance, "LISTED_MAPPINGS", 10)
    (predicate,) = event_predicates(filters, db)
    ids, sql = _event_sql(db, predicate)
    assert ids == {e.id for e in events[:-1]}
    assert "mappings" in sql and "osa_control_mappings" not in sql

    # A control without evidence or crosswalk links matches nothing.
    lonely = ControlItem(framework_slug="F0", ref="lonely", type="control")
    db.add(lonely)
    db.flush()
    empty = parse_event_filters(framework="F0", control=str(lonely.id))
    (predicate,) = event_predicates(empty, db)
    assert db.query(Event).filter(predicate).count() == 0


def test_clause_filters_share_one_bounded_predicate(catalogue, monkeypatch):
    """Chosen clauses resolve to one list of events, whatever their number."""
    from app.db.models import ControlClauseLink, FrameworkClause
    from app.services import control_inheritance
    from app.services.event_filters import event_predicates, parse_event_filters

    db, controls, events, _ = catalogue
    clauses = [FrameworkClause(framework_slug="F0", ref=f"{n}.1") for n in range(1, 6)]
    db.add_all(clauses)
    db.flush()
    for clause, control in zip(clauses, controls[0][1:6]):
        db.add(
            ControlClauseLink(
                clause_id=clause.id,
                control_item_id=control.id,
                applicability="applicable",
            )
        )
    db.flush()
    filters = parse_event_filters(
        framework="F0", clause=",".join(clause.ref for clause in clauses)
    )
    (predicate,) = event_predicates(filters, db)
    ids, sql = _event_sql(db, predicate)
    # The clause controls inherit every event mapped in the other frameworks.
    assert ids == {e.id for e in events[:-1]}
    assert "control_clause_links" not in sql and "mappings" not in sql
    # One list of event ids, never one list per clause.
    assert sql.count("?") <= len(events)

    monkeypatch.setattr(control_inheritance, "LISTED_MAPPINGS", 10)
    (predicate,) = event_predicates(filters, db)
    ids, sql = _event_sql(db, predicate)
    assert ids == {e.id for e in events[:-1]}
    assert sql.count("FROM mappings") == 1

    unknown = parse_event_filters(framework="F0", clause="9.9")
    (predicate,) = event_predicates(unknown, db)
    assert db.query(Event).filter(predicate).count() == 0


def test_inherited_controls_name_the_rule_and_leave_out_shipped_boilerplate(
    catalogue, monkeypatch
):
    """An inherited control says which control the event was mapped to, and by which rule."""
    from app.api.routes import events as routes
    from app.core.config import settings
    from app.db.models import User
    from app.services.control_inheritance import STARTER_SAME_TOPIC_RATIONALE
    from starlette.requests import Request

    db, controls, events, _ = catalogue
    event = events[-1]
    source, same_topic, reviewed = controls[2][3], controls[0][3], controls[0][4]
    db.add(
        Mapping(
            event_id=event.id,
            control_item_id=source.id,
            method="rule",
            mapped_by="system",
            rationale="auto by rule backups (F2)",
        )
    )
    db.add_all(
        [
            CrossFrameworkControlLink(
                source_control_id=source.id,
                target_control_id=same_topic.id,
                rationale=STARTER_SAME_TOPIC_RATIONALE,
            ),
            CrossFrameworkControlLink(
                source_control_id=source.id,
                target_control_id=reviewed.id,
                rationale="Partial: restore tests only",
            ),
        ]
    )
    db.commit()
    user = User(id=uuid.uuid4(), username="test", is_active=True)
    monkeypatch.setattr(routes, "require_events_read", lambda db, request: user)
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    detail = routes.get_event(request, str(event.id), framework="F0", db=db)
    rows = {row["id"]: row for row in detail["controls"]}
    shipped, partial = rows[str(same_topic.id)], rows[str(reviewed.id)]
    assert shipped["inherited_from"] == {
        "framework": "F2",
        "ref": "C3",
        "title": source.title,
        "control_id": str(source.id),
        "rule_id": "backups",
    }
    # The shipped same-topic text adds nothing to the page's own explanation.
    assert shipped["rationale"] == ""
    assert partial["rationale"] == "Partial: restore tests only"
    # The first event's mappings were made without a rule, so none is named.
    other = routes.get_event(request, str(events[0].id), framework="F0", db=db)
    inherited = [row for row in other["controls"] if row.get("inherited_from")]
    assert inherited and all(
        row["inherited_from"]["rule_id"] is None for row in inherited
    )
