"""Repeated-summary insights leave out events without a summary, and the
activity series covers each day of the window up to the clock's day."""

from datetime import timedelta

from tests.test_source_insights import NOW, add_events, hourly, run
from tests.test_source_insights import db as db
from tests.test_source_insights import uncached as uncached
from tests.test_source_insights import user as user


def test_events_without_a_summary_form_no_repeated_pattern(db, user):
    add_events(db, hourly(5, end=NOW - timedelta(hours=1)), summary="")
    add_events(db, hourly(4, end=NOW - timedelta(hours=2)), summary="   ")
    add_events(db, hourly(3, end=NOW - timedelta(hours=3)), summary="Sync {n} done")
    result = run(db, user)
    assert result["sample_size"] == 12
    assert [
        (group["pattern"], group["count"]) for group in result["repeated_summaries"]
    ] == [("sync # done", 3)]
    # Every sampled event still shapes the share of the repeated pattern.
    assert result["repeated_summaries"][0]["share_pct"] == 25


def test_the_daily_series_runs_from_the_window_start_to_the_clocks_day(db, user):
    add_events(db, [NOW - timedelta(days=2), NOW - timedelta(minutes=5)])
    days = run(db, user, days=7)["daily"]
    assert [day["date"] for day in days] == [
        (NOW - timedelta(days=7 - n)).date().isoformat() for n in range(8)
    ]
    assert days[-1] == {"date": NOW.date().isoformat(), "count": 1}
    assert days[-3] == {
        "date": (NOW - timedelta(days=2)).date().isoformat(),
        "count": 1,
    }
