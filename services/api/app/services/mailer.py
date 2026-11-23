"""KEEN's audit and quiz emails, sent through mosp's mailer."""

from __future__ import annotations

from mosp.services.mailer import (
    build_calendar_invite,
    send_with_invite,
    sender_name,
)


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
    send_with_invite(
        to_email=to_email,
        subject=f"{sender_name()}: Audit scheduled - {audit_title}",
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
    send_with_invite(
        to_email=to_email,
        subject=f"{sender_name()}: Audit starts {scheduled_for} - {audit_title}",
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
    send_with_invite(
        to_email=to_email,
        subject=f"{sender_name()}: Quiz ready - {quiz_title}",
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
    send_with_invite(
        to_email=to_email,
        subject=f"{sender_name()}: Quiz due {due_on} - {quiz_title}",
        body=body,
        invite=_quiz_invite(
            quiz_title=quiz_title, due_on=due_on, quiz_url=quiz_url, uid=uid
        ),
        filename="keen-quiz.ics",
    )
