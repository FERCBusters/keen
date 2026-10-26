"""Source activity insights: quiet detection, spikes, repeats and suggestions.

The service tests run on SQLite against a source's own history; the assembled
application tests cover the permission boundary of the two new read routes.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from app.core import cache
from app.db.models import ControlItem, Event, Framework, Mapping, User
from app.services import source_insights as insights
from app.services.errors import DomainError
from app.services.events import event_control_suggestions
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tests.db_helpers import create_sqlite_schema

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen  # noqa: F401

NOW = datetime(2026, 10, 10, 12, 0, 0)
FW = "fw-insights"


@pytest.fixture(autouse=True)
def uncached(monkeypatch):
    monkeypatch.setattr(cache.settings, "aggregate_cache_ttl_seconds", 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    create_sqlite_schema(engine)
    with Session(engine) as session:
        session.add(Framework(slug=FW))
        session.add(
            ControlItem(framework_slug=FW, type="control", ref="A.1", title="A.1")
        )
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def user(db):
    row = User(username="reader", password_hash="x", role="normal", is_active=True)
    db.add(row)
    db.commit()
    return row


def add_events(db, timestamps, *, source="ci", summary="deploy {n} finished"):
    rows = []
    for n, ts in enumerate(timestamps):
        rows.append(
            Event(
                id=uuid.uuid4(),
                source=source,
                timestamp=ts,
                summary=summary.format(n=n),
                action="deploy",
                system="build",
                outcome="success",
                raw_pointer={},
                normalized_payload={},
                external_id=uuid.uuid4().hex,
            )
        )
    db.add_all(rows)
    db.commit()
    return rows


def hourly(count, *, end=NOW, step=timedelta(hours=1)):
    return [end - step * i for i in range(count)]


def run(db, user, **kwargs):
    return insights.source_insights(
        source_name="ci", framework=FW, db=db, user=user, now=NOW, **kwargs
    )


def test_summary_patterns_collapse_identifiers_and_numbers():
    pattern = insights.summary_pattern(
        "Job 1234 on host web-01 finished for 9f1c2a7e-0000-4000-8000-0123456789ab in 3.5s"
    )
    assert pattern == "job # on host web-# finished for {id} in #s"
    assert insights.summary_pattern("  Mixed   CASE  ") == "mixed case"
    assert insights.summary_pattern("") == ""


@pytest.mark.parametrize(
    "seconds,label",
    [
        (5, "under a minute"),
        (60, "1 minute"),
        (3600 * 26 + 120, "1 day 2 hours"),
        (86400 * 3, "3 days"),
    ],
)
def test_durations_read_naturally(seconds, label):
    assert insights.humanise_seconds(seconds) == label


def test_a_source_with_no_events_is_reported_as_such(db, user):
    result = run(db, user)
    assert result["status"] == "no_events"
    assert result["last_seen"] is None
    assert result["daily"][-1] == {"date": "2026-10-10", "count": 0}
    assert len(result["daily"]) == 91
    assert result["repeated_summaries"] == []


def test_too_few_events_give_no_baseline(db, user):
    add_events(db, [NOW - timedelta(days=2), NOW - timedelta(days=1)])
    result = run(db, user)
    assert result["status"] == "insufficient"
    assert result["since_last_seconds"] == 86400
    assert "no baseline" in result["message"]


def test_a_regular_source_is_active_when_its_last_event_is_recent(db, user):
    add_events(db, hourly(48, end=NOW - timedelta(minutes=30)))
    result = run(db, user)
    assert result["status"] == "active"
    assert result["typical_gap_seconds"] == 3600
    assert result["quiet_threshold_seconds"] == 7200
    assert result["quiet_periods"] == []
    assert "0.53 events per day over this 90-day window" in result["message"]


def test_a_regular_source_that_stops_is_quiet_relative_to_its_own_rhythm(db, user):
    add_events(db, hourly(48, end=NOW - timedelta(days=3)))
    result = run(db, user)
    assert result["status"] == "quiet"
    assert result["since_last_seconds"] == 3 * 86400
    assert result["message"].startswith("No events for 3 days;")
    assert "90% of sampled gaps are at most 1 hour" in result["message"]


def test_a_daily_report_three_days_late_is_quiet_but_one_day_late_is_fine(db, user):
    add_events(db, hourly(20, end=NOW - timedelta(hours=30), step=timedelta(days=1)))
    assert run(db, user)["status"] == "active"
    db.query(Event).delete()
    db.commit()
    add_events(db, hourly(20, end=NOW - timedelta(days=3), step=timedelta(days=1)))
    assert run(db, user)["status"] == "quiet"


def test_quiet_periods_inside_the_window_are_listed_longest_first(db, user):
    recent = hourly(24, end=NOW - timedelta(minutes=10))
    older = hourly(24, end=NOW - timedelta(days=2))
    oldest = hourly(24, end=NOW - timedelta(days=8))
    add_events(db, recent + older + oldest)
    result = run(db, user)
    assert result["status"] == "active"
    labels = [p["label"] for p in result["quiet_periods"]]
    assert labels == ["5 days 1 hour", "1 day 50 minutes"]
    assert result["quiet_periods"][0]["start"] < result["quiet_periods"][0]["end"]


def test_spikes_stand_out_against_the_median_active_day(db, user):
    quiet_days = [NOW - timedelta(days=d, hours=1) for d in range(1, 15)]
    busy_day = [NOW - timedelta(days=5, minutes=m) for m in range(1, 21)]
    add_events(db, quiet_days + busy_day)
    result = run(db, user)
    assert [s["date"] for s in result["spikes"]] == ["2026-10-05"]
    spike = result["spikes"][0]
    assert spike["count"] == 21 and spike["baseline"] == 1 and spike["ratio"] == 21


def test_trend_compares_the_last_seven_days_with_the_seven_before(db, user):
    recent = [NOW - timedelta(days=d, hours=1) for d in range(0, 7)]
    previous = [NOW - timedelta(days=d, hours=1) for d in range(7, 14) for _ in (0, 1)]
    add_events(db, recent + previous)
    trend = run(db, user)["trend"]
    assert trend == {"recent_7_days": 7, "previous_7_days": 14, "change_pct": -50}


def test_repeated_summaries_group_by_pattern_and_prefer_an_unmapped_example(db, user):
    rows = add_events(
        db,
        hourly(6, end=NOW - timedelta(hours=1)),
        summary="Backup job {n} completed on srv-{n}",
    )
    add_events(db, hourly(2, end=NOW - timedelta(hours=2)), summary="One-off {n}")
    control = db.query(ControlItem).filter_by(framework_slug=FW).one()
    # The two most recent repeats are mapped; the example must be an unmapped one.
    for row in rows[:2]:
        db.add(
            Mapping(
                event_id=row.id,
                control_item_id=control.id,
                confidence=1,
                method="manual",
            )
        )
    db.commit()
    result = run(db, user)
    assert len(result["repeated_summaries"]) == 1
    group = result["repeated_summaries"][0]
    assert group["pattern"] == "backup job # completed on srv-#"
    assert group["count"] == 6 and group["unmapped_count"] == 4
    assert group["share_pct"] == 75
    assert group["example_event_id"] == str(rows[2].id)
    assert group["example"] == "Backup job 2 completed on srv-2"


def test_events_outside_the_window_shape_last_seen_only(db, user):
    add_events(db, hourly(10, end=NOW - timedelta(days=100)))
    result = run(db, user, days=30)
    assert result["lifetime_events"] == 10
    assert result["window_events"] == 0
    assert result["status"] == "insufficient"
    assert result["last_seen"] == (NOW - timedelta(days=100)).isoformat()


def test_other_sources_and_window_sizes_are_validated(db, user):
    add_events(db, hourly(5), source="other")
    assert run(db, user)["status"] == "no_events"
    with pytest.raises(DomainError):
        run(db, user, days=3)
    with pytest.raises(DomainError):
        run(db, user, days=1000)
    with pytest.raises(DomainError):
        insights.source_insights(
            source_name="", framework=FW, db=db, user=user, now=NOW
        )


def test_event_control_suggestions_rank_the_catalogue_and_skip_mapped_refs(db, user):
    db.add_all(
        [
            ControlItem(
                framework_slug=FW,
                type="control",
                ref="BK-1",
                title="Information backup",
                meta={
                    "description": "Backup copies of information are taken and tested."
                },
            ),
            ControlItem(
                framework_slug=FW,
                type="control",
                ref="PH-1",
                title="Physical entry controls",
                meta={"description": "Secure areas are protected by entry controls."},
            ),
        ]
    )
    db.commit()
    (event,) = add_events(db, [NOW], summary="Nightly backup completed and tested")
    result = event_control_suggestions(
        event_id=str(event.id), framework=FW, db=db, user=user
    )
    assert result["unmapped"] is True
    assert [item["ref"] for item in result["items"]][:1] == ["BK-1"]
    assert "PH-1" not in [item["ref"] for item in result["items"]]
    assert result["items"][0]["reason"]
    backup = db.query(ControlItem).filter_by(ref="BK-1").one()
    db.add(
        Mapping(
            event_id=event.id, control_item_id=backup.id, confidence=1, method="manual"
        )
    )
    db.commit()
    result = event_control_suggestions(
        event_id=str(event.id), framework=FW, db=db, user=user
    )
    assert result["unmapped"] is False
    assert result["already_mapped"] == ["BK-1"]
    assert "BK-1" not in [item["ref"] for item in result["items"]]
    with pytest.raises(DomainError):
        event_control_suggestions(
            event_id=str(uuid.uuid4()), framework=FW, db=db, user=user
        )
    with pytest.raises(DomainError):
        event_control_suggestions(event_id="nope", framework=FW, db=db, user=user)


def test_insight_routes_need_events_read(keen):  # noqa: F811 - fixture imported above
    keen.db.add(Framework(slug=FW))
    keen.db.add(
        Event(
            id=uuid.uuid4(),
            source="ci",
            timestamp=NOW,
            summary="deploy finished",
            raw_pointer={},
            normalized_payload={},
            external_id=uuid.uuid4().hex,
        )
    )
    keen.db.commit()
    event_id = str(keen.db.query(Event.id).filter(Event.source == "ci").scalar())
    paths = [
        f"/v1/sources/ci/insights?framework={FW}",
        f"/v1/events/{event_id}/control-suggestions?framework={FW}",
    ]
    for path in paths:
        assert keen.client().get(path).status_code == 401
        assert keen.client(keen.user()).get(path).status_code == 403
    reader = keen.client(keen.user("events.read"))
    insight = reader.get(paths[0])
    assert insight.status_code == 200, insight.text
    assert insight.json()["source"] == "ci"
    assert insight.json()["lifetime_events"] >= 1
    assert reader.get(paths[0] + "&days=2").status_code == 400
    assert reader.get(paths[0] + "&days=abc").status_code == 422
    suggestions = reader.get(paths[1])
    assert suggestions.status_code == 200, suggestions.text
    assert suggestions.json()["event_id"] == event_id


def test_daily_batches_describe_rate_without_claiming_continuous_arrivals(db, user):
    # Five articles collected together each day have a zero median gap.
    add_events(
        db, [NOW - timedelta(days=day, hours=1) for day in range(7) for _ in range(5)]
    )
    result = run(db, user, days=7)
    assert result["window_events"] == 35
    assert result["average_events_per_day"] == 5
    assert result["typical_gap_seconds"] == 0
    assert result["usual_longest_gap_seconds"] == 86400
    assert "5 events per day over this 7-day window" in result["message"]
    assert "several a minute" not in result["message"]
    assert "one every" not in result["message"]


def test_activity_rate_includes_empty_days_and_excludes_future_events(db, user):
    add_events(db, [NOW - timedelta(days=2)] * 7 + [NOW + timedelta(days=1)] * 100)
    result = run(db, user, days=7)
    assert result["window_events"] == 7
    assert result["average_events_per_day"] == 1
    assert result["last_seen"] == (NOW - timedelta(days=2)).isoformat()
