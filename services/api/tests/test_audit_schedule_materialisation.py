"""Scheduled audit templates: materialisation and audit reminders.

Materialisation runs against persisted templates (PostgreSQL when
KEEN_CONTRACT_TEST_DATABASE_URL is set) with the mailer replaced at its owning
module. The shared occurrence rules are checked in test_schedules.py.
"""

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

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


def test_run_length_is_preserved_for_each_occurrence():
    template = SimpleNamespace(start_date=date(2026, 4, 1), end_date=date(2026, 4, 3))
    assert schedules._audit_end_for_run(template, date(2026, 5, 4)) == date(2026, 5, 6)
    backwards = SimpleNamespace(start_date=date(2026, 4, 3), end_date=date(2026, 4, 1))
    assert schedules._audit_end_for_run(backwards, date(2026, 5, 4)) == date(2026, 5, 4)
    open_ended = SimpleNamespace(start_date=None, end_date=date(2026, 4, 3))
    assert schedules._audit_end_for_run(open_ended, date(2026, 5, 4)) == date(
        2026, 4, 3
    )


# --- Materialisation -----------------------------------------------------------------


@pytest.fixture
def mailer(monkeypatch):
    sent = []
    monkeypatch.setattr(schedules, "smtp_configured", lambda: True)
    monkeypatch.setattr(
        schedules, "send_scheduled_audit_created_email", lambda **kw: sent.append(kw)
    )
    monkeypatch.setattr(settings, "public_base_url", "https://keen.example")
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


def test_template_attendees_keep_their_people_records(contract_db, mailer):
    from app.db.models import IsmsPerson

    db = contract_db
    template = _persisted_template(db)
    person = IsmsPerson(name="Auditor", email="auditor@example.test")
    db.add(person)
    db.flush()
    db.add(AuditAttendee(audit_id=template.id, person_id=person.id, name="Auditor"))
    db.commit()
    (created,) = schedules.materialize_due_scheduled_audits(db, today=TODAY)["created"]
    copied = db.query(AuditAttendee).filter_by(audit_id=uuid.UUID(created["audit_id"]))
    assert [a.person_id for a in copied] == [person.id]


# --- Reminders -----------------------------------------------------------------------


@pytest.fixture
def reminders(monkeypatch):
    from app.services import schedules as shared

    sent = []
    monkeypatch.setattr(shared, "smtp_configured", lambda: True)
    monkeypatch.setattr(settings, "reminder_days_before", 2)
    monkeypatch.setattr(settings, "public_base_url", "https://keen.example")
    monkeypatch.setattr(
        schedules, "send_audit_reminder_email", lambda **kw: sent.append(kw)
    )
    return sent


def _audit(db, start, *, status="open", title="Access review"):
    audit = Audit(
        title=title,
        framework_slug="A",
        status=status,
        audit_type="internal",
        start_date=start,
        end_date=start,
    )
    db.add(audit)
    db.flush()
    return audit


def test_attendees_are_reminded_once_shortly_before_an_audit(contract_db, reminders):
    db = contract_db
    soon = _audit(db, date(2026, 4, 12))
    later = _audit(db, date(2026, 4, 20), title="Later")
    finished = _audit(db, date(2026, 4, 11), status="completed", title="Done")
    for audit in (soon, later, finished):
        db.add(AuditAttendee(audit_id=audit.id, name="Lead", email="lead@example.test"))
    # A second row for the same address shares one reminder.
    db.add(
        AuditAttendee(audit_id=soon.id, name="Lead again", email="LEAD@example.test")
    )
    db.add(AuditAttendee(audit_id=soon.id, name="No address"))
    db.commit()

    report = schedules.send_audit_reminders(db, today=TODAY)
    assert (report["enabled"], report["window_end"]) == (True, "2026-04-12")
    assert [m["audit_title"] for m in reminders] == ["Access review"]
    assert reminders[0]["uid"] == f"keen-audit-{soon.id}"
    assert reminders[0]["scheduled_for"] == "2026-04-12"
    reminded = {
        a.name: a.reminded_at is not None
        for a in db.query(AuditAttendee).filter_by(audit_id=soon.id)
    }
    assert reminded == {"Lead": True, "Lead again": True, "No address": False}
    assert schedules.send_audit_reminders(db, today=TODAY)["sent"] == 0
    assert len(reminders) == 1


def test_a_failed_reminder_is_tried_again(contract_db, reminders, monkeypatch):
    db = contract_db
    audit = _audit(db, date(2026, 4, 11))
    db.add(AuditAttendee(audit_id=audit.id, name="Lead", email="lead@example.test"))
    db.add(AuditAttendee(audit_id=audit.id, name="Again", email="lead@example.test"))
    db.commit()
    monkeypatch.setattr(
        schedules,
        "send_audit_reminder_email",
        Mock(side_effect=RuntimeError("relay down")),
    )
    assert schedules.send_audit_reminders(db, today=TODAY)["sent"] == 0
    assert {a.reminded_at for a in db.query(AuditAttendee)} == {None}
    monkeypatch.setattr(
        schedules, "send_audit_reminder_email", lambda **kw: reminders.append(kw)
    )
    assert schedules.send_audit_reminders(db, today=TODAY)["sent"] == 2
    assert len(reminders) == 1


def test_an_attendee_whose_account_is_deactivated_gets_no_reminder(
    contract_db, reminders
):
    db = contract_db
    audit = _audit(db, date(2026, 4, 11))
    gone = User(
        username="gone-attendee",
        password_hash="x",
        role="normal",
        is_active=False,
        email="gone@example.test",
    )
    db.add(gone)
    db.flush()
    db.add(
        AuditAttendee(
            audit_id=audit.id, name="Gone", email="gone@example.test", user_id=gone.id
        )
    )
    db.add(AuditAttendee(audit_id=audit.id, name="Guest", email="guest@example.test"))
    db.commit()
    assert schedules.send_audit_reminders(db, today=TODAY)["sent"] == 1
    assert [m["to_email"] for m in reminders] == ["guest@example.test"]


def test_reminders_need_smtp_a_lead_time_and_audits(
    contract_db, reminders, monkeypatch
):
    from app.services import schedules as shared

    db = contract_db
    monkeypatch.setattr(settings, "reminder_days_before", 0)
    assert schedules.send_audit_reminders(db, today=TODAY) == {
        "enabled": False,
        "sent": 0,
    }
    monkeypatch.setattr(settings, "reminder_days_before", 2)
    monkeypatch.setattr(shared, "smtp_configured", lambda: False)
    assert schedules.send_audit_reminders(db, today=TODAY)["enabled"] is False
    monkeypatch.setattr(shared, "smtp_configured", lambda: True)
    db.add(ManagedConfiguration(name="features", version=1, document={"audits": False}))
    db.commit()
    assert schedules.send_audit_reminders(db, today=TODAY)["enabled"] is False
