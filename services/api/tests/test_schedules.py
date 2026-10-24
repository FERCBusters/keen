"""Recurring schedules shared by scheduled audits and quizzes.

The occurrence rules, the configured "today" and the notification address
check are pure functions, checked directly.
"""

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from app.core.config import settings
from app.services import mailer, schedules
from app.services.errors import DomainError


@pytest.mark.parametrize(
    "current,recurrence,interval,expected",
    [
        (date(2026, 4, 10), "weekly", 1, date(2026, 4, 17)),
        (date(2026, 4, 10), "weekly", 2, date(2026, 4, 24)),
        # 10 May 2026 is a Sunday, so the run moves back to Friday 8 May.
        (date(2026, 4, 10), "monthly", 1, date(2026, 5, 8)),
        (date(2026, 4, 10), "monthly", 3, date(2026, 7, 10)),
        (date(2026, 4, 10), "quarterly", 1, date(2026, 7, 10)),
        (date(2026, 11, 10), "quarterly", 2, date(2027, 5, 10)),
        # 10 April 2027 is a Saturday, so the run moves back to Friday 9 April.
        (date(2026, 4, 10), "yearly", 1, date(2027, 4, 9)),
        (date(2026, 4, 10), "YEARLY", 0, date(2027, 4, 9)),
        (date(2026, 4, 10), "once", 1, None),
        (date(2026, 4, 10), "fortnightly", 1, None),
    ],
)
def test_exact_occurrences_follow_the_recurrence(
    current, recurrence, interval, expected
):
    assert (
        schedules.next_occurrence_after(
            current, recurrence=recurrence, interval=interval
        )
        == expected
    )


def _anchored(start, recurrence="monthly", **kw):
    return SimpleNamespace(
        start_date=start,
        schedule_next_run_date=start,
        schedule_recurrence=recurrence,
        schedule_interval=1,
        schedule_until_date=None,
        schedule_date_rule="exact",
        schedule_anchor_month=None,
        **kw,
    )


def test_month_end_schedules_keep_their_day_through_short_months():
    # Started on Tuesday 31 March: every run is the month's last weekday.
    template = _anchored(date(2026, 3, 31))
    runs = schedules.occurrences_between(
        template, start=date(2026, 3, 1), end=date(2027, 3, 31)
    )
    assert runs == [
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 29),  # 31 May is a Sunday
        date(2026, 6, 30),
        date(2026, 7, 31),
        date(2026, 8, 31),
        date(2026, 9, 30),
        date(2026, 10, 30),  # 31 October is a Saturday
        date(2026, 11, 30),
        date(2026, 12, 31),
        date(2027, 1, 29),  # 31 January is a Sunday
        date(2027, 2, 26),  # 28 February is a Sunday
        date(2027, 3, 31),
    ]


def test_weekend_runs_at_the_start_of_a_month_move_to_the_monday():
    template = _anchored(date(2026, 1, 1))
    runs = schedules.occurrences_between(
        template, start=date(2026, 1, 1), end=date(2026, 8, 31)
    )
    assert runs == [
        date(2026, 1, 1),
        date(2026, 2, 2),  # 1 February is a Sunday; 30 January is in January
        date(2026, 3, 2),  # 1 March is a Sunday
        date(2026, 4, 1),
        date(2026, 5, 1),
        date(2026, 6, 1),
        date(2026, 7, 1),
        date(2026, 8, 3),  # 1 August is a Saturday
    ]


@pytest.mark.parametrize(
    "recurrence,start,end,expected",
    [
        # A leap-day schedule runs on the last weekday of February otherwise.
        (
            "yearly",
            date(2024, 2, 29),
            date(2028, 12, 31),
            [
                date(2024, 2, 29),
                date(2025, 2, 28),
                date(2026, 2, 27),
                date(2027, 2, 26),
                date(2028, 2, 29),
            ],
        ),
        (
            "quarterly",
            date(2025, 8, 31),
            date(2026, 9, 1),
            [
                date(2025, 8, 31),  # the first run is the chosen start date
                date(2025, 11, 28),  # 30 November is a Sunday
                date(2026, 2, 27),  # 28 February is a Saturday
                date(2026, 5, 29),  # 31 May is a Sunday
                date(2026, 8, 31),
            ],
        ),
    ],
)
def test_longer_recurrences_keep_the_anchor_day(recurrence, start, end, expected):
    template = _anchored(start, recurrence)
    assert schedules.occurrences_between(template, start=start, end=end) == expected


def test_weekly_schedules_keep_the_weekday_they_were_given():
    saturday = date(2026, 4, 11)
    assert schedules.next_occurrence_after(saturday, recurrence="weekly") == date(
        2026, 4, 18
    )


def test_occurrences_stop_after_the_until_date():
    until = date(2026, 4, 20)
    assert schedules.next_occurrence_after(
        date(2026, 4, 10), recurrence="weekly", until=until
    ) == date(2026, 4, 17)
    assert (
        schedules.next_occurrence_after(
            date(2026, 4, 17), recurrence="weekly", until=until
        )
        is None
    )


@pytest.mark.parametrize(
    "current,recurrence,rule,anchor,expected",
    [
        # 1 May 2026 is a Friday; 1 August 2026 is a Saturday.
        (date(2026, 4, 1), "monthly", "first_weekday_of_month", None, date(2026, 5, 1)),
        (date(2026, 7, 1), "monthly", "first_weekday_of_month", None, date(2026, 8, 3)),
        (date(2026, 4, 6), "monthly", "first_monday_of_month", None, date(2026, 5, 4)),
        (date(2026, 4, 7), "monthly", "first_tuesday_of_month", None, date(2026, 5, 5)),
        (
            date(2026, 4, 1),
            "monthly",
            "first_wednesday_of_month",
            None,
            date(2026, 5, 6),
        ),
        (
            date(2026, 4, 2),
            "monthly",
            "first_thursday_of_month",
            None,
            date(2026, 5, 7),
        ),
        (date(2026, 4, 3), "monthly", "first_friday_of_month", None, date(2026, 5, 1)),
        (
            date(2026, 4, 3),
            "quarterly",
            "first_friday_of_month",
            None,
            date(2026, 7, 3),
        ),
        (date(2026, 4, 3), "yearly", "first_friday_of_month", 9, date(2027, 9, 3)),
        (date(2026, 4, 3), "yearly", "first_friday_of_month", None, date(2027, 4, 2)),
        (date(2026, 4, 3), "weekly", "first_friday_of_month", None, None),
        (date(2026, 4, 3), "once", "first_friday_of_month", None, None),
    ],
)
def test_first_weekday_rules_pick_the_matching_day(
    current, recurrence, rule, anchor, expected
):
    assert (
        schedules.next_occurrence_after(
            current, recurrence=recurrence, date_rule=rule, anchor_month=anchor
        )
        == expected
    )


def test_first_weekday_helpers_skip_weekends():
    first_weekday = "first_weekday_of_month"
    # 1 February 2026 is a Sunday.
    assert schedules.first_scheduled_rule_date(2026, 2, first_weekday) == date(
        2026, 2, 2
    )
    assert schedules.first_scheduled_rule_date(2026, 4, first_weekday) == date(
        2026, 4, 1
    )
    assert schedules.first_scheduled_rule_date(
        2026, 2, "first_thursday_of_month"
    ) == date(2026, 2, 5)
    # Unknown rules fall back to the first business day.
    assert schedules.first_scheduled_rule_date(2026, 8, "unknown") == date(2026, 8, 3)


def _template(**kw):
    defaults = dict(
        schedule_recurrence="weekly",
        schedule_interval=1,
        schedule_until_date=None,
        schedule_date_rule="exact",
        schedule_anchor_month=None,
        schedule_next_run_date=date(2026, 4, 3),
        start_date=date(2026, 3, 27),
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_occurrences_between_lists_dates_inside_the_window():
    weekly = _template()
    assert schedules.occurrences_between(
        weekly, start=date(2026, 4, 9), end=date(2026, 4, 30)
    ) == [
        date(2026, 4, 10),
        date(2026, 4, 17),
        date(2026, 4, 24),
    ]
    assert schedules.occurrences_between(
        weekly, start=date(2026, 4, 1), end=date(2026, 6, 1), limit=2
    ) == [
        date(2026, 4, 3),
        date(2026, 4, 10),
    ]
    bounded = _template(schedule_until_date=date(2026, 4, 12))
    assert schedules.occurrences_between(
        bounded, start=date(2026, 4, 1), end=date(2026, 6, 1)
    ) == [
        date(2026, 4, 3),
        date(2026, 4, 10),
    ]
    # Unknown recurrences and rules fall back to a single exact occurrence.
    odd = _template(schedule_recurrence="hourly", schedule_date_rule="whenever")
    assert schedules.occurrences_between(
        odd, start=date(2026, 4, 1), end=date(2026, 6, 1)
    ) == [date(2026, 4, 3)]
    unscheduled = _template(schedule_next_run_date=None, start_date=None)
    assert (
        schedules.occurrences_between(
            unscheduled, start=date(2026, 1, 1), end=date(2027, 1, 1)
        )
        == []
    )
    from_start = _template(schedule_next_run_date=None, schedule_recurrence="monthly")
    assert schedules.occurrences_between(
        from_start, start=date(2026, 4, 1), end=date(2026, 5, 31)
    ) == [
        date(2026, 4, 27),
        date(2026, 5, 27),
    ]


def test_schedule_dates_use_the_configured_timezone(monkeypatch):
    monkeypatch.setattr(settings, "timezone", "Pacific/Kiritimati")
    assert (
        schedules.today_for_schedule()
        == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    )
    log = Mock()
    monkeypatch.setattr(schedules, "log", log)
    monkeypatch.setattr(settings, "timezone", "Not/A_Zone")
    assert schedules.today_for_schedule() == date.today()
    assert "Invalid KEEN_TIMEZONE" in log.warning.call_args.args[0]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("auditor@example.test", True),
        ("  auditor@example.test ", True),
        ("@example.test", False),
        ("auditor@", False),
        ("auditor", False),
        ("", False),
        (None, False),
        ("a@" + "x" * 300, False),
    ],
)
def test_notification_addresses_are_sanity_checked(value, expected):
    assert mailer.looks_like_email(value) is expected


def refused(call, *args, **kwargs):
    with pytest.raises(DomainError) as error:
        call(*args, **kwargs)
    return error.value.status_code, error.value.detail


@pytest.mark.parametrize(
    "user,expected",
    [
        (
            SimpleNamespace(email="lead@example.test", username="lead"),
            "lead@example.test",
        ),
        (
            SimpleNamespace(email="not-an-email", username="ops@example.test"),
            "ops@example.test",
        ),
        (SimpleNamespace(email=None, username="ops"), None),
        (None, None),
    ],
)
def test_the_email_snapshot_uses_the_best_address_a_user_has(user, expected):
    assert mailer.user_email(user) == expected


@pytest.mark.parametrize(
    "call,value,detail",
    [
        (schedules.clean_schedule_interval, "weekly", "Invalid schedule_interval"),
        (schedules.clean_schedule_interval, 0, None),
        (
            schedules.clean_schedule_interval,
            121,
            "schedule_interval must be between 1 and 120",
        ),
        (
            schedules.clean_schedule_recurrence,
            "fortnightly",
            "Invalid schedule_recurrence",
        ),
        (
            schedules.clean_schedule_date_rule,
            "last_friday_of_month",
            "Invalid schedule_date_rule",
        ),
    ],
)
def test_schedule_settings_outside_the_supported_set_are_refused(call, value, detail):
    if detail is None:
        # Zero reads as unset, which is one.
        assert call(value) == 1
        return
    assert refused(call, value) == (400, detail)


def test_anchor_months_are_required_only_for_first_weekday_rules():
    assert refused(schedules.clean_schedule_anchor_month, None, required=True) == (
        400,
        "schedule_anchor_month is required for first weekday schedules",
    )
    assert schedules.clean_schedule_anchor_month(" ", required=False) is None
    assert schedules.clean_schedule_anchor_month("3", required=True) == 3
    assert refused(schedules.clean_schedule_anchor_month, "March", required=True) == (
        400,
        "Invalid schedule_anchor_month",
    )
    assert refused(schedules.clean_schedule_anchor_month, 13, required=True) == (
        400,
        "schedule_anchor_month must be between 1 and 12",
    )


def test_first_weekday_rules_start_in_the_anchor_month():
    start = date(2026, 5, 20)

    assert (
        schedules.schedule_start_for_rule(start, date_rule="exact", anchor_month=1)
        == start
    )
    assert schedules.schedule_start_for_rule(
        start, date_rule="first_monday_of_month", anchor_month=6
    ) == date(2026, 6, 1)
    assert schedules.schedule_start_for_rule(
        start, date_rule="first_weekday_of_month", anchor_month=None
    ) == date(2026, 5, 1)


@pytest.mark.parametrize(
    "start,recurrence,interval,expected",
    [
        (None, "monthly", 1, ""),
        (date(2026, 5, 1), "once", 1, "Once"),
        (date(2026, 5, 1), "weekly", 1, "Weekly"),
        (date(2026, 5, 1), "weekly", 2, "Every 2 weeks"),
        (date(2026, 5, 1), "monthly", 3, "Every 3 months"),
        (date(2026, 5, 1), "quarterly", 2, "Every 2 quarters"),
        (date(2026, 5, 1), "yearly", 1, "Yearly"),
        (date(2026, 5, 1), "once", 4, "Once"),
    ],
)
def test_schedule_labels_say_how_often_a_schedule_runs(
    start, recurrence, interval, expected
):
    schedule = SimpleNamespace(
        start_date=start, schedule_recurrence=recurrence, schedule_interval=interval
    )
    assert schedules.schedule_label(schedule) == expected


def test_stored_times_fall_on_the_configured_dates(monkeypatch):
    late = datetime(2026, 5, 1, 23, 30)
    monkeypatch.setattr(settings, "timezone", "Australia/Sydney")
    assert schedules.schedule_date(late) == date(2026, 5, 2)
    monkeypatch.setattr(settings, "timezone", "Etc/UTC")
    assert schedules.schedule_date(late) == date(2026, 5, 1)
    monkeypatch.setattr(schedules, "log", Mock())
    monkeypatch.setattr(settings, "timezone", "Not/A_Zone")
    assert schedules.schedule_date(late) == date(2026, 5, 1)


def _revisable(**overrides):
    values = dict(
        start_date=date(2026, 5, 4),
        schedule_recurrence="monthly",
        schedule_interval=1,
        schedule_date_rule="exact",
        schedule_anchor_month=None,
        schedule_next_run_date=date(2026, 7, 6),
        schedule_until_date=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _payload(**fields):
    names = (
        "start_date",
        "schedule_recurrence",
        "schedule_interval",
        "schedule_date_rule",
        "schedule_anchor_month",
        "schedule_until_date",
    )
    return SimpleNamespace(**{name: fields.get(name) for name in names}), set(fields)


def test_revising_a_schedule_moves_its_next_run_only_for_date_changes():
    schedule = _revisable()
    payload, fields = _payload(schedule_interval=2)
    schedules.revise_schedule(schedule, payload, fields)
    assert (schedule.schedule_interval, schedule.schedule_next_run_date) == (
        2,
        date(2026, 7, 6),
    )
    payload, fields = _payload(start_date=date(2026, 9, 1))
    schedules.revise_schedule(schedule, payload, fields)
    assert schedule.schedule_next_run_date == date(2026, 9, 1)
    payload, fields = _payload(
        schedule_date_rule="first_monday_of_month", schedule_anchor_month=10
    )
    schedules.revise_schedule(schedule, payload, fields)
    assert schedule.start_date == date(2026, 10, 5)
    assert schedule.schedule_next_run_date == date(2026, 10, 5)
    payload, fields = _payload(schedule_until_date=date(2026, 9, 1))
    assert refused(schedules.revise_schedule, schedule, payload, fields)[1] == (
        "schedule_until_date cannot be before the next run date"
    )


def test_a_schedule_may_go_without_a_start_date_only_when_allowed():
    payload, fields = _payload(start_date=None)
    assert refused(schedules.revise_schedule, _revisable(), payload, fields) == (
        400,
        "start_date is required",
    )
    schedule = _revisable()
    schedules.revise_schedule(schedule, payload, fields, start_required=False)
    assert (schedule.start_date, schedule.schedule_next_run_date) == (None, None)
    unset = _revisable(start_date=None, schedule_next_run_date=None)
    payload, fields = _payload(schedule_recurrence="weekly")
    assert refused(schedules.revise_schedule, unset, payload, fields) == (
        400,
        "start_date is required",
    )


def test_reminder_windows_need_a_lead_time_and_smtp(monkeypatch):
    monkeypatch.setattr(schedules, "smtp_configured", lambda: True)
    monkeypatch.setattr(settings, "reminder_days_before", 3)
    assert schedules.reminder_window(date(2026, 5, 1)) == (
        date(2026, 5, 1),
        date(2026, 5, 4),
    )
    monkeypatch.setattr(settings, "reminder_days_before", 0)
    assert schedules.reminder_window(date(2026, 5, 1)) is None
    monkeypatch.setattr(settings, "reminder_days_before", 3)
    monkeypatch.setattr(schedules, "smtp_configured", lambda: False)
    assert schedules.reminder_window(date(2026, 5, 1)) is None


def test_a_recipient_claimed_elsewhere_is_left_alone(contract_db):
    from app.db.models import Audit, AuditAttendee

    audit = Audit(title="A", framework_slug="A", status="open", audit_type="internal")
    contract_db.add(audit)
    contract_db.flush()
    claimed = AuditAttendee(
        audit_id=audit.id, name="Lead", reminded_at=datetime(2026, 5, 1, 9, 0)
    )
    contract_db.add(claimed)
    contract_db.commit()
    compose = Mock(return_value=True)
    sent = schedules.deliver_once(
        contract_db,
        model=AuditAttendee,
        marker="reminded_at",
        ids=[claimed.id],
        compose=compose,
    )
    assert sent == 0
    compose.assert_not_called()


def test_an_email_goes_out_with_no_transaction_open_and_a_failure_tries_again(
    contract_db,
):
    from app.db.models import Audit, AuditAttendee

    audit = Audit(title="A", framework_slug="A", status="open", audit_type="internal")
    contract_db.add(audit)
    contract_db.flush()
    rows = [
        AuditAttendee(audit_id=audit.id, name=name)
        for name in ("Lead", "No address", "Broken")
    ]
    contract_db.add_all(rows)
    contract_db.commit()
    ids = [row.id for row in rows]
    delivered = []

    def compose(attendee):
        name = attendee.name
        if name == "No address":
            return False

        def deliver():
            delivered.append((name, contract_db.in_transaction()))
            if name == "Broken":
                raise RuntimeError("mail server down")

        return deliver

    sent = schedules.deliver_once(
        contract_db, model=AuditAttendee, marker="reminded_at", ids=ids, compose=compose
    )
    assert sent == 1
    assert delivered == [("Lead", False), ("Broken", False)]
    contract_db.expire_all()
    reminded = {
        row.name: row.reminded_at for row in contract_db.query(AuditAttendee).all()
    }
    # The sent row stays claimed; the others are free for a later run.
    assert reminded["Lead"] is not None
    assert reminded["No address"] is None and reminded["Broken"] is None
