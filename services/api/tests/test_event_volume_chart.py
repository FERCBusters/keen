"""Event volume charts through the assembled application.

The chart counts the events a reader can open, in dense buckets, split into
events mapped to the chosen framework and the rest. Buckets use PostgreSQL's
date_trunc, so these tests run against PostgreSQL.
"""

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.core.source_meta import get_source_meta
from app.db.models import ControlItem, Event, Framework, Mapping, User

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen  # noqa: F401

PATH = "/v1/charts/event_volume_timeseries"
FW, OTHER = "fw-volume", "fw-other"


@pytest.fixture
def chart(keen):  # noqa: F811 - fixture imported above
    if not keen.postgres:
        pytest.skip("Chart buckets use PostgreSQL date_trunc")
    db = keen.db
    db.add_all([Framework(slug=FW), Framework(slug=OTHER)])
    controls = {
        ref: ControlItem(framework_slug=fw, type="annex_control", ref=ref, title=ref)
        for fw, ref in ((FW, "A.1"), (FW, "A.2"), (OTHER, "B.1"))
    }
    db.add_all(controls.values())
    db.flush()

    def event(at, source="ci", *refs, **columns):
        row = Event(
            id=uuid.uuid4(),
            source=source,
            summary=f"{source} at {at}",
            timestamp=datetime.fromisoformat(at),
            external_id=columns.pop("external_id", uuid.uuid4().hex),
            raw_pointer={},
            normalized_payload={},
            **columns,
        )
        db.add(row)
        db.flush()
        for ref in refs:
            db.add(
                Mapping(
                    event_id=row.id, control_item_id=controls[ref].id, method="rule"
                )
            )
        return row

    event("2026-02-28T23:59:59", "ci", "A.1")  # before the window
    event("2026-03-01T10:15:00", "ci", "A.1")
    event("2026-03-01T23:59:59", "ci")
    event("2026-03-02T00:00:00", "loki", "B.1")
    # Mapped to two of the framework's controls, counted once.
    event("2026-03-02T00:30:00", "loki", "A.1", "A.2")
    event("2026-03-02T01:00:00", "loki")
    # A framework-link diary entry about a risk is hidden without risk access.
    event(
        "2026-03-04T08:00:00",
        "diary",
        "A.1",
        system="isms",
        external_id=f"isms-link:risk:{uuid.uuid4()}",
    )
    event("2026-03-05T00:00:00", "ci", "A.1")  # after the window
    db.commit()

    def get(client, **params):
        response = client.get(PATH, params={"framework": FW, **params})
        assert response.status_code == 200, response.text
        return response.json()

    def counts(body):
        return [
            (i["bucket"], i["total"], i["mapped"], i["unmapped"]) for i in body["items"]
        ]

    reader_user = keen.user("events.read")
    return SimpleNamespace(
        keen=keen,
        db=db,
        reader=keen.client(reader_user),
        reader_user=reader_user,
        risk_reader=keen.client(keen.user("events.read", "risk.read")),
        get=get,
        counts=counts,
    )


WINDOW = {"start_date": "2026-03-01", "end_date": "2026-03-04"}


def test_daily_buckets_count_visible_events_once_against_the_framework(chart):
    body = chart.get(chart.reader, **WINDOW)

    assert chart.counts(body) == [
        ("2026-03-01", 2, 1, 1),
        ("2026-03-02", 3, 1, 2),
        ("2026-03-03", 0, 0, 0),
        ("2026-03-04", 0, 0, 0),
    ]
    assert (body["days"], body["start_date"], body["end_date"]) == (
        4,
        "2026-03-01",
        "2026-03-04",
    )
    assert (body["start_ts"], body["end_ts"]) == (
        "2026-03-01T00:00:00Z",
        "2026-03-05T00:00:00Z",
    )
    assert (body["interval"], body["interval_requested"], body["framework"]) == (
        "day",
        "day",
        FW,
    )
    # The risk-link diary entry counts for a reader who can open it.
    assert chart.counts(chart.get(chart.risk_reader, **WINDOW))[-1] == (
        "2026-03-04",
        1,
        1,
        0,
    )
    # A reversed window reads the same days.
    reversed_window = chart.get(
        chart.reader, start_date="2026-03-04", end_date="2026-03-01"
    )
    assert chart.counts(reversed_window) == chart.counts(body)


def test_counts_follow_the_framework_and_source_filters(chart):
    other = chart.get(chart.reader, framework=OTHER, **WINDOW)
    loki = chart.get(chart.reader, source="loki", **WINDOW)

    assert chart.counts(other)[:2] == [("2026-03-01", 2, 0, 2), ("2026-03-02", 3, 1, 2)]
    assert chart.counts(loki)[:2] == [("2026-03-01", 0, 0, 0), ("2026-03-02", 3, 1, 2)]
    assert loki["source"] == "loki"


def test_counts_follow_the_events_page_filters(chart):
    """The chart shows the events the list shows: lists, controls and text."""
    both = chart.get(chart.reader, source="loki,ci", breakdown="source", **WINDOW)
    assert chart.counts(both)[:2] == [("2026-03-01", 2, 1, 1), ("2026-03-02", 3, 1, 2)]
    assert both["source"] == "loki,ci"
    assert [s["source"] for s in both["sources"]] == ["loki", "ci"]

    control = chart.get(chart.reader, control="A.1", **WINDOW)
    assert chart.counts(control)[:2] == [
        ("2026-03-01", 1, 1, 0),
        ("2026-03-02", 1, 1, 0),
    ]
    condition = chart.get(chart.reader, f="summary:contains:loki at", **WINDOW)
    assert chart.counts(condition)[:2] == [
        ("2026-03-01", 0, 0, 0),
        ("2026-03-02", 3, 1, 2),
    ]
    unmapped = chart.get(chart.reader, unmapped="true", **WINDOW)
    assert chart.counts(unmapped)[:2] == [
        ("2026-03-01", 1, 0, 1),
        ("2026-03-02", 2, 0, 2),
    ]
    refused = chart.reader.get(
        PATH, params={"framework": FW, "f": "colour:contains:x", **WINDOW}
    )
    assert refused.status_code == 400


def test_hourly_buckets_follow_a_timestamp_window_with_an_exclusive_end(chart):
    body = chart.get(
        chart.reader,
        # 09:00 at UTC+11 is 22:00 UTC on the previous day.
        start_ts="2026-03-02T09:00:00+11:00",
        # The mapped event at exactly 00:30 falls outside the window.
        end_ts="2026-03-02T00:30:00Z",
        interval="hour",
        breakdown="source",
    )

    assert chart.counts(body) == [
        ("2026-03-01T22:00:00Z", 0, 0, 0),
        ("2026-03-01T23:00:00Z", 1, 0, 1),
        ("2026-03-02T00:00:00Z", 1, 0, 1),
    ]
    assert (body["start_ts"], body["end_ts"], body["interval"]) == (
        "2026-03-01T22:00:00Z",
        "2026-03-02T00:30:00Z",
        "hour",
    )
    assert [item["by_source"] for item in body["items"]] == [{}, {"ci": 1}, {"loki": 1}]
    # A minute later the same bucket counts it, as mapped. A window inside
    # one bucket still draws two points.
    later = chart.get(
        chart.reader,
        start_ts="2026-03-02T00:00:00Z",
        end_ts="2026-03-02T00:31:00Z",
        interval="hour",
    )
    assert chart.counts(later) == [
        ("2026-03-02T00:00:00Z", 2, 1, 1),
        ("2026-03-02T01:00:00Z", 0, 0, 0),
    ]


@pytest.mark.parametrize(
    "requested,days,used,buckets",
    [
        ("second", 3, "minute", 3 * 24 * 60),
        ("second", 4, "hour", 4 * 24),
        ("minute", 4, "hour", 4 * 24),
        ("fortnight", 4, "day", 4),
    ],
)
def test_fine_intervals_coarsen_to_stay_within_the_bucket_budget(
    chart, requested, days, used, buckets
):
    body = chart.get(
        chart.reader,
        start_ts="2026-03-01T00:00:00",
        end_ts=f"2026-03-0{1 + days}T00:00:00",
        interval=requested,
    )

    assert (body["interval"], body["interval_requested"]) == (
        used,
        requested if requested != "fortnight" else "day",
    )
    assert len(body["items"]) == buckets
    assert sum(i["total"] for i in body["items"]) == 5


def test_the_source_breakdown_lists_each_visible_source(chart):
    chart.db.get(User, chart.reader_user.id).pref_source_colors = {"loki": "#123456"}
    chart.db.commit()

    body = chart.get(chart.reader, breakdown="source", **WINDOW)

    assert body["breakdown"] == "source"
    assert [i["by_source"] for i in body["items"]] == [
        {"ci": 2},
        {"loki": 3},
        {},
        {},
    ]
    assert body["sources"] == [
        {
            "source": "loki",
            "label": get_source_meta("loki").label,
            "color": "#123456",
            "total": 3,
        },
        {
            "source": "ci",
            "label": get_source_meta("ci").label,
            "color": get_source_meta("ci").color,
            "total": 2,
        },
    ]
    assert "by_source" not in chart.get(chart.reader, **WINDOW)["items"][0]


@pytest.mark.parametrize(
    "params,detail",
    [
        (
            {"start_date": "1000-01-01", "end_date": "1999-12-31"},
            "The requested window needs more than 5000 daily buckets; narrow the date range",
        ),
        (
            {"start_date": "9999-12-30", "end_date": "9999-12-31"},
            "The requested date range is not supported",
        ),
    ],
    ids=["too-many-days", "calendar-limit"],
)
def test_unusable_windows_are_refused_as_bad_requests(chart, params, detail):
    refused = chart.reader.get(PATH, params=params)

    assert refused.status_code == 400
    assert refused.json()["detail"] == detail


@pytest.mark.parametrize(
    "params,window",
    [
        # With only an end, the window reaches back the requested days.
        (
            {"end_ts": "2026-03-03T00:00:00Z", "days": 2},
            ("2026-03-01T00:00:00Z", "2026-03-03T00:00:00Z"),
        ),
        # Reversed timestamps read the same window.
        (
            {"start_ts": "2026-03-03T00:00:00Z", "end_ts": "2026-03-01T00:00:00Z"},
            ("2026-03-01T00:00:00Z", "2026-03-03T00:00:00Z"),
        ),
        # With only an end date, the window ends that day.
        (
            {"end_date": "2026-03-02", "days": 2},
            ("2026-03-01T00:00:00Z", "2026-03-03T00:00:00Z"),
        ),
    ],
    ids=["end-only", "reversed", "end-date-only"],
)
def test_partial_windows_are_completed_from_the_days_requested(chart, params, window):
    body = chart.get(chart.reader, **params)

    assert (body["start_ts"], body["end_ts"]) == window
    assert chart.counts(body)[:2] == [
        ("2026-03-01", 2, 1, 1),
        ("2026-03-02", 3, 1, 2),
    ]


def test_second_buckets_cover_a_short_window(chart):
    body = chart.get(
        chart.reader,
        start_ts="2026-03-01T10:14:58Z",
        end_ts="2026-03-01T10:15:02Z",
        interval="second",
    )

    assert body["interval"] == "second"
    assert chart.counts(body) == [
        ("2026-03-01T10:14:58Z", 0, 0, 0),
        ("2026-03-01T10:14:59Z", 0, 0, 0),
        ("2026-03-01T10:15:00Z", 1, 1, 0),
        ("2026-03-01T10:15:01Z", 0, 0, 0),
    ]
    # A window with no length still reads one bucket.
    instant = chart.get(
        chart.reader,
        start_ts="2026-03-01T10:15:00Z",
        end_ts="2026-03-01T10:15:00Z",
        interval="minute",
    )
    assert chart.counts(instant)[0] == ("2026-03-01T10:15:00Z", 1, 1, 0)
