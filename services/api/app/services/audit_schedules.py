from __future__ import annotations

import logging
import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Audit,
    AuditAttendee,
    AuditScopedClause,
    AuditScopedControl,
    AuditScopedIsmsDocument,
    User,
)
from app.services.mailer import send_scheduled_audit_created_email, smtp_configured

log = logging.getLogger(__name__)

SCHEDULE_RECURRENCES = {"once", "weekly", "monthly", "quarterly", "yearly"}
SCHEDULE_DATE_RULES = {
    "exact",
    "first_weekday_of_month",
    "first_monday_of_month",
    "first_tuesday_of_month",
    "first_wednesday_of_month",
    "first_thursday_of_month",
    "first_friday_of_month",
}
FIRST_NAMED_WEEKDAY_RULES = {
    "first_monday_of_month": 0,
    "first_tuesday_of_month": 1,
    "first_wednesday_of_month": 2,
    "first_thursday_of_month": 3,
    "first_friday_of_month": 4,
}
FUZZY_MONTH_DATE_RULES = SCHEDULE_DATE_RULES - {"exact"}


def utcnow() -> datetime:
    return datetime.utcnow()


def _today_for_schedule() -> date:
    """Return today's date in Keen's configured timezone for date-only schedules."""

    tz_name = str(settings.timezone or "Etc/UTC").strip() or "Etc/UTC"
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except ZoneInfoNotFoundError:
        log.warning(
            "Invalid KEEN_TIMEZONE=%r; falling back to server-local date for scheduled audits",
            tz_name,
        )
    except Exception:
        log.exception(
            "Failed to resolve KEEN_TIMEZONE=%r; falling back to server-local date for scheduled audits",
            tz_name,
        )
    return date.today()


def _add_months(d: date, months: int) -> date:
    month_idx = (d.month - 1) + months
    year = d.year + month_idx // 12
    month = month_idx % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def _first_matching_weekday_of_month(
    year: int, month: int, *, weekday: int | None = None
) -> date:
    """Return the first matching weekday date for a month.

    weekday=None means the first business weekday, Monday through Friday.
    Otherwise weekday follows Python's date.weekday(): Monday=0 ... Friday=4.
    """

    m = max(1, min(12, int(month or 1)))
    d = date(int(year), m, 1)
    if weekday is None:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d

    target = max(0, min(4, int(weekday)))
    while d.weekday() != target:
        d += timedelta(days=1)
    return d


def first_weekday_of_month(year: int, month: int) -> date:
    """Return the first Monday-Friday date for a month."""

    return _first_matching_weekday_of_month(year, month)


def first_scheduled_rule_date(year: int, month: int, rule: str) -> date:
    """Return the first concrete date for a supported fuzzy monthly rule."""

    weekday = FIRST_NAMED_WEEKDAY_RULES.get(str(rule or "").strip().lower())
    return _first_matching_weekday_of_month(year, month, weekday=weekday)


def _fuzzy_month_rule_next(
    current: date,
    *,
    recurrence: str,
    interval: int,
    anchor_month: int | None,
    rule: str,
) -> date | None:
    recur = str(recurrence or "once").strip().lower()
    step = max(1, int(interval or 1))
    if recur == "once":
        return None
    if recur == "weekly":
        return None
    if recur == "monthly":
        candidate = _add_months(date(current.year, current.month, 1), step)
        return first_scheduled_rule_date(candidate.year, candidate.month, rule)
    if recur == "quarterly":
        candidate = _add_months(date(current.year, current.month, 1), 3 * step)
        return first_scheduled_rule_date(candidate.year, candidate.month, rule)
    if recur == "yearly":
        month = max(1, min(12, int(anchor_month or current.month)))
        return first_scheduled_rule_date(current.year + step, month, rule)
    return None


def next_occurrence_after(
    current: date,
    *,
    recurrence: str,
    interval: int = 1,
    until: date | None = None,
    date_rule: str = "exact",
    anchor_month: int | None = None,
) -> date | None:
    recur = str(recurrence or "once").strip().lower()
    step = max(1, int(interval or 1))
    rule = str(date_rule or "exact").strip().lower()
    if rule in FUZZY_MONTH_DATE_RULES:
        nxt = _fuzzy_month_rule_next(
            current,
            recurrence=recur,
            interval=step,
            anchor_month=anchor_month,
            rule=rule,
        )
        if not nxt:
            return None
    elif recur == "once":
        return None
    elif recur == "weekly":
        nxt = current + timedelta(days=7 * step)
    elif recur == "monthly":
        nxt = _add_months(current, step)
    elif recur == "quarterly":
        nxt = _add_months(current, 3 * step)
    elif recur == "yearly":
        nxt = _add_months(current, 12 * step)
    else:
        return None
    if until and nxt > until:
        return None
    return nxt


def occurrences_between(
    template: Audit, *, start: date, end: date, limit: int = 100
) -> list[date]:
    """Return scheduled occurrence dates for a template within [start, end]."""

    recur = (
        str(getattr(template, "schedule_recurrence", None) or "once").strip().lower()
    )
    if recur not in SCHEDULE_RECURRENCES:
        recur = "once"
    interval = max(1, int(getattr(template, "schedule_interval", None) or 1))
    until = getattr(template, "schedule_until_date", None)
    date_rule = (
        str(getattr(template, "schedule_date_rule", None) or "exact").strip().lower()
    )
    if date_rule not in SCHEDULE_DATE_RULES:
        date_rule = "exact"
    anchor_month = getattr(template, "schedule_anchor_month", None)

    cur = getattr(template, "schedule_next_run_date", None) or getattr(
        template, "start_date", None
    )
    if not cur:
        return []

    out: list[date] = []
    guard = 0
    # Advance cheaply until the window if the saved next date is before it.
    while cur and cur < start and guard < 1000:
        cur = next_occurrence_after(
            cur,
            recurrence=recur,
            interval=interval,
            until=until,
            date_rule=date_rule,
            anchor_month=anchor_month,
        )
        guard += 1
    while cur and cur <= end and len(out) < limit:
        if cur >= start:
            out.append(cur)
        cur = next_occurrence_after(
            cur,
            recurrence=recur,
            interval=interval,
            until=until,
            date_rule=date_rule,
            anchor_month=anchor_month,
        )
        if recur == "once":
            break
    return out


def _looks_like_email(raw: str | None) -> bool:
    value = (raw or "").strip()
    return bool(
        value
        and "@" in value
        and not value.startswith("@")
        and not value.endswith("@")
        and len(value) <= 256
    )


def _user_notification_email(user: User | None) -> str | None:
    if user is None:
        return None
    email = getattr(user, "email", None)
    if _looks_like_email(email):
        return str(email).strip()
    username = getattr(user, "username", None)
    if _looks_like_email(username):
        return str(username).strip()
    return None


def _attendee_notification_email(db: Session, attendee: AuditAttendee) -> str | None:
    email = str(attendee.email or "").strip()
    if _looks_like_email(email):
        return email
    if attendee.user_id is None:
        return None
    user = (
        db.query(User)
        .filter(User.id == attendee.user_id, User.is_active.is_(True))
        .one_or_none()
    )
    return _user_notification_email(user)


def _audit_url(audit_id: uuid.UUID) -> str | None:
    base = (settings.public_base_url or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/audit.html?id={audit_id}"


def _audit_end_for_run(template: Audit, run_date: date) -> date | None:
    if not template.start_date or not template.end_date:
        return template.end_date
    if template.end_date < template.start_date:
        return run_date
    return run_date + (template.end_date - template.start_date)


def _next_template_occurrence(template: Audit, run_date: date) -> date | None:
    return next_occurrence_after(
        run_date,
        recurrence=template.schedule_recurrence or "once",
        interval=template.schedule_interval or 1,
        until=template.schedule_until_date,
        date_rule=template.schedule_date_rule or "exact",
        anchor_month=template.schedule_anchor_month,
    )


def _advance_template_past_missed_runs(
    template: Audit, *, run_today: date, max_steps: int = 1000
) -> tuple[date | None, list[str]]:
    """Advance stale schedule dates without creating backfilled audits.

    A scheduled audit should only materialise when the occurrence's begin date is
    today or inside the configured look-ahead window. If the worker was offline
    for prior occurrences, those missed dates are skipped rather than creating
    audits whose begin date is already in the past.
    """

    run_date = template.schedule_next_run_date
    skipped: list[str] = []
    steps = 0
    while run_date and run_date < run_today and steps < max_steps:
        skipped.append(run_date.isoformat())
        run_date = _next_template_occurrence(template, run_date)
        steps += 1
    if run_date and run_date < run_today:
        log.warning(
            "Stopped advancing stale scheduled audit template %s after %s missed "
            "runs; disabling next run date to avoid creating a past audit",
            getattr(template, "id", "unknown"),
            max_steps,
        )
        return None, skipped
    return run_date, skipped


def _copy_template_scope_and_attendees(
    db: Session, *, template: Audit, audit: Audit
) -> None:
    now = utcnow()
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
                name=attendee.name,
                email=_attendee_notification_email(db, attendee),
                role=attendee.role,
                created_at=now,
            )
        )


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
                audit_title=audit.title,
                audit_type=audit.audit_type or "internal",
                framework_slug=audit.framework_slug,
                scheduled_for=(
                    audit.start_date.isoformat() if audit.start_date else "today"
                ),
                scheduled_until=(
                    audit.end_date.isoformat() if audit.end_date else None
                ),
                audit_url=_audit_url(audit.id),
                uid=f"keen-audit-{audit.id}",
            )
            sent += 1
        except Exception:
            log.exception(
                "Failed to email scheduled audit attendee %s for audit_id=%s",
                email,
                audit.id,
            )
    return sent


def materialize_due_scheduled_audits(
    db: Session, *, today: date | None = None
) -> dict[str, Any]:
    run_today = today or _today_for_schedule()
    days_ahead = max(0, int(settings.scheduled_audit_create_days_ahead or 0))
    latest_create_date = run_today + timedelta(days=days_ahead)
    templates = (
        db.query(Audit)
        .filter(Audit.status == "template")
        .filter(Audit.schedule_next_run_date.isnot(None))
        .filter(Audit.schedule_next_run_date <= latest_create_date)
        .order_by(Audit.schedule_next_run_date.asc(), Audit.created_at.asc())
        .all()
    )

    created: list[dict[str, Any]] = []
    skipped_past: list[dict[str, Any]] = []
    for template in templates:
        run_date = template.schedule_next_run_date
        if not run_date:
            continue

        if run_date < run_today:
            next_run_date, skipped_dates = _advance_template_past_missed_runs(
                template, run_today=run_today
            )
            template.schedule_next_run_date = next_run_date
            template.updated_at = utcnow()
            db.add(template)
            db.commit()
            if skipped_dates:
                skipped_past.append(
                    {
                        "template_id": str(template.id),
                        "skipped_run_dates": skipped_dates,
                        "next_run_date": (
                            next_run_date.isoformat() if next_run_date else None
                        ),
                    }
                )
            run_date = next_run_date
            if not run_date or run_date > latest_create_date:
                continue

        # Defensive idempotency guard for repeated runs on the same persisted template state.
        if (
            template.schedule_last_run_date
            and template.schedule_last_run_date >= run_date
        ):
            template.schedule_next_run_date = _next_template_occurrence(
                template, run_date
            )
            db.add(template)
            db.commit()
            continue

        now = utcnow()
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

        template.schedule_last_run_date = run_date
        template.schedule_last_created_audit_id = audit.id
        template.schedule_next_run_date = _next_template_occurrence(template, run_date)
        template.updated_at = now
        db.add(template)
        db.commit()
        db.refresh(audit)

        emails_sent = _email_attendees(db, audit=audit)
        created.append(
            {
                "template_id": str(template.id),
                "audit_id": str(audit.id),
                "run_date": run_date.isoformat(),
                "emails_sent": emails_sent,
            }
        )

    return {
        "checked": len(templates),
        "created": created,
        "skipped_past": skipped_past,
        "create_days_ahead": days_ahead,
        "create_window_start": run_today.isoformat(),
        "create_window_end": latest_create_date.isoformat(),
    }
