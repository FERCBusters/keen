from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import (
    Audit,
    AuditAttendee,
    AuditEvidence,
    AuditFinding,
    User,
)
from app.security.permissions import has_permission
from app.services.audits.access import (
    audit_or_404,
    can_manage_audits,
    can_see_event,
    editable_audit_or_404,
    require_audit_admin,
    require_sample_source_access,
)
from app.services.audits.references import control_out
from app.services.audits.samples import (
    audit_sample_entity_reference,
    clean_audit_sample_entity_type,
    clean_optional_entity_id,
    short_text,
)
from app.services.audits.schemas import (
    AuditAttendeePayload,
    AuditControlsPayload,
    AuditCreatePayload,
    AuditEvidencePayload,
    AuditEvidenceUpdatePayload,
    AuditFindingPayload,
    AuditFindingUpdatePayload,
    AuditScopePayload,
    AuditUpdatePayload,
)
from app.services.audits.scope import control_or_404, set_audit_scope
from app.services.audits.serializers import (
    attendee_dict,
    audit_summary,
    evidence_dict,
    finding_dict,
    scope_response,
    usernames,
)
from app.services.audits.swot_scope import (
    scope_items as swot_scope_items,
)
from app.services.audits.swot_scope import (
    set_scope as set_swot_scope,
)
from app.services.audits.validation import (
    AUDIT_STATUSES,
    FINDING_KINDS,
    FINDING_STATUSES,
    MUTABLE_AUDIT_STATUSES,
    clean_audit_type,
    clean_email,
    clean_framework,
    clean_rich_text,
    clean_status,
    clean_url,
    event_id_from_url,
    is_locked_audit,
    require_mutable_audit,
    try_uuid,
    utcnow,
    validate_dates,
)
from app.services.errors import DomainError
from app.services.isms.related_records import resolve_target
from app.services.mailer import user_email
from app.services.text import clean_text


def create_audit(*, payload: AuditCreatePayload, db: Session, user: User):
    if not can_manage_audits(db, user):
        raise DomainError(403, "audits.manage permission required")
    framework = clean_framework(payload.framework_slug)
    validate_dates(payload.start_date, payload.end_date)
    a = Audit(
        title=clean_text(payload.title, max_len=256, required=True, label="title"),
        framework_slug=framework,
        audit_type=clean_audit_type(payload.audit_type),
        start_date=payload.start_date,
        end_date=payload.end_date,
        created_by_user_id=user.id,
        created_at=utcnow(),
        updated_at=utcnow(),
        notes=clean_rich_text(
            payload.report_notes if payload.report_notes is not None else payload.notes,
            max_len=20000,
            label="report_notes",
        ),
        executive_summary=clean_rich_text(
            payload.executive_summary, max_len=20000, label="executive_summary"
        ),
    )
    db.add(a)
    db.flush()
    set_swot_scope(db, a, payload.swot_ids, user)
    set_audit_scope(
        db,
        a,
        control_values=payload.controls,
        clause_values=payload.clauses,
        document_values=payload.documents,
    )
    # The audit and its attendees are saved together, or not at all.
    for attendee in payload.attendees:
        db.add(_attendee_row(db, user, a, attendee))
    db.commit()
    db.refresh(a)
    return audit_summary(db, a, {user.id: user.username})


def delete_audit(*, audit_id: str, db: Session, user: User):
    require_audit_admin(db, user)
    a = audit_or_404(db, audit_id, lock=True)
    if a.status == "template":
        raise DomainError(
            status_code=400,
            detail="Use the scheduled audit template delete endpoint for templates",
        )

    db.query(Audit).filter(Audit.schedule_last_created_audit_id == a.id).update(
        {Audit.schedule_last_created_audit_id: None}, synchronize_session=False
    )
    from app.services.evidence_retention import queue_object

    queue_object(db, a.final_report_storage_uri)
    db.delete(a)
    db.commit()
    return {"ok": True}


def update_audit(
    *, audit_id: str, payload: AuditUpdatePayload, db: Session, user: User
):
    a = editable_audit_or_404(db, user, audit_id)
    fields = set(payload.model_fields_set or set())
    if is_locked_audit(a):
        if fields != {"status"}:
            require_mutable_audit(a)
        new_status = clean_status(
            payload.status, AUDIT_STATUSES, default=a.status or "open"
        )
        if new_status not in MUTABLE_AUDIT_STATUSES:
            raise DomainError(
                status_code=409,
                detail="Completed or archived audits can only be reopened to Open or In progress.",
            )
        a.status = new_status
        a.updated_at = utcnow()
        db.add(a)
        db.commit()
        db.refresh(a)
        return audit_summary(
            db,
            a,
            usernames(db, [a.created_by_user_id] if a.created_by_user_id else []),
        )
    if "title" in fields:
        a.title = clean_text(payload.title, max_len=256, required=True, label="title")
    if "framework_slug" in fields:
        a.framework_slug = clean_framework(payload.framework_slug)
    if "status" in fields:
        a.status = clean_status(
            payload.status, AUDIT_STATUSES, default=a.status or "open"
        )
    if "audit_type" in fields:
        a.audit_type = clean_audit_type(
            payload.audit_type, default=a.audit_type or "internal"
        )
    if "start_date" in fields:
        a.start_date = payload.start_date
    if "end_date" in fields:
        a.end_date = payload.end_date
    if "notes" in fields or "report_notes" in fields:
        a.notes = clean_rich_text(
            payload.report_notes if "report_notes" in fields else payload.notes,
            max_len=20000,
            label="report_notes",
        )
    if "executive_summary" in fields:
        a.executive_summary = clean_rich_text(
            payload.executive_summary, max_len=20000, label="executive_summary"
        )
    validate_dates(a.start_date, a.end_date)
    a.updated_at = utcnow()
    db.add(a)
    db.commit()
    db.refresh(a)
    return audit_summary(
        db, a, usernames(db, [a.created_by_user_id] if a.created_by_user_id else [])
    )


def replace_audit_scope(
    *, audit_id: str, payload: AuditScopePayload, db: Session, user: User
):
    a = editable_audit_or_404(db, user, audit_id)
    require_mutable_audit(a)
    set_swot_scope(db, a, payload.swot_ids, user)
    controls, clauses, documents = set_audit_scope(
        db,
        a,
        control_values=payload.controls,
        clause_values=payload.clauses,
        document_values=payload.documents,
    )
    db.commit()
    return {
        **scope_response(controls, clauses, documents),
        "scoped_swot": swot_scope_items(db, a.id),
    }


def replace_audit_controls(
    *, audit_id: str, payload: AuditControlsPayload, db: Session, user: User
):
    a = editable_audit_or_404(db, user, audit_id)
    require_mutable_audit(a)
    controls, _, _ = set_audit_scope(
        db,
        a,
        control_values=payload.controls,
        clause_values=None,
        document_values=None,
        replace_clauses=False,
        replace_documents=False,
    )
    db.commit()
    return {"ok": True, "scoped_controls": [control_out(c) for c in controls]}


def add_audit_evidence(
    *, audit_id: str, payload: AuditEvidencePayload, db: Session, user: User
):
    from app.services.features import state

    features = state(db)
    features.require("sampling")
    if payload.event_id:
        features.require("event_sampling")
    entity_type = clean_audit_sample_entity_type(payload.entity_type)
    if not entity_type and not has_permission(db, user, "events.read"):
        raise DomainError(403, "events.read permission required")
    a = editable_audit_or_404(db, user, audit_id)
    require_mutable_audit(a, action="sampled into")
    evidence_url = clean_url(payload.evidence_url)
    entity_id = clean_optional_entity_id(payload.entity_id)
    eid = payload.event_id
    if not eid and not (entity_type and entity_id):
        eid = event_id_from_url(evidence_url)
    ev = can_see_event(db, user, eid) if eid else None
    if entity_type and entity_id:
        require_sample_source_access(db, user, entity_type, entity_id)

    entity = audit_sample_entity_reference(
        db,
        framework=a.framework_slug,
        entity_type=entity_type,
        entity_id=entity_id,
        required=True,
    )

    if not ev and not entity and not evidence_url:
        raise DomainError(
            status_code=400,
            detail="event_id, entity_type/entity_id, or evidence_url is required",
        )

    title = clean_text(payload.title, max_len=512, label="title")
    if not title and ev:
        title = ev.summary[:512]
    if not title and entity:
        title = short_text(entity.get("title"), 512)
    if not evidence_url and entity:
        evidence_url = clean_url(entity.get("href"))
    notes = clean_rich_text(payload.notes, max_len=20000, label="notes")

    if ev:
        existing = (
            db.query(AuditEvidence)
            .filter(AuditEvidence.audit_id == a.id, AuditEvidence.event_id == ev.id)
            .one_or_none()
        )
        if existing:
            if evidence_url:
                existing.evidence_url = evidence_url
            if title:
                existing.title = title
            if notes:
                existing.notes = notes
            a.updated_at = utcnow()
            db.add(existing)
            db.add(a)
            db.commit()
            db.refresh(existing)
            return evidence_dict(db, existing, {user.id: user.username})

    if entity_type and entity_id:
        existing = (
            db.query(AuditEvidence)
            .filter(
                AuditEvidence.audit_id == a.id,
                AuditEvidence.entity_type == entity_type,
                AuditEvidence.entity_id == entity_id,
            )
            .one_or_none()
        )
        if existing:
            if evidence_url:
                existing.evidence_url = evidence_url
            if title:
                existing.title = title
            if notes:
                existing.notes = notes
            a.updated_at = utcnow()
            db.add(existing)
            db.add(a)
            db.commit()
            db.refresh(existing)
            return evidence_dict(db, existing, {user.id: user.username})

    row = AuditEvidence(
        audit_id=a.id,
        event_id=ev.id if ev else None,
        entity_type=entity_type,
        entity_id=entity_id,
        evidence_url=evidence_url,
        title=title or evidence_url,
        notes=notes,
        added_by_user_id=user.id,
        added_at=utcnow(),
    )
    a.updated_at = utcnow()
    db.add(row)
    db.add(a)
    db.commit()
    db.refresh(row)
    return evidence_dict(db, row, {user.id: user.username})


def update_audit_evidence(
    *,
    audit_id: str,
    evidence_id: str,
    payload: AuditEvidenceUpdatePayload,
    db: Session,
    user: User,
):
    a = editable_audit_or_404(db, user, audit_id)
    require_mutable_audit(a)
    evid = try_uuid(evidence_id)
    if not evid:
        raise DomainError(status_code=400, detail="evidence_id must be a UUID")
    row = (
        db.query(AuditEvidence)
        .filter(AuditEvidence.audit_id == a.id, AuditEvidence.id == evid)
        .one_or_none()
    )
    if not row:
        raise DomainError(status_code=404, detail="Evidence not found")
    fields = set(payload.model_fields_set or set())
    if "evidence_url" in fields:
        row.evidence_url = clean_url(payload.evidence_url)
    if "title" in fields:
        row.title = clean_text(payload.title, max_len=512, label="title") or None
    if "notes" in fields:
        row.notes = clean_rich_text(payload.notes, max_len=20000, label="notes")
    a.updated_at = utcnow()
    db.add(row)
    db.add(a)
    db.commit()
    db.refresh(row)
    return evidence_dict(db, row, {user.id: user.username})


def delete_audit_evidence(*, audit_id: str, evidence_id: str, db: Session, user: User):
    a = editable_audit_or_404(db, user, audit_id)
    require_mutable_audit(a)
    evid = try_uuid(evidence_id)
    if not evid:
        raise DomainError(status_code=400, detail="evidence_id must be a UUID")
    row = (
        db.query(AuditEvidence)
        .filter(AuditEvidence.audit_id == a.id, AuditEvidence.id == evid)
        .one_or_none()
    )
    if not row:
        raise DomainError(status_code=404, detail="Evidence not found")
    db.delete(row)
    a.updated_at = utcnow()
    db.add(a)
    db.commit()
    return {"ok": True}


def _attendee_row(
    db: Session, user: User, audit: Audit, payload: AuditAttendeePayload
) -> AuditAttendee:
    """A checked attendee of ``audit``; the caller adds and commits it.

    People come from the ISMS register, so naming one needs the access its
    page needs, and an unreadable person is reported like a missing one.
    """
    linked_user = None
    linked_person = None
    if payload.person_id is not None:
        linked_person = resolve_target(
            db, user, "person", payload.person_id, required=False
        )
        if linked_person is None:
            raise DomainError(status_code=400, detail="Unknown Person")
    if payload.person_id is not None and payload.user_id is not None:
        raise DomainError(
            status_code=400, detail="Select either a person or a Keen user"
        )
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
        required=linked_user is None and linked_person is None,
        label="name",
    )
    if linked_user is not None and not display_name:
        display_name = linked_user.username
    if linked_person is not None and not display_name:
        display_name = linked_person.name

    explicit_email = clean_email(payload.email, label="email")
    return AuditAttendee(
        audit_id=audit.id,
        user_id=linked_user.id if linked_user is not None else None,
        person_id=linked_person.id if linked_person is not None else None,
        name=display_name,
        email=explicit_email
        or (linked_person.email if linked_person else None)
        or user_email(linked_user),
        role=clean_text(payload.role, max_len=128, label="role") or None,
        created_at=utcnow(),
    )


def add_attendee(
    *, audit_id: str, payload: AuditAttendeePayload, db: Session, user: User
):
    a = editable_audit_or_404(db, user, audit_id)
    require_mutable_audit(a)
    row = _attendee_row(db, user, a, payload)
    a.updated_at = utcnow()
    db.add(row)
    db.add(a)
    db.commit()
    db.refresh(row)
    return attendee_dict(row)


def delete_attendee(*, audit_id: str, attendee_id: str, db: Session, user: User):
    a = editable_audit_or_404(db, user, audit_id)
    require_mutable_audit(a)
    tid = try_uuid(attendee_id)
    if not tid:
        raise DomainError(status_code=400, detail="attendee_id must be a UUID")
    row = (
        db.query(AuditAttendee)
        .filter(AuditAttendee.audit_id == a.id, AuditAttendee.id == tid)
        .one_or_none()
    )
    if not row:
        raise DomainError(status_code=404, detail="Attendee not found")
    db.delete(row)
    a.updated_at = utcnow()
    db.add(a)
    db.commit()
    return {"ok": True}


def add_finding(
    *, audit_id: str, payload: AuditFindingPayload, db: Session, user: User
):
    a = editable_audit_or_404(db, user, audit_id)
    require_mutable_audit(a)
    kind = (payload.kind or "").strip().lower()
    if kind not in FINDING_KINDS:
        raise DomainError(status_code=400, detail="Invalid finding kind")
    ctrl = (
        control_or_404(db, a.framework_slug, payload.control)
        if payload.control
        else None
    )
    row = AuditFinding(
        audit_id=a.id,
        kind=kind,
        title=clean_text(payload.title, max_len=256, required=True, label="title"),
        description=clean_rich_text(
            payload.description, max_len=50000, label="description"
        ),
        control_item_id=ctrl.id if ctrl else None,
        status=clean_status(payload.status, FINDING_STATUSES, default="open"),
        created_by_user_id=user.id,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    a.updated_at = utcnow()
    db.add(row)
    db.add(a)
    db.commit()
    db.refresh(row)
    return finding_dict(row, {user.id: user.username})


def update_finding(
    *,
    audit_id: str,
    finding_id: str,
    payload: AuditFindingUpdatePayload,
    db: Session,
    user: User,
):
    a = editable_audit_or_404(db, user, audit_id)
    require_mutable_audit(a)
    fid = try_uuid(finding_id)
    if not fid:
        raise DomainError(status_code=400, detail="finding_id must be a UUID")
    row = (
        db.query(AuditFinding)
        .filter(AuditFinding.audit_id == a.id, AuditFinding.id == fid)
        .one_or_none()
    )
    if not row:
        raise DomainError(status_code=404, detail="Finding not found")
    fields = set(payload.model_fields_set or set())
    if "kind" in fields:
        kind = (payload.kind or "").strip().lower()
        if kind not in FINDING_KINDS:
            raise DomainError(status_code=400, detail="Invalid finding kind")
        row.kind = kind
    if "title" in fields:
        row.title = clean_text(payload.title, max_len=256, required=True, label="title")
    if "description" in fields:
        row.description = clean_rich_text(
            payload.description, max_len=50000, label="description"
        )
    if "control" in fields:
        ctrl = (
            control_or_404(db, a.framework_slug, payload.control)
            if payload.control
            else None
        )
        row.control_item_id = ctrl.id if ctrl else None
    if "status" in fields:
        row.status = clean_status(
            payload.status, FINDING_STATUSES, default=row.status or "open"
        )
    row.updated_at = utcnow()
    a.updated_at = utcnow()
    db.add(row)
    db.add(a)
    db.commit()
    db.refresh(row)
    return finding_dict(row, {user.id: user.username})


def delete_finding(*, audit_id: str, finding_id: str, db: Session, user: User):
    a = editable_audit_or_404(db, user, audit_id)
    require_mutable_audit(a)
    fid = try_uuid(finding_id)
    if not fid:
        raise DomainError(status_code=400, detail="finding_id must be a UUID")
    row = (
        db.query(AuditFinding)
        .filter(AuditFinding.audit_id == a.id, AuditFinding.id == fid)
        .one_or_none()
    )
    if not row:
        raise DomainError(status_code=404, detail="Finding not found")
    db.delete(row)
    a.updated_at = utcnow()
    db.add(a)
    db.commit()
    return {"ok": True}
