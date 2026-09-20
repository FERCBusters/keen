from __future__ import annotations

import re
import smtplib
import ssl
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

from app.core.config import settings


def smtp_configured() -> bool:
    return bool((settings.smtp_host or "").strip()) and bool(
        (settings.smtp_from_email or "").strip()
    )


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

    host = (settings.smtp_host or "").strip()
    port = int(getattr(settings, "smtp_port", 587) or 587)
    user = (settings.smtp_username or "").strip() or None
    pwd = (settings.smtp_password or "").strip() or None
    use_tls = bool(getattr(settings, "smtp_use_tls", True))
    use_ssl = bool(getattr(settings, "smtp_use_ssl", False))
    timeout = int(getattr(settings, "smtp_timeout_seconds", 20) or 20)

    from_email = (settings.smtp_from_email or "").strip()
    from_name = (settings.smtp_from_name or "Keen").strip() or "Keen"

    msg = EmailMessage()
    msg["Subject"] = subject
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


def _ics_uid(value: str | None, title: str, start_date: date) -> str:
    raw = (value or f"keen-audit-{title}-{start_date.isoformat()}").strip()
    safe = re.sub(r"[^A-Za-z0-9._@-]+", "-", raw).strip("-") or "keen-audit"
    return f"{safe}@keen"


def build_audit_ics(
    *,
    audit_title: str,
    audit_type: str,
    framework_slug: str,
    scheduled_for: str,
    scheduled_until: str | None = None,
    audit_url: str | None = None,
    uid: str | None = None,
) -> str:
    """Build a small all-day iCalendar invite for an audit attendee email."""

    start_date = _ics_date(scheduled_for, default=date.today())
    end_date = _ics_date(scheduled_until, default=start_date)
    # All-day DTEND is exclusive. Include the selected end date when one exists.
    exclusive_end = (end_date if end_date >= start_date else start_date) + timedelta(
        days=1
    )
    type_label = (
        "External" if str(audit_type or "").lower() == "external" else "Internal"
    )
    description = f"{type_label} audit for {framework_slug}."
    if audit_url:
        description += f" Open audit: {audit_url}"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Keen//Audit Schedule//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{_ics_uid(uid, audit_title, start_date)}",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART;VALUE=DATE:{start_date.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{exclusive_end.strftime('%Y%m%d')}",
        f"SUMMARY:{_ics_escape(audit_title or 'Keen audit')}",
        f"DESCRIPTION:{_ics_escape(description)}",
    ]
    if audit_url:
        lines.append(f"URL:{_ics_escape(audit_url)}")
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"


def send_scheduled_audit_created_email(
    *,
    to_email: str,
    attendee_name: str | None,
    audit_title: str,
    audit_type: str,
    framework_slug: str,
    scheduled_for: str,
    scheduled_until: str | None = None,
    audit_url: str | None = None,
    uid: str | None = None,
) -> None:
    from_name = (settings.smtp_from_name or "Keen").strip() or "Keen"
    subject = f"{from_name}: Audit scheduled - {audit_title}"
    who = (attendee_name or "").strip() or "there"
    type_label = (
        "External" if str(audit_type or "").lower() == "external" else "Internal"
    )

    body = (
        f"Hello {who},\n\n"
        f"A scheduled audit has been created in Keen. An .ics calendar file is attached so you can add it to your calendar.\n\n"
        f"Audit: {audit_title}\n"
        f"Type: {type_label}\n"
        f"Framework: {framework_slug}\n"
        f"Scheduled start date: {scheduled_for}\n"
    )
    if scheduled_until:
        body += f"Scheduled end date: {scheduled_until}\n"
    body += "\n"
    if audit_url:
        body += f"Open audit: {audit_url}\n\n"
    body += "You are receiving this email because you were listed as an attendee on the scheduled audit template.\n"

    ics = build_audit_ics(
        audit_title=audit_title,
        audit_type=audit_type,
        framework_slug=framework_slug,
        scheduled_for=scheduled_for,
        scheduled_until=scheduled_until,
        audit_url=audit_url,
        uid=uid,
    )
    send_email(
        to_email=to_email,
        subject=subject,
        body=body,
        attachments=[
            {
                "filename": "keen-audit.ics",
                "content": ics,
                "maintype": "text",
                "subtype": "calendar",
            }
        ],
    )
