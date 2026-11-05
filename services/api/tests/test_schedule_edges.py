"""Recurring schedules at their edges: a malformed timezone, a window long
after a schedule's saved next date, and a schedule stale beyond the steps
Keen advances it."""

from datetime import date, timedelta
from unittest.mock import Mock

from app.core.config import settings
from app.services import schedules

from tests.test_schedules import _template


def test_a_malformed_timezone_falls_back_to_the_server_date(monkeypatch):
    log = Mock()
    monkeypatch.setattr(schedules, "log", log)
    monkeypatch.setattr(settings, "timezone", "../../etc/passwd")
    assert schedules.today_for_schedule() == date.today()
    log.warning.assert_not_called()
    message, name = log.exception.call_args.args
    assert message.startswith("Failed to resolve KEEN_TIMEZONE=%r")
    assert name == "../../etc/passwd"


def test_a_window_far_beyond_the_saved_next_date_lists_only_its_own_dates():
    # The cheap advance stops after 1,000 weeks, short of the window, so the
    # dates walked on from there are left out until the window starts.
    weekly = _template(
        schedule_next_run_date=date(2000, 1, 7), start_date=date(2000, 1, 7)
    )
    assert schedules.occurrences_between(
        weekly, start=date(2026, 4, 9), end=date(2026, 4, 30)
    ) == [date(2026, 4, 10), date(2026, 4, 17), date(2026, 4, 24)]


def test_a_schedule_stale_beyond_the_advance_limit_loses_its_next_run(monkeypatch):
    log = Mock()
    monkeypatch.setattr(schedules, "log", log)
    stale = _template(id="sched-1", schedule_next_run_date=date(2000, 1, 7))
    next_run, skipped = schedules.advance_past_missed_runs(
        stale, run_today=date(2026, 4, 10), max_steps=1000
    )
    assert next_run is None
    assert len(skipped) == 1000
    assert skipped[0] == "2000-01-07"
    assert skipped[-1] == (date(2000, 1, 7) + timedelta(weeks=999)).isoformat()
    message, ident, steps = log.warning.call_args.args
    assert message.startswith("Stopped advancing stale schedule %s")
    assert (ident, steps) == ("sched-1", 1000)

    # Within the limit the schedule moves to its first run from today.
    recent = _template(schedule_next_run_date=date(2026, 3, 20))
    assert schedules.advance_past_missed_runs(recent, run_today=date(2026, 4, 10)) == (
        date(2026, 4, 10),
        ["2026-03-20", "2026-03-27", "2026-04-03"],
    )
