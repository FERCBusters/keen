from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.db.models import (
    Audit,
    AuditAttendee,
    AuditEvidence,
    AuditFinding,
    AuditScopedClause,
    AuditScopedControl,
    AuditScopedIsmsDocument,
    ControlClauseLink,
    ControlItem,
    FrameworkClause,
    IsmsDocument,
    User,
)
from app.services.audit_schedules import occurrences_between
from app.services.audits.access import (
    audit_or_404,
    can_read_audit,
    can_see_event,
    require_audit_access,
)
from app.services.audits.references import clause_out, control_out, document_out
from app.services.audits.samples import (
    clean_optional_entity_id,
    normalise_audit_sample_entity_type,
)
from app.services.audits.scope import selectable_clause_descendants
from app.services.audits.serializers import (
    attendee_dict,
    audit_summary,
    coverage_item_out,
    coverage_source_out,
    evidence_dict,
    finding_dict,
    iso_dt,
    usernames,
)
from app.services.audits.swot_scope import scope_items as swot_scope_items
from app.services.audits.validation import (
    AUDIT_STATUSES,
    clean_framework,
    clean_status,
    date_overlaps,
    try_uuid,
    user_email_snapshot,
)
from app.services.errors import DomainError


def list_my_attendee_audits(*, limit: int, offset: int, db: Session, user: User):
    """List audits where the current user is explicitly marked as an attendee."""

    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))

    attendee_audit_ids = (
        db.query(AuditAttendee.audit_id)
        .filter(AuditAttendee.user_id == user.id)
        .distinct()
        .subquery()
    )

    total = int(
        db.query(func.count(Audit.id))
        .join(attendee_audit_ids, Audit.id == attendee_audit_ids.c.audit_id)
        .filter(Audit.status != "template")
        .scalar()
        or 0
    )

    rows = (
        db.query(Audit)
        .join(attendee_audit_ids, Audit.id == attendee_audit_ids.c.audit_id)
        .filter(Audit.status != "template")
        .order_by(desc(Audit.updated_at))
        .limit(limit)
        .offset(offset)
        .all()
    )
    authors = usernames(
        db, [x.created_by_user_id for x in rows if x.created_by_user_id]
    )

    attendee_rows = (
        db.query(AuditAttendee)
        .filter(
            AuditAttendee.user_id == user.id,
            AuditAttendee.audit_id.in_([x.id for x in rows]),
        )
        .order_by(AuditAttendee.created_at.asc())
        .all()
        if rows
        else []
    )
    attendee_by_audit: dict[uuid.UUID, AuditAttendee] = {}
    for attendee in attendee_rows:
        attendee_by_audit.setdefault(attendee.audit_id, attendee)

    items = []
    for audit in rows:
        item = audit_summary(db, audit, authors)
        attendee = attendee_by_audit.get(audit.id)
        item["my_attendee"] = attendee_dict(attendee) if attendee is not None else None
        items.append(item)

    return {"total": total, "limit": limit, "offset": offset, "items": items}


def list_audit_attendee_users(*, db: Session, user: User):
    rows = (
        db.query(User)
        .filter(User.is_active.is_(True))
        .order_by(User.username.asc())
        .all()
    )
    return {
        "items": [
            {
                "id": str(u.id),
                "username": u.username,
                "email": user_email_snapshot(u) or "",
            }
            for u in rows
        ]
    }


def list_audits(
    *,
    framework: str | None,
    q: str | None,
    status: str | None,
    limit: int,
    offset: int,
    db: Session,
    user: User,
):
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    qry = db.query(Audit)
    if framework:
        qry = qry.filter(Audit.framework_slug == clean_framework(framework))
    if status:
        qry = qry.filter(
            Audit.status == clean_status(status, AUDIT_STATUSES, default="open")
        )
    else:
        qry = qry.filter(Audit.status != "template")
    if q:
        qq = f"%{q.strip()}%"
        qry = qry.filter(Audit.title.ilike(qq))
    total = int(qry.order_by(None).count())
    rows = qry.order_by(desc(Audit.updated_at)).limit(limit).offset(offset).all()
    authors = usernames(
        db, [x.created_by_user_id for x in rows if x.created_by_user_id]
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [audit_summary(db, x, authors) for x in rows],
    }


def get_audit_scope_coverage(
    *,
    framework: str | None,
    start: date | None,
    end: date | None,
    mode: str,
    db: Session,
    user: User,
):
    fw = clean_framework(framework)
    today = date.today()
    window_end = end or today
    window_start = start or (window_end - timedelta(days=365))
    if window_end < window_start:
        raise DomainError(status_code=400, detail="end cannot be before start")
    if (window_end - window_start).days > 3660:
        raise DomainError(
            status_code=400, detail="Coverage window cannot exceed 10 years"
        )
    coverage_mode = str(mode or "both").strip().lower()
    if coverage_mode not in {"completed", "scheduled", "both"}:
        raise DomainError(
            status_code=400, detail="mode must be completed, scheduled or both"
        )

    audits: list[Audit] = []
    scheduled_occurrences: list[tuple[Audit, date]] = []
    if coverage_mode in {"completed", "both"}:
        completed_qry = db.query(Audit).filter(
            Audit.framework_slug == fw,
            Audit.status == "completed",
            Audit.start_date.isnot(None),
        )
        audits.extend(
            date_overlaps(completed_qry, start=window_start, end=window_end)
            .order_by(Audit.start_date.asc(), Audit.title.asc())
            .all()
        )
    if coverage_mode in {"scheduled", "both"}:
        templates = (
            db.query(Audit)
            .filter(Audit.framework_slug == fw, Audit.status == "template")
            .order_by(Audit.schedule_next_run_date.asc().nullslast(), Audit.title.asc())
            .all()
        )
        for template in templates:
            dates = occurrences_between(
                template, start=window_start, end=window_end, limit=500
            )
            for occurrence in dates:
                scheduled_occurrences.append((template, occurrence))

    source_by_audit_id: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for audit in audits:
        source_by_audit_id.setdefault(audit.id, []).append(coverage_source_out(audit))
    for template, occurrence in scheduled_occurrences:
        source_by_audit_id.setdefault(template.id, []).append(
            coverage_source_out(template, occurrence_date=occurrence)
        )

    audit_ids = list(source_by_audit_id.keys())
    control_sources: dict[uuid.UUID, list[dict[str, Any]]] = {}
    clause_sources: dict[uuid.UUID, list[dict[str, Any]]] = {}
    document_sources: dict[uuid.UUID, list[dict[str, Any]]] = {}

    if audit_ids:
        for audit_id, control_id in (
            db.query(AuditScopedControl.audit_id, AuditScopedControl.control_item_id)
            .join(ControlItem, ControlItem.id == AuditScopedControl.control_item_id)
            .filter(AuditScopedControl.audit_id.in_(audit_ids))
            .filter(
                ControlItem.framework_slug == fw,
                ControlItem.type != "clause",
                ControlItem.in_scope == True,  # noqa: E712 - SQLAlchemy boolean expression
            )
            .all()
        ):
            control_sources.setdefault(control_id, []).extend(
                source_by_audit_id.get(audit_id, [])
            )
        scoped_clause_rows = (
            db.query(AuditScopedClause.audit_id, FrameworkClause)
            .join(FrameworkClause, FrameworkClause.id == AuditScopedClause.clause_id)
            .filter(AuditScopedClause.audit_id.in_(audit_ids))
            .filter(FrameworkClause.framework_slug == fw)
            .all()
        )
        for audit_id, clause in scoped_clause_rows:
            # Older audit scope rows may still contain parent clauses.  Scope
            # coverage reports reviewable leaf clauses only, so treat a parent
            # scoped clause as coverage for its leaf descendants rather than
            # emitting the parent heading itself.
            for leaf in selectable_clause_descendants(db, fw, clause):
                clause_sources.setdefault(leaf.id, []).extend(
                    source_by_audit_id.get(audit_id, [])
                )
        for audit_id, document_id in (
            db.query(
                AuditScopedIsmsDocument.audit_id, AuditScopedIsmsDocument.document_id
            )
            .filter(AuditScopedIsmsDocument.audit_id.in_(audit_ids))
            .all()
        ):
            document_sources.setdefault(document_id, []).extend(
                source_by_audit_id.get(audit_id, [])
            )

    controls = (
        db.query(ControlItem)
        .filter(
            ControlItem.framework_slug == fw,
            ControlItem.type != "clause",
            ControlItem.in_scope == True,  # noqa: E712 - SQLAlchemy boolean expression
        )
        .order_by(ControlItem.ref.asc())
        .all()
    )
    clauses = (
        db.query(FrameworkClause)
        .filter(FrameworkClause.framework_slug == fw)
        .filter(~FrameworkClause.children.any())
        .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
        .all()
    )
    documents = (
        db.query(IsmsDocument)
        .order_by(IsmsDocument.document_type.asc(), IsmsDocument.title.asc())
        .all()
    )

    def split(items: list[Any], source_map: dict[uuid.UUID, list[dict[str, Any]]]):
        included = []
        gaps = []
        for item in items:
            sources = source_map.get(item.id, [])
            out = coverage_item_out(item, sources)
            if sources:
                included.append(out)
            else:
                gaps.append(out)
        return included, gaps

    included_controls, gap_controls = split(controls, control_sources)
    included_clauses, gap_clauses = split(clauses, clause_sources)
    included_documents, gap_documents = split(documents, document_sources)

    return {
        "framework_slug": fw,
        "mode": coverage_mode,
        "start": window_start.isoformat(),
        "end": window_end.isoformat(),
        "source_audit_count": len(source_by_audit_id),
        "scheduled_occurrence_count": len(scheduled_occurrences),
        "summary": {
            "controls": {
                "total": len(controls),
                "included": len(included_controls),
                "gaps": len(gap_controls),
            },
            "clauses": {
                "total": len(clauses),
                "included": len(included_clauses),
                "gaps": len(gap_clauses),
            },
            "documents": {
                "total": len(documents),
                "included": len(included_documents),
                "gaps": len(gap_documents),
            },
        },
        "included": {
            "controls": included_controls,
            "clauses": included_clauses,
            "documents": included_documents,
        },
        "gaps": {
            "controls": gap_controls,
            "clauses": gap_clauses,
            "documents": gap_documents,
        },
    }


def list_audits_for_event(*, event_id: str, db: Session, user: User):
    eid = try_uuid(event_id)
    if not eid:
        raise DomainError(status_code=400, detail="event_id must be a UUID")
    can_see_event(db, user, eid)

    rows = (
        db.query(AuditEvidence)
        .join(Audit, Audit.id == AuditEvidence.audit_id)
        .filter(AuditEvidence.event_id == eid)
        .order_by(desc(Audit.updated_at), desc(AuditEvidence.added_at))
        .all()
    )
    user_ids = [
        x.audit.created_by_user_id
        for x in rows
        if x.audit is not None and x.audit.created_by_user_id
    ]
    user_ids += [x.added_by_user_id for x in rows if x.added_by_user_id]
    authors = usernames(db, list(set(user_ids)))

    return {
        "event_id": str(eid),
        "items": [
            {
                "audit": audit_summary(db, x.audit, authors),
                "evidence": {
                    "id": str(x.id),
                    "notes": x.notes or "",
                    "evidence_url": x.evidence_url,
                    "title": x.title,
                    "added_at": iso_dt(x.added_at),
                    "added_by_user_id": (
                        str(x.added_by_user_id) if x.added_by_user_id else None
                    ),
                    "added_by_username": (
                        authors.get(x.added_by_user_id, "")
                        if x.added_by_user_id
                        else ""
                    ),
                },
            }
            for x in rows
            if x.audit is not None
        ],
    }


def list_audits_for_entity(
    *, entity_type: str, entity_id: str, db: Session, user: User
):
    sampled_type = normalise_audit_sample_entity_type(entity_type)
    sampled_id = clean_optional_entity_id(entity_id)
    if not sampled_type or not sampled_id:
        raise DomainError(status_code=400, detail="entity_id must be a UUID")

    rows = (
        db.query(AuditEvidence)
        .join(Audit, Audit.id == AuditEvidence.audit_id)
        .filter(
            AuditEvidence.entity_type == sampled_type,
            AuditEvidence.entity_id == sampled_id,
        )
        .order_by(desc(Audit.updated_at), desc(AuditEvidence.added_at))
        .all()
    )
    readable_rows = [
        x for x in rows if x.audit is not None and can_read_audit(db, user, x.audit)
    ]
    user_ids = [
        x.audit.created_by_user_id
        for x in readable_rows
        if x.audit is not None and x.audit.created_by_user_id
    ]
    user_ids += [x.added_by_user_id for x in readable_rows if x.added_by_user_id]
    authors = usernames(db, list(set(user_ids)))

    return {
        "entity_type": sampled_type,
        "entity_id": str(sampled_id),
        "items": [
            {
                "audit": audit_summary(db, x.audit, authors),
                "evidence": {
                    "id": str(x.id),
                    "notes": x.notes or "",
                    "evidence_url": x.evidence_url,
                    "title": x.title,
                    "entity_type": x.entity_type,
                    "entity_id": str(x.entity_id) if x.entity_id else None,
                    "added_at": iso_dt(x.added_at),
                    "added_by_user_id": (
                        str(x.added_by_user_id) if x.added_by_user_id else None
                    ),
                    "added_by_username": (
                        authors.get(x.added_by_user_id, "")
                        if x.added_by_user_id
                        else ""
                    ),
                },
            }
            for x in readable_rows
        ],
    }


def get_audit_scope_map(*, audit_id: str, db: Session, user: User):
    a = audit_or_404(db, audit_id)
    require_audit_access(db, user, a)

    scoped_controls = (
        db.query(ControlItem)
        .join(AuditScopedControl, AuditScopedControl.control_item_id == ControlItem.id)
        .filter(AuditScopedControl.audit_id == a.id)
        .order_by(ControlItem.type.asc(), ControlItem.ref.asc())
        .all()
    )
    scoped_clauses = (
        db.query(FrameworkClause)
        .join(AuditScopedClause, AuditScopedClause.clause_id == FrameworkClause.id)
        .filter(AuditScopedClause.audit_id == a.id)
        .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
        .all()
    )
    scoped_documents = (
        db.query(IsmsDocument)
        .join(
            AuditScopedIsmsDocument,
            AuditScopedIsmsDocument.document_id == IsmsDocument.id,
        )
        .filter(AuditScopedIsmsDocument.audit_id == a.id)
        .order_by(IsmsDocument.document_type.asc(), IsmsDocument.title.asc())
        .all()
    )

    control_ids = [c.id for c in scoped_controls]
    clause_ids = [c.id for c in scoped_clauses]
    links: list[dict[str, Any]] = []
    if control_ids and clause_ids:
        rows = (
            db.query(ControlClauseLink, FrameworkClause, ControlItem)
            .join(FrameworkClause, FrameworkClause.id == ControlClauseLink.clause_id)
            .join(ControlItem, ControlItem.id == ControlClauseLink.control_item_id)
            .filter(ControlClauseLink.clause_id.in_(clause_ids))
            .filter(ControlClauseLink.control_item_id.in_(control_ids))
            .filter(FrameworkClause.framework_slug == a.framework_slug)
            .filter(ControlItem.framework_slug == a.framework_slug)
            .order_by(
                FrameworkClause.sort_order.asc(),
                FrameworkClause.ref.asc(),
                ControlItem.ref.asc(),
            )
            .all()
        )
        for link, clause, control in rows:
            links.append(
                {
                    "clause_id": str(clause.id),
                    "clause_ref": clause.ref,
                    "control_id": str(control.id),
                    "control_ref": control.ref,
                    "applicability": link.applicability,
                }
            )

    linked_control_ids = {x["control_id"] for x in links}
    linked_clause_ids = {x["clause_id"] for x in links}
    return {
        "audit_id": str(a.id),
        "framework_slug": a.framework_slug,
        "audit_type": a.audit_type or "internal",
        "clauses": [clause_out(c) for c in scoped_clauses],
        "controls": [control_out(c) for c in scoped_controls],
        "documents": [document_out(d) for d in scoped_documents],
        "swot": swot_scope_items(db, a.id),
        "links": links,
        "summary": {
            "clause_count": len(scoped_clauses),
            "control_count": len(scoped_controls),
            "document_count": len(scoped_documents),
            "relationship_count": len(links),
            "controls_linked_to_selected_clauses": len(linked_control_ids),
            "controls_in_scope_without_selected_clause_link": max(
                0, len(scoped_controls) - len(linked_control_ids)
            ),
            "clauses_without_selected_control_link": max(
                0, len(scoped_clauses) - len(linked_clause_ids)
            ),
        },
    }


def get_audit(*, audit_id: str, db: Session, user: User):
    a = audit_or_404(db, audit_id)
    require_audit_access(db, user, a)
    scoped_controls = (
        db.query(ControlItem)
        .join(AuditScopedControl, AuditScopedControl.control_item_id == ControlItem.id)
        .filter(AuditScopedControl.audit_id == a.id)
        .order_by(ControlItem.ref.asc())
        .all()
    )
    scoped_clauses = (
        db.query(FrameworkClause)
        .join(AuditScopedClause, AuditScopedClause.clause_id == FrameworkClause.id)
        .filter(AuditScopedClause.audit_id == a.id)
        .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
        .all()
    )
    scoped_documents = (
        db.query(IsmsDocument)
        .join(
            AuditScopedIsmsDocument,
            AuditScopedIsmsDocument.document_id == IsmsDocument.id,
        )
        .filter(AuditScopedIsmsDocument.audit_id == a.id)
        .order_by(IsmsDocument.document_type.asc(), IsmsDocument.title.asc())
        .all()
    )
    evidence = (
        db.query(AuditEvidence)
        .filter(AuditEvidence.audit_id == a.id)
        .order_by(desc(AuditEvidence.added_at))
        .all()
    )
    attendees = (
        db.query(AuditAttendee)
        .filter(AuditAttendee.audit_id == a.id)
        .order_by(AuditAttendee.name.asc())
        .all()
    )
    findings = (
        db.query(AuditFinding)
        .filter(AuditFinding.audit_id == a.id)
        .order_by(desc(AuditFinding.created_at))
        .all()
    )

    user_ids = [a.created_by_user_id] if a.created_by_user_id else []
    user_ids += [x.added_by_user_id for x in evidence if x.added_by_user_id]
    user_ids += [x.created_by_user_id for x in findings if x.created_by_user_id]
    authors = usernames(db, list(set(user_ids)))

    out = audit_summary(db, a, authors)
    out["scoped_controls"] = [control_out(c) for c in scoped_controls]
    out["scoped_clauses"] = [clause_out(c) for c in scoped_clauses]
    out["scoped_swot"] = swot_scope_items(db, a.id)
    out["scoped_documents"] = [document_out(d) for d in scoped_documents]
    out["evidence"] = [evidence_dict(db, x, authors) for x in evidence]
    out["attendees"] = [attendee_dict(x) for x in attendees]
    out["findings"] = [finding_dict(x, authors) for x in findings]
    return out
