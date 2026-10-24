"""Scheduled audits and audit reminders.

A scheduled audit is an Audit row with status 'template' whose schedule
follows app.services.schedules. Each due run creates an open audit with the
template's scope and attendees and emails the attendees. Each attendee of an
open audit is reminded once, shortly before the audit starts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    Audit,
    AuditAttendee,
    AuditScopedClause,
    AuditScopedControl,
    AuditScopedIsmsDocument,
    AuditSwotScope,
    User,
)
from app.services.mailer import (
    looks_like_email,
    send_audit_reminder_email,
    send_scheduled_audit_created_email,
    smtp_configured,
    user_email,
)
from app.services.schedules import (
    deliver_once,
    materialise_due,
    reminder_window,
    today_for_schedule,
)

log = logging.getLogger(__name__)

# Audits that have not finished yet; their attendees are due reminders.
REMINDER_AUDIT_STATUSES = ("open", "in_progress")


def _attendee_notification_email(db: Session, attendee: AuditAttendee) -> str | None:
    """The attendee's address; an attendee whose account is deactivated gets none."""
    user = db.get(User, attendee.user_id) if attendee.user_id else None
    if user is not None and not user.is_active:
        return None
    email = str(attendee.email or "").strip()
    if looks_like_email(email):
        return email
    return user_email(user) if user is not None else None


def _audit_url(audit_id: uuid.UUID) -> str:
    return f"{settings.public_base_url}/audit.html?id={audit_id}"


def _audit_end_for_run(template: Audit, run_date: date) -> date | None:
    if not template.start_date or not template.end_date:
        return template.end_date
    if template.end_date < template.start_date:
        return run_date
    return run_date + (template.end_date - template.start_date)


def _copy_template_scope_and_attendees(
    db: Session, *, template: Audit, audit: Audit
) -> None:
    now = utc_now_naive()
    controls = (
        db.query(AuditScopedControl.control_item_id)
        .filter(AuditScopedControl.audit_id == template.id)
        .all()
    )
    for (control_id,) in controls:
        db.add(
            AuditScopedControl(
                audit_id=audit.id,
                control_item_id=control_id,
                created_at=now,
            )
        )

    clauses = (
        db.query(AuditScopedClause.clause_id)
        .filter(AuditScopedClause.audit_id == template.id)
        .all()
    )
    for (clause_id,) in clauses:
        db.add(
            AuditScopedClause(
                audit_id=audit.id,
                clause_id=clause_id,
                created_at=now,
            )
        )

    for (swot_id,) in (
        db.query(AuditSwotScope.swot_id)
        .filter(AuditSwotScope.audit_id == template.id)
        .all()
    ):
        db.add(AuditSwotScope(audit_id=audit.id, swot_id=swot_id))

    documents = (
        db.query(AuditScopedIsmsDocument.document_id)
        .filter(AuditScopedIsmsDocument.audit_id == template.id)
        .all()
    )
    for (document_id,) in documents:
        db.add(
            AuditScopedIsmsDocument(
                audit_id=audit.id,
                document_id=document_id,
                created_at=now,
            )
        )

    attendees = (
        db.query(AuditAttendee)
        .filter(AuditAttendee.audit_id == template.id)
        .order_by(AuditAttendee.created_at.asc())
        .all()
    )
    for attendee in attendees:
        db.add(
            AuditAttendee(
                audit_id=audit.id,
                user_id=attendee.user_id,
                person_id=attendee.person_id,
                name=attendee.name,
                email=_attendee_notification_email(db, attendee),
                role=attendee.role,
                created_at=now,
            )
        )


def _audit_email_fields(audit: Audit) -> dict[str, Any]:
    return {
        "audit_title": audit.title,
        "audit_type": audit.audit_type or "internal",
        "framework_slug": audit.framework_slug,
        "scheduled_for": audit.start_date.isoformat() if audit.start_date else "today",
        "scheduled_until": audit.end_date.isoformat() if audit.end_date else None,
        "audit_url": _audit_url(audit.id),
        "uid": f"keen-audit-{audit.id}",
    }


def _email_attendees(db: Session, *, audit: Audit) -> int:
    if not smtp_configured():
        return 0
    attendees = (
        db.query(AuditAttendee)
        .filter(AuditAttendee.audit_id == audit.id)
        .order_by(AuditAttendee.created_at.asc())
        .all()
    )
    sent = 0
    seen: set[str] = set()
    for attendee in attendees:
        email = _attendee_notification_email(db, attendee)
        if not email:
            log.info(
                "Skipping scheduled audit attendee without email: attendee_id=%s audit_id=%s user_id=%s",
                attendee.id,
                audit.id,
                attendee.user_id,
            )
            continue
        if email.lower() in seen:
            continue
        seen.add(email.lower())
        try:
            send_scheduled_audit_created_email(
                to_email=email,
                attendee_name=attendee.name,
                **_audit_email_fields(audit),
            )
            sent += 1
        except Exception:
            log.exception(
                "Failed to email scheduled audit attendee %s for audit_id=%s",
                email,
                audit.id,
            )
    return sent


def _audit_run(db: Session):
    """Return the function that creates one template's audit for a run date."""

    def create(template: Audit, run_date: date) -> Audit:
        now = utc_now_naive()
        audit = Audit(
            title=f"{template.title} ({run_date.isoformat()})",
            framework_slug=template.framework_slug,
            status="open",
            audit_type=template.audit_type or "internal",
            start_date=run_date,
            end_date=_audit_end_for_run(template, run_date),
            created_by_user_id=template.created_by_user_id,
            created_at=now,
            updated_at=now,
            notes=template.notes or "",
            executive_summary=template.executive_summary or "",
            meta={
                **(template.meta or {}),
                "scheduled_from_template_id": str(template.id),
                "scheduled_run_date": run_date.isoformat(),
            },
        )
        db.add(audit)
        db.flush()
        _copy_template_scope_and_attendees(db, template=template, audit=audit)
        template.schedule_last_created_audit_id = audit.id
        return audit

    return create


def materialize_due_scheduled_audits(
    db: Session, *, today: date | None = None
) -> dict[str, Any]:
    from app.services.features import state

    if not state(db, refresh=True).enabled("audit_scheduling"):
        return {"enabled": False, "created": [], "skipped_past": []}
    run_today = today or today_for_schedule()
    days_ahead = max(0, int(settings.scheduled_audit_create_days_ahead or 0))
    latest_create_date = run_today + timedelta(days=days_ahead)
    checked, created, skipped = materialise_due(
        db,
        model=Audit,
        filters=[Audit.status == "template"],
        run_today=run_today,
        latest_create_date=latest_create_date,
        create_run=_audit_run(db),
        after_commit=lambda template_id, audit: {
            "template_id": str(template_id),
            "audit_id": str(audit.id),
            "run_date": audit.start_date.isoformat(),
            "emails_sent": _email_attendees(db, audit=audit),
        },
    )
    return {
        "checked": checked,
        "created": created,
        "skipped_past": [
            {"template_id": str(template_id), **record}
            for template_id, record in skipped
        ],
        "create_days_ahead": days_ahead,
        "create_window_start": run_today.isoformat(),
        "create_window_end": latest_create_date.isoformat(),
    }


def send_audit_reminders(db: Session, *, today: date | None = None) -> dict[str, Any]:
    """Remind each attendee of an open audit once before it starts."""
    from app.services.features import state

    window = reminder_window(today)
    if window is None or not state(db, refresh=True).enabled("audits"):
        return {"enabled": False, "sent": 0}
    first, last = window
    ids = [
        attendee_id
        for (attendee_id,) in db.query(AuditAttendee.id)
        .join(Audit, Audit.id == AuditAttendee.audit_id)
        .filter(
            Audit.status.in_(REMINDER_AUDIT_STATUSES),
            Audit.start_date.isnot(None),
            Audit.start_date >= first,
            Audit.start_date <= last,
            AuditAttendee.reminded_at.is_(None),
        )
        .order_by(Audit.start_date.asc(), AuditAttendee.created_at.asc())
        .all()
    ]

    reminded: set[tuple[uuid.UUID, str]] = set()

    def compose(attendee: AuditAttendee):
        email = _attendee_notification_email(db, attendee)
        if not email:
            return False
        # Two attendee rows for one address share a reminder.
        key = (attendee.audit_id, email.lower())
        if key in reminded:
            return True
        name, fields = attendee.name, _audit_email_fields(attendee.audit)

        def deliver() -> None:
            send_audit_reminder_email(to_email=email, attendee_name=name, **fields)
            reminded.add(key)

        return deliver

    return {
        "enabled": True,
        "sent": deliver_once(
            db, model=AuditAttendee, marker="reminded_at", ids=ids, compose=compose
        ),
        "window_start": first.isoformat(),
        "window_end": last.isoformat(),
    }
