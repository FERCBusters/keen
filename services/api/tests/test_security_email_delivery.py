"""The security-email outbox worker and the address an account links for SSO.

The worker sends at most its limit of queued messages per run and leaves the
rest queued; without mail settings it reads nothing. Mail delivery is
replaced by a recorder at the outbox module.
"""

from datetime import timedelta

import pytest
from app.core import task_deadline
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import SecurityNotification, SsoEmailChallenge
from app.services import mailer, security_notifications

from tests.task_limits import checked_statements, stops_wherever_the_deadline_falls

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)


@pytest.fixture
def outbox(contract_db, monkeypatch):
    sent = []
    monkeypatch.setattr(security_notifications, "smtp_configured", lambda: True)
    monkeypatch.setattr(
        security_notifications, "send_email", lambda **mail: sent.append(mail)
    )

    class Session:
        def __enter__(self):
            return contract_db

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(security_notifications, "SessionLocal", Session)
    created = utc_now_naive() - timedelta(minutes=5)
    rows = [
        SecurityNotification(
            recipient=f"person{n}@example.test",
            subject=f"Notice {n}",
            body=f"Body {n}",
            created_at=created + timedelta(seconds=n),
            next_attempt_at=created,
        )
        for n in range(3)
    ]
    contract_db.add_all(rows)
    contract_db.commit()
    return contract_db, rows, sent


def test_a_delivery_run_stops_at_its_limit_and_leaves_the_rest_queued(outbox):
    db, rows, sent = outbox

    assert security_notifications.deliver_pending(limit=2) == 2

    # The oldest messages go first.
    assert [mail["to_email"] for mail in sent] == [
        "person0@example.test",
        "person1@example.test",
    ]
    db.expire_all()
    assert [(row.status, row.attempts, row.body) for row in rows] == [
        ("sent", 1, ""),
        ("sent", 1, ""),
        ("pending", 0, "Body 2"),
    ]
    assert security_notifications.deliver_pending(limit=2) == 1
    assert sent[-1]["to_email"] == "person2@example.test"


def test_one_recipients_backlog_does_not_hold_up_anyone_elses_mail(outbox):
    # Notification mail shares the outbox with every account's security mail.
    # A long run of messages for one recipient, queued first, takes one place
    # in each turn, so a verification link or an alert for someone else goes
    # out in the next run instead of waiting behind all of it.
    db, rows, sent = outbox
    for row in rows:
        db.delete(row)
    created = utc_now_naive() - timedelta(minutes=5)
    backlog = [
        SecurityNotification(
            recipient="owner@example.test",
            subject=f"Keen: Measure outside its threshold {n}",
            body="x",
            created_at=created + timedelta(seconds=n),
            next_attempt_at=created,
        )
        for n in range(12)
    ]
    urgent = [
        SecurityNotification(
            recipient="other@example.test",
            subject=subject,
            body="x",
            created_at=created + timedelta(seconds=20 + n),
            next_attempt_at=created,
        )
        for n, subject in enumerate(
            ["Verify your Keen SSO email address", "Keen security: password changed"]
        )
    ]
    db.add_all([*backlog, *urgent])
    db.commit()

    assert security_notifications.deliver_pending(limit=6) == 6

    # Each recipient in turn, oldest first within each; once the other
    # recipient has nothing left, the turns are all the backlog's.
    assert [(mail["to_email"], mail["subject"]) for mail in sent] == [
        ("owner@example.test", backlog[0].subject),
        ("other@example.test", urgent[0].subject),
        ("owner@example.test", backlog[1].subject),
        ("other@example.test", urgent[1].subject),
        ("owner@example.test", backlog[2].subject),
        ("owner@example.test", backlog[3].subject),
    ]
    db.expire_all()
    assert {row.status for row in urgent} == {"sent"}
    assert [row.status for row in backlog].count("pending") == 8


def test_the_worker_time_limit_is_no_failed_delivery(outbox, monkeypatch):
    # The deadline is placed at each statement and send in turn. Wherever it
    # falls, the run stops there: no message is marked as a failed attempt or
    # backed off, and one not yet sent waits, unchanged, for the next run.
    db, rows, sent = outbox

    def send(**mail):
        task_deadline.check()  # as mailer.send_email does before connecting
        sent.append(mail)

    monkeypatch.setattr(security_notifications, "send_email", send)

    def prepare():
        db.rollback()
        for row in rows:
            row.status, row.attempts, row.last_error = "pending", 0, None
            row.sent_at, row.body = None, "Body"
        db.commit()
        sent.clear()

    def after_limit():
        db.rollback()
        db.expire_all()
        delivered = {mail["to_email"] for mail in sent}
        for row in rows:
            if row.recipient in delivered:
                assert row.status in ("sent", "pending") and row.last_error is None
            else:
                assert (row.status, row.attempts, row.last_error, row.body) == (
                    "pending",
                    0,
                    None,
                    "Body",
                )

    with checked_statements(db.get_bind().engine):
        checks = stops_wherever_the_deadline_falls(
            security_notifications.deliver_pending, prepare, after_limit
        )
    # Each of the three messages was read, sent and recorded.
    assert checks >= 9 and len(sent) == 3


def test_nothing_is_read_or_sent_without_mail_settings(outbox, monkeypatch):
    db, rows, sent = outbox
    monkeypatch.setattr(security_notifications, "smtp_configured", lambda: False)

    def unused():
        raise AssertionError("the outbox was read without mail settings")

    monkeypatch.setattr(security_notifications, "SessionLocal", unused)

    assert security_notifications.deliver_pending() == 0

    assert sent == []
    db.expire_all()
    assert {(row.status, row.attempts) for row in rows} == {("pending", 0)}


@pytest.mark.parametrize(
    "address",
    ["   ", "not-an-address", "two@example.test, three@example.test"],
    ids=["blank", "not-an-address", "two-addresses"],
)
def test_a_linking_address_must_be_one_mailbox(keen, monkeypatch, address):  # noqa: F811
    monkeypatch.setattr(settings, "smtp_host", "smtp.invalid")
    monkeypatch.setattr(settings, "smtp_from_email", "keen@keen.example")
    monkeypatch.setattr(
        mailer,
        "send_email",
        lambda **kw: pytest.fail("the request contacts the mail server"),
    )
    user = keen.user()
    client = keen.client(user)  # a fresh sign-in satisfies the five-minute rule

    refused = client.post(
        "/v1/me/sso-emails/request", json={"email": address}, headers=unsafe(client)
    )

    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        "Invalid email address",
    )
    assert keen.db.query(SsoEmailChallenge).filter_by(user_id=user.id).count() == 0
    assert keen.db.query(SecurityNotification).filter_by(user_id=user.id).count() == 0
    accepted = client.post(
        "/v1/me/sso-emails/request",
        json={"email": "Person@Corp.Example"},
        headers=unsafe(client),
    )
    assert (accepted.status_code, accepted.json()) == (200, {"ok": True})
    (queued,) = keen.db.query(SecurityNotification).filter_by(user_id=user.id)
    assert queued.recipient == "person@corp.example"
