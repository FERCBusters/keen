"""Audit and quiz reminder emails, and implicit TLS without SMTP credentials.

Each message goes to the fake SMTP class from tests/test_mailer.py, so no
network is used; the tests read the message the server would have received.
"""

from app.core.config import settings
from app.services import mailer

# Imported fixture: SMTP classes replaced by a recorder, with mail configured.
from tests.test_mailer import smtp  # noqa: F401


def _sent(recorder):
    (client,) = recorder.instances
    (message,) = client.messages
    body = message.get_body(preferencelist=("plain",)).get_content()
    (invite,) = list(message.iter_attachments())
    return client, message, body, invite


def test_audit_reminders_describe_the_audit_and_attach_its_invite(smtp):  # noqa: F811
    mailer.send_audit_reminder_email(
        to_email="auditor@example.test",
        attendee_name=" ",
        audit_title="Access review",
        audit_type="external",
        framework_slug="A",
        scheduled_for="2026-04-10",
        audit_url="https://keen.example/audit.html?id=1",
        uid="keen-audit-1",
    )

    client, message, body, invite = _sent(smtp)
    assert client.recipients == ["auditor@example.test"]
    assert message["Subject"] == "Keen Alerts: Audit starts 2026-04-10 - Access review"
    assert body.startswith("Hello there,\n\nAn audit you are attending starts soon.")
    assert "Type: External\n" in body
    assert "Scheduled start date: 2026-04-10\n" in body
    # Without an end date the details name none.
    assert "Scheduled end date" not in body
    assert "Open audit: https://keen.example/audit.html?id=1" in body
    assert body.endswith("because you are listed as an attendee on the audit.\n")
    assert invite.get_filename() == "keen-audit.ics"
    ics = invite.get_content()
    # The invitation's uid, so calendars update the one entry.
    assert "UID:keen-audit-1@keen" in ics
    assert "DTSTART;VALUE=DATE:20260410" in ics
    assert "DTEND;VALUE=DATE:20260411" in ics


def test_audit_reminders_name_the_end_date_when_there_is_one(smtp):  # noqa: F811
    mailer.send_audit_reminder_email(
        to_email="auditor@example.test",
        attendee_name="Robin",
        audit_title="Access review",
        audit_type="internal",
        framework_slug="A",
        scheduled_for="2026-04-10",
        scheduled_until="2026-04-12",
        audit_url="https://keen.example/audit.html?id=1",
    )

    _, _, body, invite = _sent(smtp)
    assert body.startswith("Hello Robin,")
    assert "Scheduled end date: 2026-04-12\n" in body
    assert "DTEND;VALUE=DATE:20260413" in invite.get_content()


def test_quiz_invitations_give_the_due_date_and_a_calendar_entry(smtp):  # noqa: F811
    mailer.send_quiz_open_email(
        to_email="student@example.test",
        person_name="",
        quiz_title="Security basics",
        opens_on="2026-05-01",
        due_on="2026-05-15",
        quiz_url="https://keen.example/quiz.html?id=9",
        uid="keen-quiz-9",
    )

    client, message, body, invite = _sent(smtp)
    assert client.recipients == ["student@example.test"]
    assert message["Subject"] == "Keen Alerts: Quiz ready - Security basics"
    assert body.startswith("Hello there,\n\nA quiz is ready for you in Keen.")
    assert "Quiz: Security basics\nOpen from: 2026-05-01\nDue: 2026-05-15\n" in body
    assert "Start the quiz: https://keen.example/quiz.html?id=9" in body
    assert invite.get_filename() == "keen-quiz.ics"
    unfolded = invite.get_content().replace("\r\n ", "")
    lines = unfolded.split("\r\n")
    assert "SUMMARY:Quiz due: Security basics" in lines
    assert "UID:keen-quiz-9@keen" in lines
    assert "DTSTART;VALUE=DATE:20260515" in lines
    assert "PRODID:-//Keen//Quiz Schedule//EN" in lines
    assert (
        "DESCRIPTION:Complete the Keen quiz Security basics by 2026-05-15: "
        "https://keen.example/quiz.html?id=9"
    ) in lines


def test_quiz_reminders_reuse_the_invitations_calendar_entry(smtp):  # noqa: F811
    mailer.send_quiz_reminder_email(
        to_email="student@example.test",
        person_name=" Ada ",
        quiz_title="Security basics",
        due_on="2026-05-15",
        quiz_url="https://keen.example/quiz.html?id=9",
        uid="keen-quiz-9",
    )

    _, message, body, invite = _sent(smtp)
    assert message["Subject"] == "Keen Alerts: Quiz due 2026-05-15 - Security basics"
    assert body.startswith("Hello Ada,\n\nA quiz assigned to you is due soon.")
    assert "Continue the quiz: https://keen.example/quiz.html?id=9" in body
    assert body.endswith("is assigned to you and is not finished yet.\n")
    assert invite.get_filename() == "keen-quiz.ics"
    assert "UID:keen-quiz-9@keen" in invite.get_content()


def test_implicit_tls_without_credentials_sends_without_signing_in(
    smtp,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    monkeypatch.setattr(settings, "smtp_use_ssl", True)
    monkeypatch.setattr(settings, "smtp_port", 465)

    mailer.send_email(to_email="a@example.test", subject="S", body="B")

    (client,) = smtp.instances
    assert client.port == 465
    assert [call[0] for call in client.calls] == ["send", "quit"]
    assert client.recipients == ["a@example.test"]
