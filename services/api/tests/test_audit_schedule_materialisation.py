"""Scheduled audit templates: occurrence rules and materialisation.

Occurrence rules are checked directly. Materialisation runs against persisted
templates (PostgreSQL when KEEN_CONTRACT_TEST_DATABASE_URL is set) with the
mailer replaced at its owning module.
"""

import uuid
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from app.core.config import settings
from app.db.models import (
    Audit,
    AuditAttendee,
    AuditScopedClause,
    AuditScopedControl,
    ControlItem,
    FrameworkClause,
    ManagedConfiguration,
    User,
)
from app.services import audit_schedules as schedules

TODAY = date(2026, 4, 10)  # a Friday


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
    assert schedules.first_weekday_of_month(2026, 2) == date(
        2026, 2, 2
    )  # 1 Feb is a Sunday
    assert schedules.first_weekday_of_month(2026, 4) == date(2026, 4, 1)
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


def test_run_length_is_preserved_for_each_occurrence():
    template = SimpleNamespace(start_date=date(2026, 4, 1), end_date=date(2026, 4, 3))
    assert schedules._audit_end_for_run(template, date(2026, 5, 4)) == date(2026, 5, 6)
    backwards = SimpleNamespace(start_date=date(2026, 4, 3), end_date=date(2026, 4, 1))
    assert schedules._audit_end_for_run(backwards, date(2026, 5, 4)) == date(2026, 5, 4)
    open_ended = SimpleNamespace(start_date=None, end_date=date(2026, 4, 3))
    assert schedules._audit_end_for_run(open_ended, date(2026, 5, 4)) == date(
        2026, 4, 3
    )


def test_schedule_dates_use_the_configured_timezone(monkeypatch):
    monkeypatch.setattr(settings, "timezone", "Pacific/Kiritimati")
    assert (
        schedules._today_for_schedule()
        == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    )
    log = Mock()
    monkeypatch.setattr(schedules, "log", log)
    monkeypatch.setattr(settings, "timezone", "Not/A_Zone")
    assert schedules._today_for_schedule() == date.today()
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
    assert schedules._looks_like_email(value) is expected


# --- Materialisation -----------------------------------------------------------------


@pytest.fixture
def mailer(monkeypatch):
    sent = []
    monkeypatch.setattr(schedules, "smtp_configured", lambda: True)
    monkeypatch.setattr(
        schedules, "send_scheduled_audit_created_email", lambda **kw: sent.append(kw)
    )
    monkeypatch.setattr(settings, "public_base_url", "https://keen.example/")
    monkeypatch.setattr(settings, "scheduled_audit_create_days_ahead", 0)
    return sent


def _persisted_template(db, **kw):
    owner = User(
        username="owner-" + uuid.uuid4().hex[:8], password_hash="unused", is_active=True
    )
    db.add(owner)
    db.flush()
    defaults = dict(
        title="Monthly access review",
        framework_slug="A",
        status="template",
        audit_type="internal",
        start_date=date(2026, 3, 10),
        end_date=date(2026, 3, 12),
        schedule_recurrence="monthly",
        schedule_interval=1,
        schedule_date_rule="exact",
        schedule_next_run_date=TODAY,
        created_by_user_id=owner.id,
        notes="Review admin accounts",
        meta={"origin": "test"},
    )
    defaults.update(kw)
    template = Audit(**defaults)
    db.add(template)
    db.flush()
    return template


def test_due_templates_create_one_audit_with_scope_and_attendees(contract_db, mailer):
    db = contract_db
    template = _persisted_template(db)
    control = ControlItem(
        framework_slug="A", ref="A.5.15", type="annex_control", title="Access"
    )
    clause = FrameworkClause(framework_slug="A", ref="9.2", title="Internal audit")
    with_email = User(
        username="lead",
        email="Lead@Example.test",
        password_hash="unused",
        is_active=True,
    )
    username_email = User(
        username="second@example.test", password_hash="unused", is_active=True
    )
    inactive = User(
        username="gone@example.test", password_hash="unused", is_active=False
    )
    db.add_all([control, clause, with_email, username_email, inactive])
    db.flush()
    db.add_all(
        [
            AuditScopedControl(audit_id=template.id, control_item_id=control.id),
            AuditScopedClause(audit_id=template.id, clause_id=clause.id),
            AuditAttendee(
                audit_id=template.id,
                user_id=with_email.id,
                name="Lead",
                role="Lead auditor",
            ),
            AuditAttendee(
                audit_id=template.id, user_id=username_email.id, name="Second"
            ),
            AuditAttendee(
                audit_id=template.id, name="External", email="lead@example.test"
            ),
            AuditAttendee(audit_id=template.id, user_id=inactive.id, name="Former"),
            AuditAttendee(audit_id=template.id, name="No address"),
        ]
    )
    db.commit()

    result = schedules.materialize_due_scheduled_audits(db, today=TODAY)
    assert result["checked"] == 1 and result["skipped_past"] == []
    (created,) = result["created"]
    assert created["run_date"] == "2026-04-10"
    audit = db.get(Audit, uuid.UUID(created["audit_id"]))
    assert audit.title == "Monthly access review (2026-04-10)"
    assert (audit.status, audit.start_date, audit.end_date) == (
        "open",
        TODAY,
        date(2026, 4, 12),
    )
    assert audit.meta["scheduled_from_template_id"] == str(template.id)
    assert audit.meta["origin"] == "test"
    assert audit.notes == "Review admin accounts"
    assert [
        s.control_item_id
        for s in db.query(AuditScopedControl).filter_by(audit_id=audit.id)
    ] == [control.id]
    assert [
        s.clause_id for s in db.query(AuditScopedClause).filter_by(audit_id=audit.id)
    ] == [clause.id]
    copied = {
        a.name: a.email for a in db.query(AuditAttendee).filter_by(audit_id=audit.id)
    }
    assert copied == {
        "Lead": "Lead@Example.test",
        "Second": "second@example.test",
        "External": "lead@example.test",
        "Former": None,
        "No address": None,
    }

    # One message per distinct address; inactive and missing addresses are skipped.
    assert created["emails_sent"] == 2
    assert sorted(m["to_email"].lower() for m in mailer) == [
        "lead@example.test",
        "second@example.test",
    ]
    message = mailer[0]
    assert message["audit_url"] == f"https://keen.example/audit.html?id={audit.id}"
    assert (message["scheduled_for"], message["scheduled_until"]) == (
        "2026-04-10",
        "2026-04-12",
    )
    assert message["uid"] == f"keen-audit-{audit.id}"

    db.refresh(template)
    assert template.schedule_last_run_date == TODAY
    assert template.schedule_last_created_audit_id == audit.id
    # 10 May 2026 is a Sunday, so the next run is Friday 8 May.
    assert template.schedule_next_run_date == date(2026, 5, 8)

    # Running again the same day creates nothing.
    again = schedules.materialize_due_scheduled_audits(db, today=TODAY)
    assert (again["checked"], again["created"]) == (0, [])
    assert db.query(Audit).filter(Audit.status == "open").count() == 1


def test_missed_runs_are_skipped_without_backfilling(contract_db, mailer):
    db = contract_db
    template = _persisted_template(
        db, schedule_recurrence="weekly", schedule_next_run_date=date(2026, 3, 20)
    )
    db.commit()
    result = schedules.materialize_due_scheduled_audits(db, today=TODAY)
    assert result["skipped_past"] == [
        {
            "template_id": str(template.id),
            "skipped_run_dates": ["2026-03-20", "2026-03-27", "2026-04-03"],
            "next_run_date": "2026-04-10",
        }
    ]
    # The occurrence falling today is still created.
    assert [c["run_date"] for c in result["created"]] == ["2026-04-10"]
    db.refresh(template)
    assert template.schedule_next_run_date == date(2026, 4, 17)


def test_missed_runs_beyond_the_window_wait_for_their_date(contract_db, mailer):
    db = contract_db
    template = _persisted_template(
        db,
        start_date=date(2026, 3, 20),
        end_date=date(2026, 3, 22),
        schedule_recurrence="monthly",
        schedule_next_run_date=date(2026, 3, 20),
    )
    db.commit()
    result = schedules.materialize_due_scheduled_audits(db, today=TODAY)
    assert result["created"] == []
    assert result["skipped_past"][0]["next_run_date"] == "2026-04-20"
    db.refresh(template)
    assert template.schedule_next_run_date == date(2026, 4, 20)


def test_ended_schedules_stop_after_skipping_missed_runs(contract_db, mailer):
    db = contract_db
    template = _persisted_template(
        db,
        schedule_recurrence="weekly",
        schedule_next_run_date=date(2026, 3, 20),
        schedule_until_date=date(2026, 4, 1),
    )
    db.commit()
    result = schedules.materialize_due_scheduled_audits(db, today=TODAY)
    assert result["created"] == []
    assert result["skipped_past"][0]["next_run_date"] is None
    db.refresh(template)
    assert template.schedule_next_run_date is None


def test_the_look_ahead_window_creates_upcoming_audits(
    contract_db, mailer, monkeypatch
):
    monkeypatch.setattr(settings, "scheduled_audit_create_days_ahead", 7)
    db = contract_db
    soon = _persisted_template(
        db, title="Soon", schedule_next_run_date=date(2026, 4, 15)
    )
    _persisted_template(db, title="Later", schedule_next_run_date=date(2026, 4, 20))
    db.commit()
    result = schedules.materialize_due_scheduled_audits(db, today=TODAY)
    assert result["create_window_end"] == "2026-04-17"
    assert [(c["template_id"], c["run_date"]) for c in result["created"]] == [
        (str(soon.id), "2026-04-15")
    ]


def test_an_already_run_date_only_advances_the_template(contract_db, mailer):
    db = contract_db
    template = _persisted_template(db, schedule_last_run_date=TODAY)
    db.commit()
    result = schedules.materialize_due_scheduled_audits(db, today=TODAY)
    assert result["created"] == []
    db.refresh(template)
    # 10 May 2026 is a Sunday, so the next run is Friday 8 May.
    assert template.schedule_next_run_date == date(2026, 5, 8)
    assert db.query(Audit).filter(Audit.status == "open").count() == 0


def test_a_month_end_run_schedules_the_next_month_end(contract_db, mailer):
    db = contract_db
    template = _persisted_template(
        db,
        start_date=date(2026, 3, 31),
        end_date=date(2026, 3, 31),
        schedule_next_run_date=date(2026, 4, 30),
    )
    db.commit()
    result = schedules.materialize_due_scheduled_audits(db, today=date(2026, 4, 30))
    assert [c["run_date"] for c in result["created"]] == ["2026-04-30"]
    db.refresh(template)
    # 31 May 2026 is a Sunday, so the next run is Friday 29 May.
    assert template.schedule_next_run_date == date(2026, 5, 29)


def test_a_drifted_schedule_returns_to_its_anchor_day(contract_db, mailer):
    db = contract_db
    # Saved by earlier builds, which carried February's 28th into March.
    template = _persisted_template(
        db,
        start_date=date(2026, 1, 31),
        end_date=date(2026, 1, 31),
        schedule_next_run_date=date(2026, 3, 28),
    )
    db.commit()
    result = schedules.materialize_due_scheduled_audits(db, today=date(2026, 3, 28))
    assert [c["run_date"] for c in result["created"]] == ["2026-03-28"]
    db.refresh(template)
    assert template.schedule_next_run_date == date(2026, 4, 30)


def test_mail_failures_are_logged_without_stopping_creation(contract_db, monkeypatch):
    db = contract_db
    monkeypatch.setattr(settings, "scheduled_audit_create_days_ahead", 0)
    monkeypatch.setattr(schedules, "smtp_configured", lambda: True)
    monkeypatch.setattr(
        schedules,
        "send_scheduled_audit_created_email",
        Mock(side_effect=RuntimeError("relay down")),
    )
    log = Mock()
    monkeypatch.setattr(schedules, "log", log)
    template = _persisted_template(db)
    db.add(
        AuditAttendee(audit_id=template.id, name="External", email="ext@example.test")
    )
    db.commit()
    result = schedules.materialize_due_scheduled_audits(db, today=TODAY)
    assert result["created"][0]["emails_sent"] == 0
    assert log.exception.called


def test_no_mail_is_attempted_without_smtp(contract_db, monkeypatch):
    db = contract_db
    monkeypatch.setattr(settings, "scheduled_audit_create_days_ahead", 0)
    monkeypatch.setattr(settings, "public_base_url", "")
    monkeypatch.setattr(schedules, "smtp_configured", lambda: False)
    send = Mock()
    monkeypatch.setattr(schedules, "send_scheduled_audit_created_email", send)
    template = _persisted_template(db)
    db.add(
        AuditAttendee(audit_id=template.id, name="External", email="ext@example.test")
    )
    db.commit()
    result = schedules.materialize_due_scheduled_audits(db, today=TODAY)
    assert result["created"][0]["emails_sent"] == 0
    assert not send.called
    assert schedules._audit_url(uuid.uuid4()) is None


def test_scheduling_can_be_switched_off(contract_db, mailer):
    db = contract_db
    _persisted_template(db)
    db.add(
        ManagedConfiguration(
            name="features", version=1, document={"audit_scheduling": False}
        )
    )
    db.commit()
    assert schedules.materialize_due_scheduled_audits(db, today=TODAY) == {
        "enabled": False,
        "created": [],
        "skipped_past": [],
    }
    assert db.query(Audit).filter(Audit.status == "open").count() == 0
