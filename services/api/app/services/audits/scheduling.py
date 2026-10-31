from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive as utcnow
from app.db.models import Audit, AuditAttendee, User
from app.services.audits.access import (
    audit_or_404,
    can_manage_audits,
    editable_audit_or_404,
    require_audit_admin,
)
from app.services.audits.schemas import (
    ScheduledAuditAttendeePayload,
    ScheduledAuditPayload,
    ScheduledAuditUpdatePayload,
)
from app.services.audits.scope import set_audit_scope
from app.services.audits.serializers import attendee_dict, audit_summary, usernames
from app.services.audits.validation import (
    clean_audit_type,
    clean_email,
    clean_framework,
    clean_rich_text,
    validate_dates,
)
from app.services.errors import DomainError
from app.services.mailer import user_email
from app.services.schedules import (
    FUZZY_MONTH_DATE_RULES,
    apply_schedule_fields,
    clean_schedule_anchor_month,
    clean_schedule_date_rule,
    clean_schedule_interval,
    clean_schedule_recurrence,
    occurrences_between,
    revise_schedule,
    schedule_start_for_rule,
)
from app.services.text import clean_text


def schedule_summary(
    db: Session, a: Audit, authors: dict[uuid.UUID, str] | None = None
) -> dict[str, Any]:
    item = audit_summary(db, a, authors)
    item["attendees"] = [
        attendee_dict(x)
        for x in db.query(AuditAttendee)
        .filter(AuditAttendee.audit_id == a.id)
        .order_by(AuditAttendee.name.asc())
        .all()
    ]
    return item


def replace_template_attendees(
    db: Session, *, audit: Audit, attendees: list[ScheduledAuditAttendeePayload]
) -> list[AuditAttendee]:
    db.query(AuditAttendee).filter(AuditAttendee.audit_id == audit.id).delete()
    rows: list[AuditAttendee] = []
    now = utcnow()
    for payload in attendees or []:
        linked_user = None
        if payload.user_id is not None:
            linked_user = (
                db.query(User)
                .filter(User.id == payload.user_id, User.is_active.is_(True))
                .one_or_none()
            )
            if not linked_user:
                raise DomainError(
                    status_code=400, detail="Unknown or inactive attendee user"
                )
        display_name = clean_text(
            payload.name,
            max_len=256,
            required=linked_user is None,
            label="attendee name",
        )
        if linked_user is not None and not display_name:
            display_name = linked_user.username
        explicit_email = clean_email(payload.email, label="attendee email")
        row = AuditAttendee(
            audit_id=audit.id,
            user_id=linked_user.id if linked_user is not None else None,
            name=display_name,
            email=explicit_email or user_email(linked_user),
            role=clean_text(payload.role, max_len=128, label="attendee role") or None,
            created_at=now,
        )
        db.add(row)
        rows.append(row)
    return rows


def template_occurrences_payload(
    audit: Audit, *, start: date, end: date
) -> list[dict[str, Any]]:
    return [
        {
            "date": d.isoformat(),
            "audit_id": str(audit.id),
            "title": audit.title,
            "audit_type": audit.audit_type or "internal",
            "framework_slug": audit.framework_slug,
        }
        for d in occurrences_between(audit, start=start, end=end, limit=100)
    ]


def list_scheduled_audits(
    *, start: date | None, end: date | None, q: str | None, db: Session, user: User
):
    qry = db.query(Audit).filter(Audit.status == "template")
    if q:
        qq = f"%{q.strip()}%"
        qry = qry.filter(Audit.title.ilike(qq))
    rows = qry.order_by(
        Audit.schedule_next_run_date.asc().nullslast(), Audit.title.asc()
    ).all()
    authors = usernames(
        db, [x.created_by_user_id for x in rows if x.created_by_user_id]
    )

    today = date.today()
    window_start = start or date(today.year, today.month, 1)
    window_end = end or (window_start.replace(day=28) + timedelta(days=4))
    if end is None:
        # The UI normally supplies explicit dates; this fallback keeps the API useful by defaulting to the current month.
        window_end = window_end.replace(day=1) - timedelta(days=1)
    if window_end < window_start:
        raise DomainError(status_code=400, detail="end cannot be before start")

    items = [schedule_summary(db, x, authors) for x in rows]
    occurrences: list[dict[str, Any]] = []
    for template in rows:
        occurrences.extend(
            template_occurrences_payload(template, start=window_start, end=window_end)
        )
    occurrences.sort(key=lambda x: (x["date"], x["title"]))
    return {"total": len(rows), "items": items, "occurrences": occurrences}


def create_scheduled_audit(*, payload: ScheduledAuditPayload, db: Session, user: User):
    if not can_manage_audits(db, user):
        raise DomainError(403, "audits.manage permission required")
    framework = clean_framework(payload.framework_slug)
    date_rule = clean_schedule_date_rule(payload.schedule_date_rule)
    anchor_month = clean_schedule_anchor_month(
        payload.schedule_anchor_month, required=(date_rule in FUZZY_MONTH_DATE_RULES)
    )
    start_date = schedule_start_for_rule(
        payload.start_date, date_rule=date_rule, anchor_month=anchor_month
    )
    validate_dates(start_date, payload.end_date)
    recurrence = clean_schedule_recurrence(payload.schedule_recurrence)
    interval = clean_schedule_interval(payload.schedule_interval)
    if payload.schedule_until_date and payload.schedule_until_date < start_date:
        raise DomainError(
            status_code=400, detail="schedule_until_date cannot be before start_date"
        )
    now = utcnow()
    a = Audit(
        title=clean_text(payload.title, max_len=256, required=True, label="title"),
        framework_slug=framework,
        status="template",
        audit_type=clean_audit_type(payload.audit_type),
        start_date=start_date,
        end_date=payload.end_date,
        created_by_user_id=user.id,
        created_at=now,
        updated_at=now,
        notes=clean_rich_text(
            payload.report_notes, max_len=20000, label="report_notes"
        ),
        executive_summary=clean_rich_text(
            payload.executive_summary, max_len=20000, label="executive_summary"
        ),
    )
    apply_schedule_fields(
        a,
        recurrence=recurrence,
        interval=interval,
        next_run_date=start_date,
        until_date=payload.schedule_until_date,
        date_rule=date_rule,
        anchor_month=anchor_month,
    )
    db.add(a)
    db.flush()
    set_audit_scope(
        db,
        a,
        control_values=payload.controls,
        clause_values=payload.clauses,
        document_values=payload.documents,
    )
    replace_template_attendees(db, audit=a, attendees=payload.attendees)
    db.commit()
    db.refresh(a)
    return schedule_summary(db, a, {user.id: user.username})


def update_scheduled_audit(
    *, audit_id: str, payload: ScheduledAuditUpdatePayload, db: Session, user: User
):
    a = editable_audit_or_404(db, user, audit_id)
    if a.status != "template":
        raise DomainError(status_code=400, detail="Audit is not a scheduled template")
    fields = set(payload.model_fields_set or set())
    if "title" in fields:
        a.title = clean_text(payload.title, max_len=256, required=True, label="title")
    if "framework_slug" in fields:
        a.framework_slug = clean_framework(payload.framework_slug)
    if "audit_type" in fields:
        a.audit_type = clean_audit_type(
            payload.audit_type, default=a.audit_type or "internal"
        )
    if "end_date" in fields:
        a.end_date = payload.end_date
    if "report_notes" in fields:
        a.notes = clean_rich_text(
            payload.report_notes, max_len=20000, label="report_notes"
        )
    if "executive_summary" in fields:
        a.executive_summary = clean_rich_text(
            payload.executive_summary, max_len=20000, label="executive_summary"
        )

    revise_schedule(a, payload, fields)
    validate_dates(a.start_date, a.end_date)

    if (
        payload.controls is not None
        or payload.clauses is not None
        or payload.documents is not None
    ):
        set_audit_scope(
            db,
            a,
            control_values=payload.controls or [],
            clause_values=payload.clauses or [],
            document_values=payload.documents or [],
            replace_controls=payload.controls is not None,
            replace_clauses=payload.clauses is not None,
            replace_documents=payload.documents is not None,
        )
    if payload.attendees is not None:
        replace_template_attendees(db, audit=a, attendees=payload.attendees)

    a.updated_at = utcnow()
    db.add(a)
    db.commit()
    db.refresh(a)
    return schedule_summary(
        db, a, usernames(db, [a.created_by_user_id] if a.created_by_user_id else [])
    )


def delete_scheduled_audit(*, audit_id: str, db: Session, user: User):
    require_audit_admin(db, user)
    a = audit_or_404(db, audit_id, lock=True)
    if a.status != "template":
        raise DomainError(status_code=400, detail="Audit is not a scheduled template")
    from app.services.evidence_retention import queue_object

    queue_object(db, a.final_report_storage_uri)
    db.delete(a)
    db.commit()
    return {"ok": True}
