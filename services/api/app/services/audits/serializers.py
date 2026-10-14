from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    Audit,
    AuditAttendee,
    AuditEvidence,
    AuditFinding,
    AuditScopedClause,
    AuditScopedControl,
    AuditScopedIsmsDocument,
    AuditSwotScope,
    ControlItem,
    FrameworkClause,
    IsmsDocument,
    Mapping,
    User,
)
from app.services.audits.references import clause_out, control_out, document_out
from app.services.audits.samples import audit_sample_entity_reference
from app.services.audits.validation import user_email_snapshot


def scope_response(
    controls: list[ControlItem],
    clauses: list[FrameworkClause],
    documents: list[IsmsDocument] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "scoped_controls": [control_out(c) for c in controls],
        "scoped_clauses": [clause_out(c) for c in clauses],
        "scoped_documents": [document_out(d) for d in (documents or [])],
    }


def usernames(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = db.query(User.id, User.username).filter(User.id.in_(ids)).all()
    return {uid: uname for uid, uname in rows}


def iso_dt(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def iso_date(v: date | None) -> str | None:
    return v.isoformat() if v else None


def audit_summary(
    db: Session, a: Audit, authors: dict[uuid.UUID, str] | None = None
) -> dict[str, Any]:
    authors = authors or {}
    swot_scope_count = db.query(AuditSwotScope).filter_by(audit_id=a.id).count()
    control_scope_count = int(
        db.query(func.count(AuditScopedControl.control_item_id))
        .filter(AuditScopedControl.audit_id == a.id)
        .scalar()
        or 0
    )
    clause_scope_count = int(
        db.query(func.count(AuditScopedClause.clause_id))
        .filter(AuditScopedClause.audit_id == a.id)
        .scalar()
        or 0
    )
    document_scope_count = int(
        db.query(func.count(AuditScopedIsmsDocument.document_id))
        .filter(AuditScopedIsmsDocument.audit_id == a.id)
        .scalar()
        or 0
    )
    counts = {
        "evidence_count": int(
            db.query(func.count(AuditEvidence.id))
            .filter(AuditEvidence.audit_id == a.id)
            .scalar()
            or 0
        ),
        "scope_count": control_scope_count
        + clause_scope_count
        + document_scope_count
        + swot_scope_count,
        "swot_scope_count": swot_scope_count,
        "control_scope_count": control_scope_count,
        "clause_scope_count": clause_scope_count,
        "document_scope_count": document_scope_count,
        "attendee_count": int(
            db.query(func.count(AuditAttendee.id))
            .filter(AuditAttendee.audit_id == a.id)
            .scalar()
            or 0
        ),
        "finding_count": int(
            db.query(func.count(AuditFinding.id))
            .filter(AuditFinding.audit_id == a.id)
            .scalar()
            or 0
        ),
    }
    return {
        "id": str(a.id),
        "title": a.title,
        "framework_slug": a.framework_slug,
        "status": a.status,
        "audit_type": a.audit_type or "internal",
        "start_date": iso_date(a.start_date),
        "end_date": iso_date(a.end_date),
        "schedule_recurrence": a.schedule_recurrence or "once",
        "schedule_interval": int(a.schedule_interval or 1),
        "schedule_date_rule": a.schedule_date_rule or "exact",
        "schedule_anchor_month": a.schedule_anchor_month,
        "schedule_next_run_date": iso_date(a.schedule_next_run_date),
        "schedule_until_date": iso_date(a.schedule_until_date),
        "schedule_last_run_date": iso_date(a.schedule_last_run_date),
        "schedule_last_created_audit_id": (
            str(a.schedule_last_created_audit_id)
            if a.schedule_last_created_audit_id
            else None
        ),
        "created_by_user_id": (
            str(a.created_by_user_id) if a.created_by_user_id else None
        ),
        "created_by_username": (
            authors.get(a.created_by_user_id, "") if a.created_by_user_id else ""
        ),
        "created_at": iso_dt(a.created_at),
        "updated_at": iso_dt(a.updated_at),
        "notes": a.notes or "",
        "report_notes": a.notes or "",
        "executive_summary": a.executive_summary or "",
        "has_final_report": bool(a.final_report_storage_uri),
        "final_report": report_dict(a),
        **counts,
    }


def report_dict(a: Audit) -> dict[str, Any] | None:
    if not a.final_report_storage_uri:
        return None
    return {
        "filename": a.final_report_filename,
        "content_type": a.final_report_content_type,
        "sha256": a.final_report_sha256,
        "size_bytes": a.final_report_size_bytes,
        "uploaded_at": iso_dt(a.final_report_uploaded_at),
        "download_url": f"/api/v1/audits/{a.id}/report",
    }


def evidence_dict(
    db: Session, e: AuditEvidence, added_by: dict[uuid.UUID, str] | None = None
) -> dict[str, Any]:
    added_by = added_by or {}
    controls: list[dict[str, Any]] = []
    ev = e.event
    entity = audit_sample_entity_reference(
        db,
        framework=e.audit.framework_slug,
        entity_type=e.entity_type,
        entity_id=e.entity_id,
        required=False,
    )
    if ev is not None:
        rows = (
            db.query(ControlItem)
            .join(Mapping, Mapping.control_item_id == ControlItem.id)
            .filter(
                Mapping.event_id == ev.id,
                ControlItem.framework_slug == e.audit.framework_slug,
            )
            .order_by(ControlItem.ref.asc())
            .all()
        )
        controls = [{"id": str(c.id), "ref": c.ref, "title": c.title} for c in rows]
    elif entity is not None:
        controls = list(entity.get("controls") or [])
    return {
        "id": str(e.id),
        "audit_id": str(e.audit_id),
        "event_id": str(e.event_id) if e.event_id else None,
        "entity_type": e.entity_type,
        "entity_id": str(e.entity_id) if e.entity_id else None,
        "evidence_url": e.evidence_url,
        "title": e.title,
        "notes": e.notes or "",
        "added_by_user_id": str(e.added_by_user_id) if e.added_by_user_id else None,
        "added_by_username": (
            added_by.get(e.added_by_user_id, "") if e.added_by_user_id else ""
        ),
        "added_at": iso_dt(e.added_at),
        "event": (
            None
            if ev is None
            else {
                "id": str(ev.id),
                "timestamp": iso_dt(ev.timestamp),
                "source": ev.source,
                "connection_id": ev.connection_id,
                "connection_name": ev.connection_name,
                "system": ev.system,
                "actor": ev.actor,
                "action": ev.action,
                "outcome": ev.outcome,
                "severity": ev.severity,
                "summary": ev.summary,
                "controls": controls,
            }
        ),
        "entity": entity,
        "controls": controls,
    }


def attendee_dict(x: AuditAttendee) -> dict[str, Any]:
    u = getattr(x, "user", None)
    return {
        "id": str(x.id),
        "audit_id": str(x.audit_id),
        "user_id": str(x.user_id) if x.user_id else None,
        "person_id": str(x.person_id) if x.person_id else None,
        "username": u.username if u else None,
        "name": x.name,
        "email": x.email or user_email_snapshot(u),
        "role": x.role,
        "created_at": iso_dt(x.created_at),
    }


def finding_dict(
    x: AuditFinding, authors: dict[uuid.UUID, str] | None = None
) -> dict[str, Any]:
    c = x.control
    authors = authors or {}
    return {
        "id": str(x.id),
        "audit_id": str(x.audit_id),
        "kind": x.kind,
        "title": x.title,
        "description": x.description or "",
        "status": x.status,
        "control": (
            None if c is None else {"id": str(c.id), "ref": c.ref, "title": c.title}
        ),
        "created_by_user_id": (
            str(x.created_by_user_id) if x.created_by_user_id else None
        ),
        "created_by_username": (
            authors.get(x.created_by_user_id, "") if x.created_by_user_id else ""
        ),
        "created_at": iso_dt(x.created_at),
        "updated_at": iso_dt(x.updated_at),
    }


def coverage_source_out(
    audit: Audit, *, occurrence_date: date | None = None
) -> dict[str, Any]:
    source = (
        "scheduled" if audit.status == "template" or occurrence_date else "completed"
    )
    return {
        "id": str(audit.id),
        "audit_id": str(audit.id),
        "title": audit.title,
        "status": audit.status,
        "source": source,
        "audit_type": audit.audit_type or "internal",
        "start_date": (
            occurrence_date.isoformat()
            if occurrence_date
            else iso_date(audit.start_date)
        ),
        "occurrence_date": occurrence_date.isoformat() if occurrence_date else None,
        "end_date": iso_date(audit.end_date),
        "framework_slug": audit.framework_slug,
    }


def coverage_item_out(item: Any, sources: list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(item, ControlItem):
        base = control_out(item)
        label = f"{item.ref or ''} {item.title or ''}".strip()
    elif isinstance(item, FrameworkClause):
        base = clause_out(item)
        label = f"{item.ref or ''} {item.title or ''}".strip()
    else:
        base = document_out(item)
        label = getattr(item, "title", None) or "ISMS document"
    base["label"] = label
    base["coverage_count"] = len(sources)
    base["sources"] = sources
    return base
