from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import (
    Audit,
    AuditScopedClause,
    AuditScopedControl,
    AuditScopedIsmsDocument,
    ControlClauseLink,
    ControlItem,
    FrameworkClause,
    IsmsDocument,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
)
from app.services.audits.errors import AuditError
from app.services.audits.validation import try_uuid, utcnow


def control_or_404(db: Session, framework: str, control_id_or_ref: str) -> ControlItem:
    val = (control_id_or_ref or "").strip()
    cid = try_uuid(val)
    q = db.query(ControlItem).filter(ControlItem.framework_slug == framework)
    if cid:
        row = q.filter(ControlItem.id == cid).one_or_none()
    else:
        row = q.filter(ControlItem.ref == val).one_or_none()
    if not row:
        raise AuditError(status_code=404, detail=f"Control not found: {val}")
    return row


def clause_or_404(
    db: Session, framework: str, clause_id_or_ref: str
) -> FrameworkClause:
    val = (clause_id_or_ref or "").strip()
    cid = try_uuid(val)
    q = db.query(FrameworkClause).filter(FrameworkClause.framework_slug == framework)
    if cid:
        row = q.filter(FrameworkClause.id == cid).one_or_none()
    else:
        row = q.filter(FrameworkClause.ref == val).one_or_none()
    if not row:
        raise AuditError(status_code=404, detail=f"Clause not found: {val}")
    return row


def dedupe_scope_values(raw_values: list[str] | None, *, label: str) -> list[str]:
    vals: list[str] = []
    seen: set[str] = set()
    for raw in raw_values or []:
        v = (raw or "").strip()
        if not v or v in seen:
            continue
        seen.add(v)
        vals.append(v)
    if len(vals) > 1000:
        raise AuditError(status_code=400, detail=f"Too many {label}")
    return vals


def document_or_404(
    db: Session, framework: str, document_id_or_title: str
) -> IsmsDocument:
    val = (document_id_or_title or "").strip()
    did = try_uuid(val)
    q = db.query(IsmsDocument)
    row = (
        q.filter(IsmsDocument.id == did).one_or_none()
        if did
        else q.filter(IsmsDocument.title == val).one_or_none()
    )
    if not row:
        raise AuditError(status_code=404, detail=f"ISMS document not found: {val}")
    return row


def linked_scope_for_documents(
    db: Session, audit: Audit, documents: list[IsmsDocument]
) -> tuple[list[ControlItem], list[FrameworkClause]]:
    doc_ids = [x.id for x in documents or []]
    if not doc_ids:
        return [], []
    controls = (
        db.query(ControlItem)
        .join(
            IsmsEntityControlLink,
            IsmsEntityControlLink.control_item_id == ControlItem.id,
        )
        .filter(
            IsmsEntityControlLink.entity_type == "document",
            IsmsEntityControlLink.entity_id.in_(doc_ids),
            IsmsEntityControlLink.framework_slug == audit.framework_slug,
            ControlItem.framework_slug == audit.framework_slug,
        )
        .order_by(ControlItem.type.asc(), ControlItem.ref.asc())
        .all()
    )
    clauses = (
        db.query(FrameworkClause)
        .join(
            IsmsEntityClauseLink, IsmsEntityClauseLink.clause_id == FrameworkClause.id
        )
        .filter(
            IsmsEntityClauseLink.entity_type == "document",
            IsmsEntityClauseLink.entity_id.in_(doc_ids),
            IsmsEntityClauseLink.framework_slug == audit.framework_slug,
            FrameworkClause.framework_slug == audit.framework_slug,
        )
        .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
        .all()
    )
    return controls, clauses


def linked_controls_for_clauses(
    db: Session, audit: Audit, clause_ids: list[uuid.UUID]
) -> list[ControlItem]:
    if not clause_ids:
        return []
    return (
        db.query(ControlItem)
        .join(ControlClauseLink, ControlClauseLink.control_item_id == ControlItem.id)
        .filter(ControlClauseLink.clause_id.in_(clause_ids))
        .filter(ControlItem.framework_slug == audit.framework_slug)
        .order_by(ControlItem.type.asc(), ControlItem.ref.asc())
        .all()
    )


def selectable_clause_descendants(
    db: Session, framework: str, clause: FrameworkClause
) -> list[FrameworkClause]:
    """Return the selectable descendants for an audit-scope clause.

    Framework parent clauses such as ``8`` are headings/groupings. Controls are
    linked at the individual child-clause level, so audit scope should never
    persist a parent clause when that parent has children. Accepting a parent in
    API input is still useful: it becomes a shortcut for selecting all leaf
    descendants underneath that parent.
    """

    children = (
        db.query(FrameworkClause)
        .filter(FrameworkClause.framework_slug == framework)
        .filter(FrameworkClause.parent_clause_id == clause.id)
        .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
        .all()
    )
    if not children:
        return [clause]

    out: list[FrameworkClause] = []
    for child in children:
        out.extend(selectable_clause_descendants(db, framework, child))
    return out


def normalise_scope_clauses(
    db: Session, framework: str, clauses: list[FrameworkClause]
) -> list[FrameworkClause]:
    """Expand parent clauses to leaf/selectable clauses and deduplicate."""

    by_id: dict[uuid.UUID, FrameworkClause] = {}
    for clause in clauses:
        for selectable in selectable_clause_descendants(db, framework, clause):
            by_id.setdefault(selectable.id, selectable)
    out = list(by_id.values())
    out.sort(key=lambda c: (int(c.sort_order or 0), str(c.ref or "")))
    return out


def set_audit_scope(
    db: Session,
    audit: Audit,
    *,
    control_values: list[str] | None,
    clause_values: list[str] | None,
    document_values: list[str] | None = None,
    replace_controls: bool = True,
    replace_clauses: bool = True,
    replace_documents: bool = True,
) -> tuple[list[ControlItem], list[FrameworkClause], list[IsmsDocument]]:
    documents: list[IsmsDocument] = []
    if replace_documents:
        document_refs = dedupe_scope_values(document_values, label="documents")
        by_id: dict[uuid.UUID, IsmsDocument] = {}
        for d in [document_or_404(db, audit.framework_slug, v) for v in document_refs]:
            by_id[d.id] = d
        documents = sorted(
            by_id.values(),
            key=lambda d: (str(d.document_type or ""), str(d.title or "")),
        )
        db.query(AuditScopedIsmsDocument).filter(
            AuditScopedIsmsDocument.audit_id == audit.id
        ).delete()
    else:
        documents = (
            db.query(IsmsDocument)
            .join(
                AuditScopedIsmsDocument,
                AuditScopedIsmsDocument.document_id == IsmsDocument.id,
            )
            .filter(AuditScopedIsmsDocument.audit_id == audit.id)
            .order_by(IsmsDocument.document_type.asc(), IsmsDocument.title.asc())
            .all()
        )

    document_controls, document_clauses = linked_scope_for_documents(
        db, audit, documents
    )
    effective_replace_clauses = replace_clauses or bool(
        replace_documents and document_clauses
    )
    effective_replace_controls = replace_controls or bool(
        replace_documents and (document_controls or document_clauses)
    )

    controls: list[ControlItem] = []
    clauses: list[FrameworkClause] = []

    if effective_replace_clauses:
        requested_clauses: list[FrameworkClause] = []
        if replace_clauses:
            clause_refs = dedupe_scope_values(clause_values, label="clauses")
            requested_clauses.extend(
                [clause_or_404(db, audit.framework_slug, v) for v in clause_refs]
            )
        else:
            requested_clauses.extend(
                db.query(FrameworkClause)
                .join(
                    AuditScopedClause, AuditScopedClause.clause_id == FrameworkClause.id
                )
                .filter(AuditScopedClause.audit_id == audit.id)
                .all()
            )
        requested_clauses.extend(document_clauses)
        clauses = normalise_scope_clauses(db, audit.framework_slug, requested_clauses)
        db.query(AuditScopedClause).filter(
            AuditScopedClause.audit_id == audit.id
        ).delete()

    if effective_replace_controls:
        control_by_id: dict[uuid.UUID, ControlItem] = {}
        if replace_controls:
            control_refs = dedupe_scope_values(control_values, label="controls")
            for c in [
                control_or_404(db, audit.framework_slug, v) for v in control_refs
            ]:
                control_by_id[c.id] = c
        else:
            for c in (
                db.query(ControlItem)
                .join(
                    AuditScopedControl,
                    AuditScopedControl.control_item_id == ControlItem.id,
                )
                .filter(AuditScopedControl.audit_id == audit.id)
                .all()
            ):
                control_by_id[c.id] = c
        for c in document_controls:
            control_by_id.setdefault(c.id, c)

        auto_clause_ids = (
            [x.id for x in clauses]
            if effective_replace_clauses
            else [
                x[0]
                for x in db.query(AuditScopedClause.clause_id)
                .filter(AuditScopedClause.audit_id == audit.id)
                .all()
            ]
        )
        if auto_clause_ids:
            for c in linked_controls_for_clauses(db, audit, auto_clause_ids):
                control_by_id.setdefault(c.id, c)

        controls = list(control_by_id.values())
        controls.sort(key=lambda c: (str(c.type or ""), str(c.ref or "")))
        db.query(AuditScopedControl).filter(
            AuditScopedControl.audit_id == audit.id
        ).delete()

    now = utcnow()
    if effective_replace_controls:
        for c in controls:
            db.add(
                AuditScopedControl(
                    audit_id=audit.id, control_item_id=c.id, created_at=now
                )
            )
    if effective_replace_clauses:
        for c in clauses:
            db.add(AuditScopedClause(audit_id=audit.id, clause_id=c.id, created_at=now))
    if replace_documents:
        for d in documents:
            db.add(
                AuditScopedIsmsDocument(
                    audit_id=audit.id, document_id=d.id, created_at=now
                )
            )

    audit.updated_at = now
    db.add(audit)
    return controls, clauses, documents
