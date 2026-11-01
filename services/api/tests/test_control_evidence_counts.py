"""Per-control evidence counts with one-hop crosswalk inheritance.

``evidence_pairs`` defines the inherited relation. The control statistics,
the Events page control facet and the control set used by event counters
resolve the short list of (control, source) pairs first and count from it,
or, when every event counts, from PostgreSQL's trigger-maintained per-control
counters. These tests compare each with the relation on a crosswalk with
explicit and shared-NIST links, overlapping sources, a link from a framework
that is not enabled, a link inside the framework, varied event times and a
diary entry only risk readers may open.
"""

import uuid
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import (
    ControlItem,
    CrossFrameworkControlLink,
    Event,
    Framework,
    ManagedConfiguration,
    Mapping,
    OsaControlMapping,
    Permission,
    User,
)
from app.security.diary_visibility import diary_filter_condition
from app.services import events as events_service
from app.services import stats as stats_service
from app.services.context import ChangeContext
from app.services.control_inheritance import (
    control_event_counts,
    effective_framework_ids,
    evidence_pairs,
    framework_evidence_sources,
    osa_pairs,
    recorded_control_evidence,
)
from app.services.event_filters import EventFilters
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

TGT, SRC, ALSO, OFF = "count-target", "count-source", "count-also", "count-off"


@pytest.fixture
def crosswalk(contract_db, monkeypatch):
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    db = contract_db
    db.add_all([Framework(slug=slug) for slug in (TGT, SRC, ALSO, OFF)])
    selection = ManagedConfiguration(
        name="organisation-frameworks",
        version=1,
        document={"enabled": [TGT, SRC, ALSO], "default": TGT},
    )
    db.add(selection)

    def control(framework, ref):
        row = ControlItem(framework_slug=framework, ref=ref, type="control")
        db.add(row)
        return row

    t = {ref: control(TGT, ref) for ref in ("T1", "T2", "T3", "T4", "T5", "T6")}
    s = {ref: control(SRC, ref) for ref in ("S1", "S2", "S3")}
    also = control(ALSO, "A1")
    off = control(OFF, "O1")
    db.flush()

    def link(source, target):
        db.add(
            CrossFrameworkControlLink(
                source_control_id=source.id, target_control_id=target.id
            )
        )

    link(s["S1"], t["T1"])  # T1: itself, S1 and S2
    link(s["S2"], t["T1"])
    link(s["S1"], t["T2"])  # T2: itself and S1
    link(t["T6"], t["T5"])  # T5: a link inside the framework
    link(off, t["T4"])  # T4: from a framework that is not enabled
    # T3 shares a NIST control with S3 and A1; A1 also with T2.
    for row, ref in (
        (t["T3"], "AC-1"),
        (s["S3"], "AC-1"),
        (also, "AC-1"),
        (t["T2"], "AC-2"),
        (also, "AC-2"),
        (off, "AC-2"),
    ):
        db.add(OsaControlMapping(control_id=row.id, nist_ref=ref))

    def event(name, day, *controls, **columns):
        row = Event(
            source=columns.pop("source", "ci"),
            summary=name,
            external_id=columns.pop("external_id", name),
            timestamp=datetime(2026, 3, day, 9, 0),
            **columns,
        )
        db.add(row)
        db.flush()
        for item in controls:
            db.add(Mapping(event_id=row.id, control_item_id=item.id, method="rule"))
        return row

    event("target-and-sources", 1, t["T1"], s["S1"], s["S2"])
    event("first-source", 2, s["S1"])
    event("first-source-again", 3, s["S1"], source="build")
    event("second-source", 4, s["S2"], source="build")
    event("direct", 5, t["T2"])
    event("shared-nist", 6, s["S3"], also)
    event("also", 7, also, source="build")
    event("off", 8, off)
    event("inside", 9, t["T6"])
    event(
        "risk-entry",
        10,
        s["S2"],
        t["T3"],
        source="diary",
        system="isms",
        external_id=f"isms-link:risk:{uuid.uuid4()}",
    )
    event("unmapped", 11)

    def user(name, *codes, role="normal"):
        row = User(
            id=uuid.uuid4(),
            username=f"{name}-{uuid.uuid4().hex[:8]}",
            password_hash="unused",
            role=role,
            is_active=True,
        )
        db.add(row)
        for code in codes:
            permission = db.query(Permission).filter_by(code=code).one_or_none()
            if permission is None:
                permission = Permission(code=code, description=code)
                db.add(permission)
            row.permissions.append(permission)
        return row

    admin = user("admin", role="admin")
    reader = user("reader", "events.read")
    db.commit()
    return SimpleNamespace(
        db=db,
        t=t,
        s=s,
        also=also,
        off=off,
        selection=selection,
        admin=admin,
        reader=reader,
    )


def postgresql(db):
    return db.get_bind().dialect.name == "postgresql"


def reference(db, framework, *conditions):
    """{control: (events, newest)} from evidence_pairs, for the events selected."""
    pairs = evidence_pairs(framework, db)
    rows = db.execute(
        select(
            pairs.c.control_id,
            func.count(pairs.c.event_id),
            func.max(Event.timestamp),
        )
        .join(Event, Event.id == pairs.c.event_id)
        .where(*conditions)
        .group_by(pairs.c.control_id)
    ).all()
    return {control: (int(count), latest) for control, count, latest in rows}


def reference_controls(db, framework):
    """The control set of effective_framework_ids, written as the original union."""
    enabled = select(ControlItem.id).where(
        ControlItem.framework_slug.in_(
            db.get(ManagedConfiguration, "organisation-frameworks").document["enabled"]
        )
    )
    target = aliased(ControlItem)
    direct = select(ControlItem.id).where(ControlItem.framework_slug == framework)
    explicit = (
        select(CrossFrameworkControlLink.source_control_id)
        .join(target, target.id == CrossFrameworkControlLink.target_control_id)
        .where(
            target.framework_slug == framework,
            CrossFrameworkControlLink.source_control_id.in_(enabled),
            CrossFrameworkControlLink.target_control_id.in_(enabled),
        )
    )
    source, membership, sc, tc = osa_pairs()
    osa = (
        select(source.c.control_id)
        .select_from(
            source.join(membership, source.c.nist_ref == membership.c.nist_ref)
            .join(sc, sc.c.id == source.c.control_id)
            .join(tc, tc.c.id == membership.c.control_id)
        )
        .where(
            tc.c.framework_slug == framework,
            sc.c.framework_slug != tc.c.framework_slug,
            sc.c.id.in_(enabled),
            tc.c.id.in_(enabled),
        )
    )
    return set(db.execute(direct.union(explicit, osa)).scalars())


def names(crosswalk, counts):
    labels = {item.id: ref for ref, item in crosswalk.t.items()}
    return {labels[control]: value for control, value in counts.items()}


@pytest.mark.parametrize(
    "enabled",
    [[TGT, SRC, ALSO], [TGT, SRC], [TGT, ALSO], [TGT], [SRC, ALSO]],
)
def test_the_control_set_and_sources_follow_the_selection(crosswalk, enabled):
    db = crosswalk.db
    crosswalk.selection.document = {"enabled": enabled, "default": enabled[0]}
    db.commit()
    for framework in (TGT, SRC, ALSO):
        found = set(db.execute(effective_framework_ids(framework, db)).scalars())
        assert found == reference_controls(db, framework)
        sources = framework_evidence_sources(db, framework)
        assert set().union(*sources.values()) == found
        assert all(target in found for target in sources)


def test_without_a_session_every_link_counts(crosswalk):
    db, t, s = crosswalk.db, crosswalk.t, crosswalk.s
    found = set(db.execute(effective_framework_ids(TGT)).scalars())
    sources = {s["S1"].id, s["S2"].id, s["S3"].id, crosswalk.also.id, crosswalk.off.id}
    assert found == {item.id for item in t.values()} | sources


def test_a_framework_without_controls_has_no_evidence(crosswalk):
    db = crosswalk.db
    assert framework_evidence_sources(db, "count-absent") == {}
    assert control_event_counts(db, "count-absent") == {}
    if postgresql(db):
        assert recorded_control_evidence(db, "count-absent") == {}


def test_sources_name_each_inheriting_control(crosswalk):
    t, s = crosswalk.t, crosswalk.s
    sources = framework_evidence_sources(crosswalk.db, TGT)
    assert sources == {
        t["T1"].id: {t["T1"].id, s["S1"].id, s["S2"].id},
        t["T2"].id: {t["T2"].id, s["S1"].id, crosswalk.also.id},
        t["T3"].id: {t["T3"].id, s["S3"].id, crosswalk.also.id},
        t["T4"].id: {t["T4"].id},
        t["T5"].id: {t["T5"].id, t["T6"].id},
        t["T6"].id: {t["T6"].id},
    }


def test_counts_match_the_relation_for_every_restriction(crosswalk):
    db = crosswalk.db
    window = (
        Event.timestamp >= datetime(2026, 3, 2),
        Event.timestamp < datetime(2026, 3, 7),
    )
    for conditions in (
        (),
        window,
        (Event.source == "build",),
        (diary_filter_condition(db, crosswalk.reader),),
    ):
        expected = reference(db, TGT, *conditions)
        assert (
            control_event_counts(db, TGT, event_conditions=conditions, latest=True)
            == expected
        )
        ids = select(Event.id).where(*conditions) if conditions else None
        assert control_event_counts(db, TGT, event_ids=ids) == {
            control: count for control, (count, _) in expected.items()
        }
    assert names(crosswalk, reference(db, TGT)) == {
        "T1": (5, datetime(2026, 3, 10, 9, 0)),
        "T2": (6, datetime(2026, 3, 7, 9, 0)),
        "T3": (3, datetime(2026, 3, 10, 9, 0)),
        "T5": (1, datetime(2026, 3, 9, 9, 0)),
        "T6": (1, datetime(2026, 3, 9, 9, 0)),
    }


def test_recorded_counters_match_the_relation(crosswalk):
    db = crosswalk.db
    if not postgresql(db):
        pytest.skip("Per-control counters are maintained by PostgreSQL triggers")
    for framework in (TGT, SRC, ALSO):
        assert recorded_control_evidence(db, framework) == reference(db, framework)
    # The figures follow removed evidence too.
    db.query(Mapping).filter(Mapping.control_item_id == crosswalk.also.id).delete()
    db.commit()
    assert recorded_control_evidence(db, TGT) == reference(db, TGT)


def test_control_statistics_count_inherited_evidence_for_each_reader(crosswalk):
    db = crosswalk.db

    def statistics(user, **window):
        body = stats_service.stats_controls(
            context=ChangeContext(method="GET", path="/v1/stats/controls", actor=user),
            framework=TGT,
            db=db,
            **window,
        )
        assert body["stats_source"] == "live_query"
        return {
            item["ref"]: (item["evidence_count"], item["last_evidence"])
            for item in body["items"]
        }

    def expected(*conditions):
        found = names(crosswalk, reference(db, TGT, *conditions))
        return {
            ref: (
                found[ref][0] if ref in found else 0,
                found[ref][1].isoformat() if ref in found else None,
            )
            for ref in crosswalk.t
        }

    reader = diary_filter_condition(db, crosswalk.reader)
    assert statistics(crosswalk.admin) == expected()
    assert statistics(crosswalk.reader) == expected(reader)
    window = {"start_date": date(2026, 3, 2), "end_date": date(2026, 3, 6)}
    assert statistics(crosswalk.admin, **window) == expected(
        Event.timestamp >= datetime(2026, 3, 2), Event.timestamp < datetime(2026, 3, 7)
    )


def test_the_control_facet_counts_the_filtered_events(crosswalk):
    db = crosswalk.db

    def facet(user, **filters):
        body = events_service.event_facets(
            filters=EventFilters(framework=TGT, **filters),
            max_sources=500,
            max_controls=500,
            db=db,
            user=user,
        )
        return body["controls"]

    def expected(*conditions):
        found = names(crosswalk, reference(db, TGT, *conditions))
        rows = sorted(found.items(), key=lambda item: (-item[1][0], item[0]))
        return [
            {"ref": ref, "title": None, "type": "control", "count": count}
            for ref, (count, _) in rows
        ]

    assert facet(crosswalk.admin) == expected()
    assert facet(crosswalk.reader) == expected(
        diary_filter_condition(db, crosswalk.reader)
    )
    assert facet(crosswalk.admin, sources=("build",)) == expected(
        Event.source == "build"
    )
    assert facet(
        crosswalk.admin,
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 6),
    ) == expected(
        Event.timestamp >= datetime(2026, 3, 2),
        Event.timestamp < datetime(2026, 3, 7),
    )
    assert facet(crosswalk.admin, sources=("absent",)) == []
    # A control filter is left out of its own facet.
    assert facet(crosswalk.admin, controls=(str(crosswalk.t["T5"].id),)) == expected()
