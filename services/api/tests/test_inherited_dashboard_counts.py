"""Dashboard counters for a framework that inherits evidence by crosswalk.

The home overview, the summary statistics and the volume chart count events
and controls through one-hop inheritance without expanding every event over
its inheriting targets. ``evidence_pairs`` defines the inherited relation, so
these tests compare the per-control and semi-join counters with it: explicit
and OSA-derived links, a source in a framework the organisation has not
enabled, an event mapped to both a source and its target, and a diary entry
that only risk readers may open.
"""

import uuid
from datetime import datetime
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
from app.services import home as home_service
from app.services import stats as stats_service
from app.services.context import ChangeContext
from app.services.control_inheritance import (
    evidence_pairs,
    evidenced_control_ids,
    mapped_event_count,
)
from sqlalchemy import func, select

TGT, SRC, OFF = "inherit-target", "inherit-source", "inherit-disabled"
DAY = datetime(2026, 3, 2, 9, 0)


@pytest.fixture
def crosswalk(contract_db, monkeypatch):
    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", 0)
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    db = contract_db
    db.add_all([Framework(slug=slug) for slug in (TGT, SRC, OFF)])
    db.add(
        ManagedConfiguration(
            name="organisation-frameworks",
            version=1,
            document={"enabled": [TGT, SRC], "default": TGT},
        )
    )

    def control(framework, ref, **columns):
        row = ControlItem(framework_slug=framework, ref=ref, type="control", **columns)
        db.add(row)
        return row

    t = {
        "T1": control(TGT, "T1"),  # inherits from S1 by an explicit link
        "T2": control(TGT, "T2"),  # direct evidence only
        "T3": control(TGT, "T3"),  # inherits from S2 through a shared NIST control
        "T4": control(TGT, "T4"),  # no evidence
        "T5": control(TGT, "T5"),  # linked from a framework that is not enabled
        "T6": control(TGT, "T6", in_scope=False),  # evidence only in a risk entry
    }
    s = {ref: control(SRC, ref) for ref in ("S1", "S2", "S3", "S4")}
    off = control(OFF, "O1")
    db.flush()
    db.add_all(
        [
            CrossFrameworkControlLink(
                source_control_id=s["S1"].id, target_control_id=t["T1"].id
            ),
            CrossFrameworkControlLink(
                source_control_id=off.id, target_control_id=t["T5"].id
            ),
            CrossFrameworkControlLink(
                source_control_id=s["S3"].id, target_control_id=t["T6"].id
            ),
            OsaControlMapping(control_id=s["S2"].id, nist_ref="AC-99"),
            OsaControlMapping(control_id=t["T3"].id, nist_ref="AC-99"),
        ]
    )

    def event(name, *controls, **columns):
        row = Event(
            source=columns.pop("source", "ci"),
            summary=name,
            external_id=columns.pop("external_id", name),
            timestamp=DAY,
            **columns,
        )
        db.add(row)
        db.flush()
        for item in controls:
            db.add(Mapping(event_id=row.id, control_item_id=item.id, method="rule"))
        return row

    event("source-and-target", s["S1"], t["T1"])  # counted once
    event("direct", t["T2"])
    event("osa-source", s["S2"])
    event("disabled-source", off)
    event("unlinked-source", s["S4"])
    event(
        "risk-entry",
        s["S3"],
        source="diary",
        system="isms",
        external_id=f"isms-link:risk:{uuid.uuid4()}",
    )
    event("unmapped")

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
    return SimpleNamespace(db=db, t=t, s=s, admin=admin, reader=reader)


def pairs_reference(db, visibility):
    """The distinct events and target controls of evidence_pairs."""
    pairs = evidence_pairs(TGT, db)
    visible = (
        select(pairs.c.event_id, pairs.c.control_id)
        .join(Event, Event.id == pairs.c.event_id)
        .where(visibility if visibility is not None else True)
    ).subquery()
    events = db.execute(select(func.count(func.distinct(visible.c.event_id)))).scalar()
    controls = set(db.execute(select(visible.c.control_id).distinct()).scalars())
    return events, controls


def refs(crosswalk, ids):
    names = {item.id: ref for ref, item in crosswalk.t.items()}
    return {names[i] for i in ids}


@pytest.mark.parametrize(
    "who, mapped, evidenced",
    [
        ("anyone", 4, {"T1", "T2", "T3", "T6"}),
        ("admin", 4, {"T1", "T2", "T3", "T6"}),
        ("reader", 3, {"T1", "T2", "T3"}),
    ],
)
def test_counters_match_the_inherited_evidence_relation(
    crosswalk, who, mapped, evidenced
):
    db = crosswalk.db
    visibility = (
        None if who == "anyone" else diary_filter_condition(db, getattr(crosswalk, who))
    )
    reference_events, reference_controls = pairs_reference(db, visibility)

    assert mapped_event_count(db, TGT, visibility) == reference_events == mapped
    found = evidenced_control_ids(db, TGT, visibility)
    assert found == reference_controls
    assert refs(crosswalk, found) == evidenced


def test_summary_counts_inherited_evidence_for_each_reader(crosswalk):
    db = crosswalk.db
    for user, mapped, with_evidence, in_scope_with_evidence in (
        (crosswalk.admin, 4, 4, 3),
        (crosswalk.reader, 3, 3, 3),
    ):
        summary = stats_service.stats_summary(
            context=ChangeContext(method="GET", path="/v1/stats/summary", actor=user),
            framework=TGT,
            db=db,
        )
        total = 7 if user is crosswalk.admin else 6
        assert summary["stats_source"] == "live_query"
        assert summary["events"] == {
            "total": total,
            "mapped": mapped,
            "unmapped": total - mapped,
        }
        assert summary["controls"] == {
            "total": 6,
            "in_scope": 5,
            "out_of_scope": 1,
            "with_evidence_in_scope": in_scope_with_evidence,
            "without_evidence_in_scope": 5 - in_scope_with_evidence,
            "with_evidence_all": with_evidence,
            "without_evidence_all": 6 - with_evidence,
        }


def test_home_overview_reports_inherited_counters_without_the_full_summary(
    crosswalk, monkeypatch
):
    db = crosswalk.db
    monkeypatch.setattr(
        stats_service,
        "stats_summary",
        lambda **_: pytest.fail("The overview needs only the event counters"),
    )
    result = home_service.home_overview(
        context=ChangeContext(method="GET", path="/v1/home", actor=crosswalk.admin),
        framework=TGT,
        db=db,
        user=crosswalk.admin,
    )
    assert result["events"] == {"total": 7, "mapped": 4, "unmapped": 3}
    # T6 is out of scope, so in-scope coverage is T1, T2 and T3 of five.
    assert result["coverage"] == {
        "in_scope": 5,
        "with_evidence": 3,
        "without_evidence": 2,
    }
    gaps = [item["label"] for item in result["attention"] if item["kind"] == "gap"]
    assert gaps == ["T4 None", "T5 None"]


def test_event_counters_are_withheld_without_events_read(crosswalk):
    outsider = User(
        id=uuid.uuid4(),
        username=f"outsider-{uuid.uuid4().hex[:8]}",
        password_hash="unused",
        role="normal",
        is_active=True,
    )
    crosswalk.db.add(outsider)
    crosswalk.db.commit()
    assert (
        stats_service.stats_event_counts(
            context=ChangeContext(method="GET", path="/v1/home", actor=outsider),
            framework=TGT,
            db=crosswalk.db,
        )
        is None
    )


def test_volume_chart_counts_inherited_events_once(crosswalk):
    db = crosswalk.db
    if db.get_bind().dialect.name != "postgresql":
        pytest.skip("Chart buckets use PostgreSQL date_trunc")
    for user, total, mapped in ((crosswalk.admin, 7, 4), (crosswalk.reader, 6, 3)):
        body = stats_service.stats_events_timeseries(
            context=ChangeContext(method="GET", path="/v1/charts", actor=user),
            framework=TGT,
            start_date=DAY.date(),
            end_date=DAY.date(),
            db=db,
        )
        counts = {
            item["bucket"]: (item["total"], item["mapped"], item["unmapped"])
            for item in body["items"]
        }
        assert counts[DAY.date().isoformat()] == (total, mapped, total - mapped)
        assert sum(item["total"] for item in body["items"]) == total
