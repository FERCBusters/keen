"""Outbound mail: SMTP transport rules, attachments and audit calendar invites.

The SMTP classes are replaced at their owning module, so no network is used.
"""

import ssl
from datetime import date

import pytest
from app.core.config import settings
from app.services import mailer


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls: list[tuple] = []
        self.messages = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append(("quit",))
        return False

    def ehlo(self):
        self.calls.append(("ehlo",))

    def starttls(self, context=None):
        self.calls.append(("starttls", context))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.calls.append(("send",))
        self.messages.append(message)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    for name, value in {
        "smtp_host": "smtp.example.test",
        "smtp_port": 587,
        "smtp_from_email": "keen@example.test",
        "smtp_from_name": "KEEN Alerts",
        "smtp_username": "",
        "smtp_password": "",
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
        "smtp_timeout_seconds": 7,
    }.items():
        monkeypatch.setattr(settings, name, value)
    return FakeSMTP


def test_nothing_is_sent_without_configuration_or_a_recipient(smtp, monkeypatch):
    mailer.send_email(to_email="  ", subject="S", body="B")
    assert smtp.instances == []
    monkeypatch.setattr(settings, "smtp_host", " ")
    assert mailer.smtp_configured() is False
    mailer.send_email(to_email="a@example.test", subject="S", body="B")
    assert smtp.instances == []


def test_starttls_precedes_login_and_sending(smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_username", "mailer")
    monkeypatch.setattr(settings, "smtp_password", "synthetic-secret")
    mailer.send_email(
        to_email=" auditor@example.test ",
        subject="Audit scheduled",
        body="Hello",
        attachments=[
            {
                "filename": "notes.txt",
                "content": "text",
                "maintype": "text",
                "subtype": "plain",
            },
            {"filename": "empty.bin", "content": b""},
            {"content": b"\x00\x01"},
        ],
    )
    (client,) = smtp.instances
    assert (client.host, client.port, client.timeout) == ("smtp.example.test", 587, 7)
    names = [call[0] for call in client.calls]
    assert names == ["ehlo", "starttls", "ehlo", "login", "send", "quit"]
    assert isinstance(client.calls[1][1], ssl.SSLContext)
    assert client.calls[1][1].verify_mode == ssl.CERT_REQUIRED
    assert client.calls[1][1].check_hostname is True
    (message,) = client.messages
    assert message["From"] == "KEEN Alerts <keen@example.test>"
    assert message["To"] == "auditor@example.test"
    assert message["Subject"] == "Audit scheduled"
    attachments = list(message.iter_attachments())
    # Empty attachments are skipped; missing names and types get safe defaults.
    assert [(a.get_filename(), a.get_content_type()) for a in attachments] == [
        ("notes.txt", "text/plain"),
        ("attachment", "application/octet-stream"),
    ]


def test_implicit_tls_uses_a_verifying_context(smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_use_ssl", True)
    monkeypatch.setattr(settings, "smtp_port", 465)
    monkeypatch.setattr(settings, "smtp_username", "mailer")
    monkeypatch.setattr(settings, "smtp_password", "synthetic-secret")
    mailer.send_email(to_email="a@example.test", subject="S", body="B")
    (client,) = smtp.instances
    assert client.port == 465 and client.context.verify_mode == ssl.CERT_REQUIRED
    assert [call[0] for call in client.calls] == ["login", "send", "quit"]


def test_credentials_are_never_sent_without_tls(smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_use_tls", False)
    monkeypatch.setattr(settings, "smtp_username", "mailer")
    monkeypatch.setattr(settings, "smtp_password", "synthetic-secret")
    with pytest.raises(RuntimeError, match="require KEEN_SMTP_USE_TLS"):
        mailer.send_email(to_email="a@example.test", subject="S", body="B")
    assert smtp.instances == []

    # Without credentials, plain relays are still allowed.
    monkeypatch.setattr(settings, "smtp_username", "")
    mailer.send_email(to_email="a@example.test", subject="S", body="B")
    (client,) = smtp.instances
    assert [call[0] for call in client.calls] == ["ehlo", "send", "quit"]


def test_header_injection_is_refused_by_the_message_builder(smtp):
    with pytest.raises(ValueError):
        mailer.send_email(
            to_email="a@example.test\r\nBcc: other@example.test", subject="S", body="B"
        )
    with pytest.raises(ValueError):
        mailer.send_email(
            to_email="a@example.test",
            subject="Audit\nBcc: other@example.test",
            body="B",
        )


def test_audit_invites_are_all_day_escaped_and_folded():
    ics = mailer.build_audit_ics(
        audit_title="Review; scope, ",
        audit_type="EXTERNAL",
        framework_slug="A",
        scheduled_for="2026-04-10T09:00:00",
        scheduled_until="2026-04-12",
        audit_url="https://keen.example/audit.html?id=1",
        uid="keen-audit-<1>",
    )
    assert ics.endswith("\r\n")
    unfolded = ics.replace("\r\n ", "")
    lines = unfolded.split("\r\n")
    assert "DTSTART;VALUE=DATE:20260410" in lines
    assert "DTEND;VALUE=DATE:20260413" in lines  # all-day end is exclusive
    assert "SUMMARY:Review\\; scope\\, " in lines
    assert "UID:keen-audit--1@keen" in lines
    assert "URL:https://keen.example/audit.html?id=1" in lines
    description = next(line for line in lines if line.startswith("DESCRIPTION:"))
    assert description.startswith("DESCRIPTION:External audit for A.")
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))


def test_invite_dates_fall_back_safely():
    ics = mailer.build_audit_ics(
        audit_title="",
        audit_type="internal",
        framework_slug="A",
        scheduled_for="not-a-date",
        scheduled_until="2000-01-01",
    )
    today = date.today().strftime("%Y%m%d")
    assert f"DTSTART;VALUE=DATE:{today}" in ics
    # An end before the start becomes a single day.
    assert "SUMMARY:Keen audit" in ics and "URL:" not in ics
    assert mailer._ics_date(date(2026, 1, 2), default=date(2000, 1, 1)) == date(
        2026, 1, 2
    )
    assert mailer._ics_date("", default=date(2000, 1, 1)) == date(2000, 1, 1)
    assert (
        mailer._ics_uid(None, "Audit / 1", date(2026, 1, 2))
        == "keen-audit-Audit-1-2026-01-02@keen"
    )
    assert mailer._ics_uid("!!!", "x", date(2026, 1, 2)) == "keen-audit@keen"


def test_long_multibyte_lines_fold_on_character_boundaries():
    line = "SUMMARY:" + "é" * 80
    folded = mailer._ics_fold(line)
    parts = folded.split("\r\n")
    assert all(len(part.encode()) <= 75 for part in parts)
    assert "".join(part[1:] if i else part for i, part in enumerate(parts)) == line
    assert mailer._ics_escape("a\\b\r\nc\rd") == "a\\\\b\\nc\\nd"


def test_scheduled_audit_emails_attach_a_calendar_invite(smtp):
    mailer.send_scheduled_audit_created_email(
        to_email="auditor@example.test",
        attendee_name="  ",
        audit_title="Access review",
        audit_type="internal",
        framework_slug="A",
        scheduled_for="2026-04-10",
        scheduled_until="2026-04-11",
        audit_url="https://keen.example/audit.html?id=1",
        uid="keen-audit-1",
    )
    (message,) = smtp.instances[0].messages
    assert message["Subject"] == "KEEN Alerts: Audit scheduled - Access review"
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert body.startswith("Hello there,")
    assert "Scheduled end date: 2026-04-11" in body
    assert "Open audit: https://keen.example/audit.html?id=1" in body
    (invite,) = list(message.iter_attachments())
    assert invite.get_filename() == "keen-audit.ics"
    assert invite.get_content_type() == "text/calendar"
    assert "UID:keen-audit-1@keen" in invite.get_content()
