"""Recurring schedules shared by scheduled audits and quizzes.

A schedule is any record with the ``schedule_*`` columns and a ``start_date``
(its first run): a recurrence (once, weekly, monthly, quarterly or yearly), an
interval, an optional end date and a date rule. An exact rule keeps the day of
the month the schedule started on; a fuzzy rule such as "first Monday of the
month" takes the first matching day in each month the schedule runs in.

The daily worker turns due schedules into runs, such as an audit created from
a template or a quiz round. Reminders email each recipient once, a configured
number of days before a run starts or falls due.
"""

from __future__ import annotations

import logging
from calendar import monthrange
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.services.errors import DomainError
from app.services.mailer import smtp_configured

log = logging.getLogger(__name__)

SCHEDULE_RECURRENCES = {"once", "weekly", "monthly", "quarterly", "yearly"}
SCHEDULE_DATE_RULES = {
    "exact",
    "first_weekday_of_month",
    "first_monday_of_month",
    "first_tuesday_of_month",
    "first_wednesday_of_month",
    "first_thursday_of_month",
    "first_friday_of_month",
}
FIRST_NAMED_WEEKDAY_RULES = {
    "first_monday_of_month": 0,
    "first_tuesday_of_month": 1,
    "first_wednesday_of_month": 2,
    "first_thursday_of_month": 3,
    "first_friday_of_month": 4,
}
FUZZY_MONTH_DATE_RULES = SCHEDULE_DATE_RULES - {"exact"}
_MONTHS_PER_STEP = {"monthly": 1, "quarterly": 3, "yearly": 12}
_RECURRENCE_LABELS = {
    "once": "Once",
    "weekly": "Weekly",
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "yearly": "Yearly",
}
_RECURRENCE_UNITS = {
    "weekly": "weeks",
    "monthly": "months",
    "quarterly": "quarters",
    "yearly": "years",
}


def _schedule_zone() -> ZoneInfo | None:
    tz_name = str(settings.timezone or "Etc/UTC").strip() or "Etc/UTC"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        log.warning(
            "Invalid KEEN_TIMEZONE=%r; falling back to server-local date for schedules",
            tz_name,
        )
    except Exception:
        log.exception(
            "Failed to resolve KEEN_TIMEZONE=%r; falling back to server-local date for schedules",
            tz_name,
        )
    return None


def today_for_schedule() -> date:
    """Return today's date in Keen's configured timezone for date-only schedules."""

    zone = _schedule_zone()
    return datetime.now(zone).date() if zone else date.today()


def schedule_date(moment: datetime) -> date:
    """The date a stored UTC time falls on in Keen's configured timezone."""

    zone = _schedule_zone()
    if zone is None:
        return moment.date()
    return moment.replace(tzinfo=timezone.utc).astimezone(zone).date()


def _add_months(d: date, months: int) -> date:
    month_idx = (d.month - 1) + months
    year = d.year + month_idx // 12
    month = month_idx % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def _weekday_within_month(d: date) -> date:
    """Move a weekend date to the Friday before it, staying inside its month.

    A Saturday or Sunday at the start of a month has no Friday in that month, so
    it moves to the following Monday instead.
    """

    if d.weekday() < 5:
        return d
    friday = d - timedelta(days=d.weekday() - 4)
    if friday.month == d.month:
        return friday
    return d + timedelta(days=7 - d.weekday())


def anchored_month_date(year: int, month: int, anchor_day: int) -> date:
    """Return the run date for an exact monthly, quarterly or yearly schedule.

    The schedule keeps the day of the month it started on. A month too short for
    that day uses its last day, so a schedule that started on the 31st runs on the
    last day of every month. Weekend dates move to the Friday before, or to the
    Monday after when that Friday falls in the previous month.
    """

    last = monthrange(year, month)[1]
    day = max(1, min(int(anchor_day), last))
    return _weekday_within_month(date(year, month, day))


def _first_matching_weekday_of_month(
    year: int, month: int, *, weekday: int | None = None
) -> date:
    """Return the first matching weekday date for a month.

    weekday=None means the first business weekday, Monday through Friday.
    Otherwise weekday follows Python's date.weekday(): Monday=0 ... Friday=4.
    """

    m = max(1, min(12, int(month or 1)))
    d = date(int(year), m, 1)
    if weekday is None:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d

    target = max(0, min(4, int(weekday)))
    while d.weekday() != target:
        d += timedelta(days=1)
    return d


def first_scheduled_rule_date(year: int, month: int, rule: str) -> date:
    """Return the first concrete date for a supported fuzzy monthly rule."""

    weekday = FIRST_NAMED_WEEKDAY_RULES.get(str(rule or "").strip().lower())
    return _first_matching_weekday_of_month(year, month, weekday=weekday)


def _fuzzy_month_rule_next(
    current: date,
    *,
    recurrence: str,
    interval: int,
    anchor_month: int | None,
    rule: str,
) -> date | None:
    recur = str(recurrence or "once").strip().lower()
    step = max(1, int(interval or 1))
    if recur == "monthly":
        candidate = _add_months(date(current.year, current.month, 1), step)
        return first_scheduled_rule_date(candidate.year, candidate.month, rule)
    if recur == "quarterly":
        candidate = _add_months(date(current.year, current.month, 1), 3 * step)
        return first_scheduled_rule_date(candidate.year, candidate.month, rule)
    if recur == "yearly":
        month = max(1, min(12, int(anchor_month or current.month)))
        return first_scheduled_rule_date(current.year + step, month, rule)
    # One-off and weekly schedules take no fuzzy rule.
    return None


def next_occurrence_after(
    current: date,
    *,
    recurrence: str,
    interval: int = 1,
    until: date | None = None,
    date_rule: str = "exact",
    anchor_month: int | None = None,
    anchor_day: int | None = None,
) -> date | None:
    """Return the run after ``current``, or None when the schedule has ended.

    Exact monthly, quarterly and yearly runs are placed on ``anchor_day`` (the
    day the schedule started on; ``current``'s day when omitted) in the month
    they fall in, so an earlier short month or weekend move never shifts them.
    """

    recur = str(recurrence or "once").strip().lower()
    step = max(1, int(interval or 1))
    rule = str(date_rule or "exact").strip().lower()
    if rule in FUZZY_MONTH_DATE_RULES:
        nxt = _fuzzy_month_rule_next(
            current,
            recurrence=recur,
            interval=step,
            anchor_month=anchor_month,
            rule=rule,
        )
        if not nxt:
            return None
    elif recur == "once":
        return None
    elif recur == "weekly":
        nxt = current + timedelta(days=7 * step)
    elif recur in _MONTHS_PER_STEP:
        month = _add_months(
            date(current.year, current.month, 1), _MONTHS_PER_STEP[recur] * step
        )
        nxt = anchored_month_date(month.year, month.month, anchor_day or current.day)
    else:
        return None
    if until and nxt > until:
        return None
    return nxt


def anchor_day(schedule: Any) -> int | None:
    """Return the day of the month a schedule's exact runs keep."""

    start = getattr(schedule, "start_date", None)
    return start.day if start else None


def schedule_label(schedule: Any) -> str:
    """How often a schedule runs, such as "Monthly" or "Every 2 weeks".

    A schedule without a start date has no runs and an empty label.
    """

    if getattr(schedule, "start_date", None) is None:
        return ""
    recurrence = str(getattr(schedule, "schedule_recurrence", None) or "once")
    interval = max(1, int(getattr(schedule, "schedule_interval", None) or 1))
    if recurrence not in _RECURRENCE_UNITS or interval == 1:
        return _RECURRENCE_LABELS.get(recurrence, "Once")
    return f"Every {interval} {_RECURRENCE_UNITS[recurrence]}"


def next_occurrence(schedule: Any, run_date: date) -> date | None:
    """Return the schedule's run after ``run_date``."""

    return next_occurrence_after(
        run_date,
        recurrence=getattr(schedule, "schedule_recurrence", None) or "once",
        interval=getattr(schedule, "schedule_interval", None) or 1,
        until=getattr(schedule, "schedule_until_date", None),
        date_rule=getattr(schedule, "schedule_date_rule", None) or "exact",
        anchor_month=getattr(schedule, "schedule_anchor_month", None),
        anchor_day=anchor_day(schedule),
    )


def occurrences_between(
    schedule: Any, *, start: date, end: date, limit: int = 100
) -> list[date]:
    """Return a schedule's run dates within [start, end]."""

    recur = (
        str(getattr(schedule, "schedule_recurrence", None) or "once").strip().lower()
    )
    if recur not in SCHEDULE_RECURRENCES:
        recur = "once"
    interval = max(1, int(getattr(schedule, "schedule_interval", None) or 1))
    until = getattr(schedule, "schedule_until_date", None)
    date_rule = (
        str(getattr(schedule, "schedule_date_rule", None) or "exact").strip().lower()
    )
    if date_rule not in SCHEDULE_DATE_RULES:
        date_rule = "exact"
    anchor_month = getattr(schedule, "schedule_anchor_month", None)
    day = anchor_day(schedule)

    def step(current: date) -> date | None:
        return next_occurrence_after(
            current,
            recurrence=recur,
            interval=interval,
            until=until,
            date_rule=date_rule,
            anchor_month=anchor_month,
            anchor_day=day,
        )

    cur = getattr(schedule, "schedule_next_run_date", None) or getattr(
        schedule, "start_date", None
    )
    if not cur:
        return []

    out: list[date] = []
    guard = 0
    # Advance cheaply until the window if the saved next date is before it.
    while cur and cur < start and guard < 1000:
        cur = step(cur)
        guard += 1
    while cur and cur <= end and len(out) < limit:
        if cur >= start:
            out.append(cur)
        if recur == "once":
            break
        cur = step(cur)
    return out


def advance_past_missed_runs(
    schedule: Any, *, run_today: date, max_steps: int = 1000
) -> tuple[date | None, list[str]]:
    """Advance a stale next run date without creating backfilled runs.

    A run is created only when its date is today or inside the look-ahead
    window. If the worker was offline for earlier dates, those dates are
    skipped rather than creating runs that have already begun.
    """

    run_date = schedule.schedule_next_run_date
    skipped: list[str] = []
    steps = 0
    while run_date and run_date < run_today and steps < max_steps:
        skipped.append(run_date.isoformat())
        run_date = next_occurrence(schedule, run_date)
        steps += 1
    if run_date and run_date < run_today:
        log.warning(
            "Stopped advancing stale schedule %s after %s missed runs; "
            "disabling its next run date to avoid creating a past run",
            getattr(schedule, "id", "unknown"),
            max_steps,
        )
        return None, skipped
    return run_date, skipped


# --- Validation ----------------------------------------------------------------


def clean_schedule_recurrence(raw: str | None, *, default: str = "once") -> str:
    v = (raw or default).strip().lower()
    if v not in SCHEDULE_RECURRENCES:
        raise DomainError(status_code=400, detail="Invalid schedule_recurrence")
    return v


def clean_schedule_interval(raw: int | None) -> int:
    try:
        value = int(raw or 1)
    except Exception:
        raise DomainError(status_code=400, detail="Invalid schedule_interval")
    if value < 1 or value > 120:
        raise DomainError(
            status_code=400, detail="schedule_interval must be between 1 and 120"
        )
    return value


def clean_schedule_date_rule(raw: str | None, *, default: str = "exact") -> str:
    v = (raw or default).strip().lower()
    if v not in SCHEDULE_DATE_RULES:
        raise DomainError(status_code=400, detail="Invalid schedule_date_rule")
    return v


def clean_schedule_anchor_month(raw: int | None, *, required: bool) -> int | None:
    if raw is None or str(raw).strip() == "":
        if required:
            raise DomainError(
                status_code=400,
                detail="schedule_anchor_month is required for first weekday schedules",
            )
        return None
    try:
        value = int(raw)
    except Exception:
        raise DomainError(status_code=400, detail="Invalid schedule_anchor_month")
    if value < 1 or value > 12:
        raise DomainError(
            status_code=400, detail="schedule_anchor_month must be between 1 and 12"
        )
    return value


def schedule_start_for_rule(
    start_date: date, *, date_rule: str, anchor_month: int | None
) -> date:
    if date_rule in FUZZY_MONTH_DATE_RULES:
        return first_scheduled_rule_date(
            start_date.year, anchor_month or start_date.month, date_rule
        )
    return start_date


def apply_schedule_fields(
    schedule: Any,
    *,
    recurrence: str,
    interval: int,
    next_run_date: date | None,
    until_date: date | None,
    date_rule: str = "exact",
    anchor_month: int | None = None,
) -> None:
    if until_date and next_run_date and until_date < next_run_date:
        raise DomainError(
            status_code=400,
            detail="schedule_until_date cannot be before the next run date",
        )
    if date_rule in FUZZY_MONTH_DATE_RULES and recurrence == "weekly":
        raise DomainError(
            status_code=400,
            detail="First weekday schedules can recur monthly, quarterly or yearly",
        )
    schedule.schedule_recurrence = recurrence
    schedule.schedule_interval = interval
    schedule.schedule_date_rule = date_rule
    schedule.schedule_anchor_month = anchor_month if date_rule != "exact" else None
    schedule.schedule_next_run_date = next_run_date
    schedule.schedule_until_date = until_date


def revise_schedule(
    schedule: Any, payload: Any, fields: set[str], *, start_required: bool = True
) -> None:
    """Apply a payload's ``start_date`` and ``schedule_*`` fields to a schedule.

    Only the fields named in ``fields`` change. A fuzzy date rule moves the
    start date to the rule's day in the anchor month. Editing the start date or
    the date rule moves the next run there, even after earlier runs; other
    edits keep the next run. Without ``start_required``, a schedule with no
    start date has no runs.
    """
    if "start_date" in fields:
        if payload.start_date is None and start_required:
            raise DomainError(status_code=400, detail="start_date is required")
        schedule.start_date = payload.start_date
    date_rule = (
        clean_schedule_date_rule(
            payload.schedule_date_rule, default=schedule.schedule_date_rule or "exact"
        )
        if "schedule_date_rule" in fields
        else (schedule.schedule_date_rule or "exact")
    )
    anchor_month = clean_schedule_anchor_month(
        (
            payload.schedule_anchor_month
            if "schedule_anchor_month" in fields
            else schedule.schedule_anchor_month
        ),
        required=(date_rule in FUZZY_MONTH_DATE_RULES),
    )
    if schedule.start_date is None:
        if start_required:
            raise DomainError(status_code=400, detail="start_date is required")
    else:
        schedule.start_date = schedule_start_for_rule(
            schedule.start_date, date_rule=date_rule, anchor_month=anchor_month
        )
    recurrence = (
        clean_schedule_recurrence(
            payload.schedule_recurrence, default=schedule.schedule_recurrence or "once"
        )
        if "schedule_recurrence" in fields
        else (schedule.schedule_recurrence or "once")
    )
    interval = (
        clean_schedule_interval(payload.schedule_interval)
        if "schedule_interval" in fields
        else int(schedule.schedule_interval or 1)
    )
    until = (
        payload.schedule_until_date
        if "schedule_until_date" in fields
        else schedule.schedule_until_date
    )
    if {"start_date", "schedule_date_rule", "schedule_anchor_month"} & fields:
        next_run = schedule.start_date
    else:
        next_run = schedule.schedule_next_run_date or schedule.start_date
    apply_schedule_fields(
        schedule,
        recurrence=recurrence,
        interval=interval,
        next_run_date=next_run,
        until_date=until,
        date_rule=date_rule,
        anchor_month=anchor_month,
    )


# --- Creating runs ---------------------------------------------------------------


def materialise_one(
    schedule: Any,
    *,
    run_today: date,
    latest_create_date: date,
    create_run: Callable[[Any, date], Any],
) -> tuple[Any | None, dict[str, Any] | None]:
    """Advance one locked schedule and create its due run, if it has one.

    Returns the run ``create_run`` made, or None, and a record of any missed
    run dates it skipped with the date it moved on to. The caller commits.
    """
    skipped = None
    run_date = schedule.schedule_next_run_date
    if not run_date or run_date > latest_create_date:
        return None, None

    if run_date < run_today:
        run_date, missed = advance_past_missed_runs(schedule, run_today=run_today)
        schedule.schedule_next_run_date = run_date
        schedule.updated_at = utc_now_naive()
        if missed:
            skipped = {
                "skipped_run_dates": missed,
                "next_run_date": run_date.isoformat() if run_date else None,
            }
        if not run_date or run_date > latest_create_date:
            return None, skipped

    # Defensive idempotency guard for repeated runs on the same persisted state.
    if schedule.schedule_last_run_date and schedule.schedule_last_run_date >= run_date:
        schedule.schedule_next_run_date = next_occurrence(schedule, run_date)
        return None, skipped

    run = create_run(schedule, run_date)
    schedule.schedule_last_run_date = run_date
    schedule.schedule_next_run_date = next_occurrence(schedule, run_date)
    schedule.updated_at = utc_now_naive()
    return run, skipped


def materialise_due(
    db: Session,
    *,
    model: Any,
    filters: Iterable[Any],
    run_today: date,
    latest_create_date: date,
    create_run: Callable[[Any, date], Any],
    after_commit: Callable[[Any, Any], dict[str, Any]],
) -> tuple[int, list[dict[str, Any]], list[tuple[Any, dict[str, Any]]]]:
    """Create the runs of every schedule due by ``latest_create_date``.

    Each schedule is read again under its row lock, which schedule edits also
    take, so an overlapping run or edit is seen before a run is created and one
    run date never produces two runs. Each schedule commits on its own, then
    ``after_commit(schedule_id, run)`` (for example, emailing attendees)
    reports on its run.

    Returns the number of schedules checked, a report per created run and, per
    schedule id, the missed dates it skipped.
    """
    conditions = list(filters)
    due = (
        db.query(model.id)
        .filter(*conditions)
        .filter(model.schedule_next_run_date.isnot(None))
        .filter(model.schedule_next_run_date <= latest_create_date)
        .order_by(model.schedule_next_run_date.asc(), model.created_at.asc())
        .all()
    )
    created: list[dict[str, Any]] = []
    skipped_past: list[tuple[Any, dict[str, Any]]] = []
    for (schedule_id,) in due:
        schedule = (
            db.query(model)
            .filter(model.id == schedule_id, *conditions)
            .with_for_update()
            .one_or_none()
        )
        run, skipped = (
            materialise_one(
                schedule,
                run_today=run_today,
                latest_create_date=latest_create_date,
                create_run=create_run,
            )
            if schedule is not None
            else (None, None)
        )
        db.commit()
        if skipped:
            skipped_past.append((schedule_id, skipped))
        if run is not None:
            db.refresh(run)
            created.append(after_commit(schedule_id, run))
    return len(due), created, skipped_past


# --- Reminders ---------------------------------------------------------------


def reminder_window(today: date | None = None) -> tuple[date, date] | None:
    """Return the dates a reminder covers today, or None when reminders are off.

    A run starting or falling due between today and the configured number of
    days ahead is due a reminder.
    """
    days = max(0, int(settings.reminder_days_before or 0))
    if days == 0 or not smtp_configured():
        return None
    first = today or today_for_schedule()
    return first, first + timedelta(days=days)


def deliver_once(
    db: Session,
    *,
    model: Any,
    marker: str,
    ids: Iterable[Any],
    compose: Callable[[Any], Callable[[], None] | bool],
) -> int:
    """Send each recipient row's email once; return how many rows it covered.

    ``marker`` names the row's timestamp column, such as ``reminded_at``. A row
    is claimed by setting it before the email goes out, so overlapping workers
    never send twice and no row lock is held during delivery. ``compose`` reads
    what the row's email needs and returns a function that sends it, True when
    another row's email covers the same address, or False when the row has no
    address. Its transaction ends before the email goes out, so none stays open
    while the mail server answers. No address, or a failed delivery, releases
    the claim so a later run tries again while the email is still due.
    """
    column = getattr(model, marker)
    sent = 0
    for row_id in ids:
        claimed = (
            db.query(model)
            .filter(model.id == row_id, column.is_(None))
            .update({column: utc_now_naive()}, synchronize_session="fetch")
        )
        db.commit()
        if not claimed:
            continue
        try:
            outcome = compose(db.get(model, row_id))
            db.commit()
            if callable(outcome):
                outcome()
                delivered = True
            else:
                delivered = bool(outcome)
        except Exception:
            log.exception("Failed to send an email for %s %s", model.__name__, row_id)
            db.rollback()
            delivered = False
        if delivered:
            sent += 1
            continue
        db.query(model).filter(model.id == row_id).update(
            {column: None}, synchronize_session="fetch"
        )
        db.commit()
    return sent
