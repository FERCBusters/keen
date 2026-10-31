from __future__ import annotations

import re
import smtplib
import ssl
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

from app.core.config import settings


def looks_like_email(raw: str | None) -> bool:
    """Whether a stored address is plausible enough to send to."""
    value = (raw or "").strip()
    return bool(
        value
        and "@" in value
        and not value.startswith("@")
        and not value.endswith("@")
        and len(value) <= 256
    )


def user_email(user) -> str | None:
    """The address Keen emails a user at: their email, or an email username."""
    if user is None:
        return None
    email = getattr(user, "email", None)
    if looks_like_email(email):
        return str(email).strip()
    # A username that is an email address serves as the user's email. Where
    # usernames are other identifiers, administrators set the email field.
    username = getattr(user, "username", None)
    if looks_like_email(username):
        return str(username).strip()
    return None


def smtp_configured() -> bool:
    return bool(settings.smtp_host.strip()) and bool(settings.smtp_from_email.strip())


def _normalise_attachments(attachments: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    for attachment in attachments or []:
        content = attachment.get("content")
        filename = (attachment.get("filename") or "attachment").strip() or "attachment"
        maintype = (
            attachment.get("maintype") or "application"
        ).strip() or "application"
        subtype = (
            attachment.get("subtype") or "octet-stream"
        ).strip() or "octet-stream"
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not content:
            continue
        out.append(
            {
                "content": content,
                "filename": filename,
                "maintype": maintype,
                "subtype": subtype,
            }
        )
    return out


def send_email(
    *,
    to_email: str,
    subject: str,
    body: str,
    attachments: list[dict] | None = None,
) -> None:
    """Send a plain-text email using Keen's SMTP settings.

    If SMTP is not configured or the recipient is empty, this is a no-op;
    callers should decide whether failed delivery is fatal.
    """

    if not smtp_configured():
        return

    to_addr = (to_email or "").strip()
    if not to_addr:
        return

    host = settings.smtp_host.strip()
    port = settings.smtp_port or 587
    user = settings.smtp_username.strip() or None
    pwd = settings.smtp_password.strip() or None
    use_tls = settings.smtp_use_tls
    use_ssl = settings.smtp_use_ssl
    timeout = settings.smtp_timeout_seconds or 20

    if user and pwd and not (use_ssl or use_tls):
        # SMTP credentials are only sent after TLS verification succeeds.
        raise RuntimeError(
            "SMTP credentials require KEEN_SMTP_USE_TLS or KEEN_SMTP_USE_SSL"
        )

    from_email = settings.smtp_from_email.strip()
    from_name = (settings.smtp_from_name or "Keen").strip() or "Keen"

    msg = EmailMessage()
    # A header holds one line; stored titles can hold more.
    msg["Subject"] = " ".join(subject.split())
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to_addr
    msg.set_content(body)
    for attachment in _normalise_attachments(attachments):
        msg.add_attachment(
            attachment["content"],
            maintype=attachment["maintype"],
            subtype=attachment["subtype"],
            filename=attachment["filename"],
        )

    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as smtp:
            if user and pwd:
                smtp.login(user, pwd)
            smtp.send_message(msg)
        return

    with smtplib.SMTP(host, port, timeout=timeout) as smtp:
        smtp.ehlo()
        if use_tls:
            smtp.starttls(context=context)
            smtp.ehlo()
        if user and pwd:
            smtp.login(user, pwd)
        smtp.send_message(msg)


def _ics_escape(value: str | None) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _ics_fold(line: str) -> str:
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts: list[str] = []
    current = ""
    current_len = 0
    for char in line:
        char_len = len(char.encode("utf-8"))
        if current and current_len + char_len > 75:
            parts.append(current)
            current = " " + char
            current_len = 1 + char_len
        else:
            current += char
            current_len += char_len
    if current:
        parts.append(current)
    return "\r\n".join(parts)


def _ics_date(value: str | date | None, *, default: date) -> date:
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return default
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return default


def _ics_uid(value: str | None, fallback: str, prefix: str) -> str:
    raw = (value or fallback).strip()
    safe = re.sub(r"[^A-Za-z0-9._@-]+", "-", raw).strip("-") or prefix
    return f"{safe}@keen"


def build_calendar_invite(
    *,
    summary: str,
    description: str,
    starts: str | date | None,
    ends: str | date | None = None,
    url: str,
    uid: str | None = None,
    uid_prefix: str = "keen-event",
    product: str = "Schedule",
) -> str:
    """Build a small all-day iCalendar event for a Keen email attachment.

    The same ``uid`` updates the recipient's existing calendar entry, so a
    reminder and the original invitation describe one event.
    """

    start_date = _ics_date(starts, default=date.today())
    end_date = _ics_date(ends, default=start_date)
    # All-day DTEND is exclusive. Include the selected end date when one exists.
    exclusive_end = (end_date if end_date >= start_date else start_date) + timedelta(
        days=1
    )
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//Keen//{product}//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{_ics_uid(uid, f'{uid_prefix}-{summary}-{start_date.isoformat()}', uid_prefix)}",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART;VALUE=DATE:{start_date.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{exclusive_end.strftime('%Y%m%d')}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        f"URL:{_ics_escape(url)}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"


def _audit_type_label(audit_type: str | None) -> str:
    return "External" if str(audit_type or "").lower() == "external" else "Internal"


def build_audit_ics(
    *,
    audit_title: str,
    audit_type: str,
    framework_slug: str,
    scheduled_for: str,
    scheduled_until: str | None = None,
    audit_url: str,
    uid: str | None = None,
) -> str:
    """Build a small all-day iCalendar invite for an audit attendee email."""

    return build_calendar_invite(
        summary=audit_title or "Keen audit",
        description=(
            f"{_audit_type_label(audit_type)} audit for {framework_slug}. "
            f"Open audit: {audit_url}"
        ),
        starts=scheduled_for,
        ends=scheduled_until,
        url=audit_url,
        uid=uid,
        uid_prefix="keen-audit",
        product="Audit Schedule",
    )


def _sender_name() -> str:
    return (settings.smtp_from_name or "Keen").strip() or "Keen"


def _send_with_invite(
    *, to_email: str, subject: str, body: str, invite: str, filename: str
) -> None:
    send_email(
        to_email=to_email,
        subject=subject,
        body=body,
        attachments=[
            {
                "filename": filename,
                "content": invite,
                "maintype": "text",
                "subtype": "calendar",
            }
        ],
    )


def _audit_details(
    *,
    audit_title: str,
    audit_type: str,
    framework_slug: str,
    scheduled_for: str,
    scheduled_until: str | None,
) -> str:
    details = (
        f"Audit: {audit_title}\n"
        f"Type: {_audit_type_label(audit_type)}\n"
        f"Framework: {framework_slug}\n"
        f"Scheduled start date: {scheduled_for}\n"
    )
    if scheduled_until:
        details += f"Scheduled end date: {scheduled_until}\n"
    return details


def send_scheduled_audit_created_email(
    *,
    to_email: str,
    attendee_name: str | None,
    audit_title: str,
    audit_type: str,
    framework_slug: str,
    scheduled_for: str,
    scheduled_until: str | None = None,
    audit_url: str,
    uid: str | None = None,
) -> None:
    who = (attendee_name or "").strip() or "there"
    body = (
        f"Hello {who},\n\n"
        f"A scheduled audit has been created in Keen. An .ics calendar file is attached so you can add it to your calendar.\n\n"
        + _audit_details(
            audit_title=audit_title,
            audit_type=audit_type,
            framework_slug=framework_slug,
            scheduled_for=scheduled_for,
            scheduled_until=scheduled_until,
        )
        + f"\nOpen audit: {audit_url}\n\n"
        + "You are receiving this email because you were listed as an attendee on the scheduled audit template.\n"
    )
    _send_with_invite(
        to_email=to_email,
        subject=f"{_sender_name()}: Audit scheduled - {audit_title}",
        body=body,
        invite=build_audit_ics(
            audit_title=audit_title,
            audit_type=audit_type,
            framework_slug=framework_slug,
            scheduled_for=scheduled_for,
            scheduled_until=scheduled_until,
            audit_url=audit_url,
            uid=uid,
        ),
        filename="keen-audit.ics",
    )


def send_audit_reminder_email(
    *,
    to_email: str,
    attendee_name: str | None,
    audit_title: str,
    audit_type: str,
    framework_slug: str,
    scheduled_for: str,
    scheduled_until: str | None = None,
    audit_url: str,
    uid: str | None = None,
) -> None:
    who = (attendee_name or "").strip() or "there"
    body = (
        f"Hello {who},\n\n"
        f"An audit you are attending starts soon. An .ics calendar file is attached.\n\n"
        + _audit_details(
            audit_title=audit_title,
            audit_type=audit_type,
            framework_slug=framework_slug,
            scheduled_for=scheduled_for,
            scheduled_until=scheduled_until,
        )
        + f"\nOpen audit: {audit_url}\n\n"
        + "You are receiving this reminder because you are listed as an attendee on the audit.\n"
    )
    _send_with_invite(
        to_email=to_email,
        subject=f"{_sender_name()}: Audit starts {scheduled_for} - {audit_title}",
        body=body,
        invite=build_audit_ics(
            audit_title=audit_title,
            audit_type=audit_type,
            framework_slug=framework_slug,
            scheduled_for=scheduled_for,
            scheduled_until=scheduled_until,
            audit_url=audit_url,
            uid=uid,
        ),
        filename="keen-audit.ics",
    )


def _quiz_invite(*, quiz_title: str, due_on: str, quiz_url: str, uid: str) -> str:
    return build_calendar_invite(
        summary=f"Quiz due: {quiz_title}",
        description=f"Complete the Keen quiz {quiz_title} by {due_on}: {quiz_url}",
        starts=due_on,
        url=quiz_url,
        uid=uid,
        uid_prefix="keen-quiz",
        product="Quiz Schedule",
    )


def send_quiz_open_email(
    *,
    to_email: str,
    person_name: str | None,
    quiz_title: str,
    opens_on: str,
    due_on: str,
    quiz_url: str,
    uid: str,
) -> None:
    who = (person_name or "").strip() or "there"
    body = (
        f"Hello {who},\n\n"
        f"A quiz is ready for you in Keen. An .ics calendar file is attached with its due date.\n\n"
        f"Quiz: {quiz_title}\n"
        f"Open from: {opens_on}\n"
        f"Due: {due_on}\n\n"
        f"Start the quiz: {quiz_url}\n\n"
        "Your progress is saved each time you move to the next page, so you can finish it later.\n"
        "You are receiving this email because the quiz is assigned to you.\n"
    )
    _send_with_invite(
        to_email=to_email,
        subject=f"{_sender_name()}: Quiz ready - {quiz_title}",
        body=body,
        invite=_quiz_invite(
            quiz_title=quiz_title, due_on=due_on, quiz_url=quiz_url, uid=uid
        ),
        filename="keen-quiz.ics",
    )


def send_quiz_reminder_email(
    *,
    to_email: str,
    person_name: str | None,
    quiz_title: str,
    due_on: str,
    quiz_url: str,
    uid: str,
) -> None:
    who = (person_name or "").strip() or "there"
    body = (
        f"Hello {who},\n\n"
        f"A quiz assigned to you is due soon.\n\n"
        f"Quiz: {quiz_title}\n"
        f"Due: {due_on}\n\n"
        f"Continue the quiz: {quiz_url}\n\n"
        "You are receiving this reminder because the quiz is assigned to you and is not finished yet.\n"
    )
    _send_with_invite(
        to_email=to_email,
        subject=f"{_sender_name()}: Quiz due {due_on} - {quiz_title}",
        body=body,
        invite=_quiz_invite(
            quiz_title=quiz_title, due_on=due_on, quiz_url=quiz_url, uid=uid
        ),
        filename="keen-quiz.ics",
    )
