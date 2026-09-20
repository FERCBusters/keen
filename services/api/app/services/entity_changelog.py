from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from app.api.utils import (
    control_justification as _control_justification,
    control_upstream_url as _control_upstream_url,
    ref_sort_key as _ref_sort_key,
)
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    EntityChangelog,
    FrameworkClause,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    RiskControlLink,
    PestleBusinessProcess,
    PestleBusinessProcessRelevance,
    PestleClauseRelevance,
    PestleItem,
    User,
)

_CHANGE_FIELD_LABELS = {
    "agenda_minutes_notes": "Agenda/minutes/notes",
    "application_configurations": "Application configuration",
    "access_control_matrix": "Access Control Matrix",
    "account_id": "hosting account ID",
    "aws_accounts": "managed hosting accounts",
    "aws_account_ids": "managed hosting accounts",
    "approved_by": "Approved by",
    "approved_by_user_id": "Approved by",
    "asset": "Asset",
    "attendees": "Attendees",
    "apologies": "Apologies",
    "business_process": "Business process",
    "completion_method": "Completion method",
    "completion_target_date": "Completion target date",
    "controls": "Linked controls",
    "clauses": "Linked clauses",
    "description": "Description",
    "document_type": "Document type",
    "documents": "Documents",
    "end_time": "End time",
    "evaluation_method": "Evaluation method",
    "external_url": "External URL",
    "filename": "Uploaded file",
    "goal": "Goal",
    "license": "License",
    "license_id": "License",
    "license_name": "License",
    "links": "Supporting links",
    "metric": "Metric",
    "node_type": "Node type",
    "org_node": "Organisation chart node",
    "owner_org_node": "Owner",
    "owner_org_node_id": "Owner",
    "people": "People",
    "register_held_by_org_node": "Detailed asset register held by",
    "register_held_by_org_node_id": "Detailed asset register held by",
    "requirement": "Requirement",
    "resource_requirements_text": "Resource requirements text",
    "resource_users": "Resource users",
    "resource_user_ids": "Resource users",
    "source": "Source",
    "source_type": "Source type",
    "service": "Service",
    "service_asset_id": "Service",
    "task_action": "Task/Action",
    "role": "Role",
    "roles": "Roles",
    "role_org_node_id": "Role",
    "role_org_node_ids": "Roles",
    "start_time": "Start time",
    "status": "Status",
    "value": "Value",
    "effectiveness_measure": "Effectiveness measure",
    "metric_entries": "Metric entries",
    "metric_key": "Metric key",
    "period_start": "Period start",
    "period_end": "Period end",
    "recorded_at": "Recorded at",
    "metric_value": "Metric value",
    "metric_unit": "Metric unit",
    "qualitative_value": "Qualitative value",
    "source_title": "Source title",
    "source_url": "Source URL",
    "source_reference": "Source reference",
    "source_event_id": "Source event",
    "target_value": "Target value",
    "target_unit": "Target unit",
    "threshold_operator": "Threshold operator",
    "frequency": "Frequency",
    "asset": "Asset",
    "category": "Category",
    "clauses": "Linked clauses",
    "controls": "Linked controls",
    "evidence_mappings": "Evidence mappings",
    "framework": "Framework",
    "impact_score": "Impact score",
    "nature": "Nature of Interest",
    "lens": "Lens",
    "item": "Item",
    "overall_relevance": "Overall relevance",
    "rationale": "Rationale",
    "business_processes": "Business process relevance",
    "communications": "Communications",
    "mitigator_context": "Keen Mitigator context",
    "in_scope": "Scope",
    "justification": "Justification",
    "note": "Note",
    "owner": "Owner",
    "owner_user_id": "Owner",
    "parent": "Parent clause",
    "ref": "Reference",
    "residual_impact_score": "Residual impact score",
    "residual_risk_score": "Residual risk score",
    "residual_vulnerability_score": "Residual vulnerability score",
    "risk_score": "Risk score",
    "risk_types": "Risk types",
    "subcategory": "Subcategory",
    "threat_score": "Threat score",
    "threat_summary": "Threat summary",
    "title": "Title",
    "type": "Type",
    "upstream_url": "Upstream guidance URL",
    "vulnerability_score": "Vulnerability score",
}

_ENTITY_LABELS = {
    "isms_objective": "ISMS Objective",
    "isms_document": "ISMS Document",
    "isms_org_node": "ISMS Organisation Chart node",
    "isms_asset": "ISMS Asset",
    "isms_license": "ISMS License",
    "isms_business_process": "ISMS Business Process",
    "isms_application_configuration": "ISMS Application Configuration",
    "isms_access_control_matrix": "ISMS Access Control Matrix",
    "isms_aws_account": "ISMS Managed Hosting Account",
    "isms_meeting": "ISMS Meeting",
    "isms_effectiveness_measure": "ISMS Effectiveness Measure",
    "isms_effectiveness_metric": "ISMS Effectiveness Metric",
    "control": "Control",
    "clause": "Clause",
    "risk": "CIA Triad Risk",
    "pestle_item": "PESTLE(E) item",
    "pestle_business_process": "PESTLE(E) business process",
    "interested_party": "Interested Party",
}


def _stringify_id(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _clean_scalar(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(k): _clean_scalar(v)
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, (list, tuple, set)):
        return [_clean_scalar(v) for v in value]
    return value


def _norm(value: Any) -> Any:
    value = _clean_scalar(value)
    if isinstance(value, list):
        # Keep intentionally ordered lists readable, but normalize list-of-dicts
        # by their stable display key so diff comparisons do not oscillate.
        if all(isinstance(x, dict) for x in value):
            return sorted(
                value,
                key=lambda x: (
                    str(
                        x.get("ref")
                        or x.get("name")
                        or x.get("title")
                        or x.get("id")
                        or ""
                    ),
                    str(x.get("applicability") or x.get("url") or ""),
                ),
            )
        return value
    if value == "":
        return None
    return value


def _field_label(field: str) -> str:
    return _CHANGE_FIELD_LABELS.get(field, field.replace("_", " ").title())


def _truncate_db_text(value: Any, max_len: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1].rstrip() + "…"


def diff_states(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> list[dict[str, Any]]:
    before = before or {}
    after = after or {}
    skip = {"id", "display", "created_at", "updated_at"}
    fields = sorted((set(before.keys()) | set(after.keys())) - skip)
    changes: list[dict[str, Any]] = []
    for field in fields:
        old = _norm(before.get(field))
        new = _norm(after.get(field))
        if old == new:
            continue
        changes.append(
            {
                "field": field,
                "label": _field_label(field),
                "before": old,
                "after": new,
            }
        )
    return changes


def _entity_display(
    entity_type: str, state: dict[str, Any] | None
) -> tuple[str | None, str | None]:
    state = state or {}
    ref = (
        state.get("ref")
        or state.get("metric_key")
        or state.get("asset")
        or state.get("task_action")
        or state.get("type")
        or state.get("name")
        or state.get("display")
    )
    title = (
        state.get("title")
        or state.get("summary")
        or state.get("threat_summary")
        or state.get("task_action")
        or state.get("item")
        or state.get("name")
        or state.get("display")
    )
    return (_truncate_db_text(ref, 128), _truncate_db_text(title, 512))


def _summarize(entity_type: str, action: str, changes: list[dict[str, Any]]) -> str:
    label = _ENTITY_LABELS.get(entity_type, entity_type.title())
    if action == "created":
        return f"{label} created"
    if action == "deleted":
        return f"{label} deleted"
    if not changes:
        return f"{label} saved with no semantic changes"
    names = [str(c.get("label") or c.get("field")) for c in changes[:4]]
    suffix = "" if len(changes) <= 4 else f" and {len(changes) - 4} more"
    return f"Updated {', '.join(names)}{suffix}"


def record_entity_changelog(
    db: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID | str,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    user: User | None = None,
    request_method: str | None = None,
    request_path: str | None = None,
) -> EntityChangelog | None:
    """Record an immutable semantic change for KEEN relationship entities.

    The regular AuditLog captures request metadata; this captures the before/after
    values users need for ISO-style evidence of what actually changed.
    """
    entity_type = str(entity_type or "").strip().lower()
    if entity_type not in _ENTITY_LABELS:
        raise ValueError(f"Unsupported changelog entity_type: {entity_type}")
    eid = entity_id if isinstance(entity_id, uuid.UUID) else uuid.UUID(str(entity_id))

    clean_before = _clean_scalar(before) if before is not None else None
    clean_after = _clean_scalar(after) if after is not None else None
    if action == "updated":
        changes = diff_states(clean_before, clean_after)
        if not changes:
            return None
    elif action == "created":
        changes = diff_states(None, clean_after)
    elif action == "deleted":
        changes = diff_states(clean_before, None)
    else:
        changes = diff_states(clean_before, clean_after)
        if not changes:
            return None

    display_state = clean_after or clean_before or {}
    entity_ref, entity_title = _entity_display(entity_type, display_state)
    entry = EntityChangelog(
        entity_type=entity_type,
        entity_id=eid,
        entity_ref=entity_ref,
        entity_title=entity_title,
        action=action,
        summary=_summarize(entity_type, action, changes),
        before_state=clean_before,
        after_state=clean_after,
        changes=changes,
        changed_by_user_id=getattr(user, "id", None),
        changed_by_username=getattr(user, "username", None),
        request_method=(request_method or None),
        request_path=(request_path or None),
        changed_at=datetime.utcnow(),
    )
    db.add(entry)
    db.flush()
    return entry


def _control_clause_links(db: Session, control_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        db.query(ControlClauseLink)
        .join(FrameworkClause, FrameworkClause.id == ControlClauseLink.clause_id)
        .filter(ControlClauseLink.control_item_id == control_id)
        .all()
    )
    out: list[dict[str, Any]] = []
    for link in rows:
        clause = link.clause
        out.append(
            {
                "id": str(clause.id),
                "ref": clause.ref,
                "title": clause.title,
                "applicability": link.applicability,
            }
        )
    out.sort(key=lambda x: (_ref_sort_key(x.get("ref") or ""), x.get("title") or ""))
    return out


def control_changelog_state(
    db: Session, control_or_id: ControlItem | uuid.UUID | str
) -> dict[str, Any] | None:
    control = control_or_id if isinstance(control_or_id, ControlItem) else None
    if control is None:
        cid = (
            control_or_id
            if isinstance(control_or_id, uuid.UUID)
            else uuid.UUID(str(control_or_id))
        )
        control = db.query(ControlItem).filter(ControlItem.id == cid).one_or_none()
    if control is None:
        return None
    return {
        "id": str(control.id),
        "framework": control.framework_slug,
        "type": control.type,
        "ref": control.ref,
        "title": control.title,
        "in_scope": bool(control.in_scope),
        "justification": _control_justification(control),
        "upstream_url": _control_upstream_url(control),
        "clauses": _control_clause_links(db, control.id),
        "created_at": control.created_at.isoformat() if control.created_at else None,
    }


def _clause_controls(db: Session, clause_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        db.query(ControlClauseLink)
        .join(ControlItem, ControlItem.id == ControlClauseLink.control_item_id)
        .filter(ControlClauseLink.clause_id == clause_id)
        .all()
    )
    out: list[dict[str, Any]] = []
    for link in rows:
        control = link.control
        out.append(
            {
                "id": str(control.id),
                "ref": control.ref,
                "title": control.title,
                "applicability": link.applicability,
            }
        )
    out.sort(key=lambda x: (_ref_sort_key(x.get("ref") or ""), x.get("title") or ""))
    return out


def _clause_evidence_mappings(clause: FrameworkClause) -> list[dict[str, str]]:
    meta = dict(getattr(clause, "meta", None) or {})
    raw = meta.get("evidence_mappings")
    if raw is None:
        raw = (
            meta.get("evidence_urls")
            or meta.get("evidenceUrls")
            or meta.get("evidence_links")
            or meta.get("evidence")
        )
    if isinstance(raw, str):
        raw = [line.strip() for line in raw.splitlines() if line.strip()]
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            url = str(
                item.get("url") or item.get("href") or item.get("link") or ""
            ).strip()
            title = str(
                item.get("title") or item.get("text") or item.get("label") or ""
            ).strip()
        else:
            url = str(item or "").strip()
            title = ""
        if url:
            out.append({"title": title or url, "url": url})
    out.sort(key=lambda x: (x.get("title") or "", x.get("url") or ""))
    return out


def clause_changelog_state(
    db: Session, clause_or_id: FrameworkClause | uuid.UUID | str
) -> dict[str, Any] | None:
    clause = clause_or_id if isinstance(clause_or_id, FrameworkClause) else None
    if clause is None:
        cid = (
            clause_or_id
            if isinstance(clause_or_id, uuid.UUID)
            else uuid.UUID(str(clause_or_id))
        )
        clause = (
            db.query(FrameworkClause).filter(FrameworkClause.id == cid).one_or_none()
        )
    if clause is None:
        return None
    parent = clause.parent
    return {
        "id": str(clause.id),
        "framework": clause.framework_slug,
        "ref": clause.ref,
        "title": clause.title,
        "parent": f"{parent.ref} {parent.title}".strip() if parent else None,
        "upstream_url": _control_upstream_url(clause),
        "evidence_mappings": _clause_evidence_mappings(clause),
        "controls": _clause_controls(db, clause.id),
        "created_at": clause.created_at.isoformat() if clause.created_at else None,
        "updated_at": clause.updated_at.isoformat() if clause.updated_at else None,
    }


def _risk_controls(
    db: Session, risk_id: uuid.UUID, framework: str | None = None
) -> list[dict[str, Any]]:
    qry = (
        db.query(RiskControlLink)
        .join(ControlItem, ControlItem.id == RiskControlLink.control_item_id)
        .filter(RiskControlLink.risk_id == risk_id)
    )
    if framework:
        qry = qry.filter(RiskControlLink.framework_slug == framework)
    rows = qry.all()
    out: list[dict[str, Any]] = []
    for link in rows:
        control = link.control
        out.append(
            {
                "id": str(control.id),
                "framework": link.framework_slug,
                "ref": control.ref,
                "title": control.title,
            }
        )
    out.sort(
        key=lambda x: (x.get("framework") or "", _ref_sort_key(x.get("ref") or ""))
    )
    return out


def risk_changelog_state(
    db: Session, risk_or_id: Risk | uuid.UUID | str, *, framework: str | None = None
) -> dict[str, Any] | None:
    risk = risk_or_id if isinstance(risk_or_id, Risk) else None
    if risk is None:
        rid = (
            risk_or_id
            if isinstance(risk_or_id, uuid.UUID)
            else uuid.UUID(str(risk_or_id))
        )
        risk = db.query(Risk).filter(Risk.id == rid).one_or_none()
    if risk is None:
        return None
    asset = risk.asset
    category = asset.category if asset else None
    subcategory = asset.subcategory if asset else None
    owner = risk.owner
    asset_name = asset.name if asset else None
    threat = (risk.threat_summary or "").strip()
    return {
        "id": str(risk.id),
        "framework": framework,
        "display": (
            f"{asset_name or 'Risk'} — {threat}" if threat else (asset_name or "Risk")
        ),
        "asset": asset_name,
        "category": category.name if category else None,
        "subcategory": subcategory.name if subcategory else None,
        "risk_types": list(risk.risk_types or []),
        "owner": owner.username if owner else None,
        "threat_summary": risk.threat_summary,
        "threat_score": risk.threat_score,
        "vulnerability_score": risk.vulnerability_score,
        "impact_score": risk.impact_score,
        "risk_score": risk.risk_score,
        "residual_vulnerability_score": risk.residual_vulnerability_score,
        "residual_impact_score": risk.residual_impact_score,
        "residual_risk_score": risk.residual_risk_score,
        "note": risk.note,
        "mitigator_context": risk.mitigator_context or {},
        "controls": _risk_controls(db, risk.id, framework=framework),
        "created_at": risk.created_at.isoformat() if risk.created_at else None,
        "updated_at": risk.updated_at.isoformat() if risk.updated_at else None,
    }


def _pestle_relevance_label(link: Any) -> str | None:
    rel = getattr(link, "relevance", None)
    if rel is None:
        return None
    return str(getattr(rel, "label", None) or getattr(rel, "code", None) or "") or None


def _pestle_business_process_links(
    db: Session, item_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        db.query(PestleBusinessProcessRelevance)
        .filter(PestleBusinessProcessRelevance.pestle_item_id == item_id)
        .all()
    )
    out: list[dict[str, Any]] = []
    for link in rows:
        proc = link.business_process
        out.append(
            {
                "id": str(proc.id),
                "name": proc.name,
                "relevance": _pestle_relevance_label(link),
            }
        )
    out.sort(key=lambda x: (x.get("name") or "").lower())
    return out


def _pestle_clause_links(db: Session, item_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        db.query(PestleClauseRelevance)
        .filter(PestleClauseRelevance.pestle_item_id == item_id)
        .all()
    )
    out: list[dict[str, Any]] = []
    for link in rows:
        clause = link.clause
        out.append(
            {
                "id": str(clause.id),
                "ref": clause.ref,
                "title": clause.title,
                "relevance": _pestle_relevance_label(link),
            }
        )
    out.sort(key=lambda x: (_ref_sort_key(x.get("ref") or ""), x.get("title") or ""))
    return out


def pestle_item_changelog_state(
    db: Session, item_or_id: PestleItem | uuid.UUID | str
) -> dict[str, Any] | None:
    item = item_or_id if isinstance(item_or_id, PestleItem) else None
    if item is None:
        iid = (
            item_or_id
            if isinstance(item_or_id, uuid.UUID)
            else uuid.UUID(str(item_or_id))
        )
        item = db.query(PestleItem).filter(PestleItem.id == iid).one_or_none()
    if item is None:
        return None
    rel = item.overall_relevance
    return {
        "id": str(item.id),
        "framework": item.framework_slug,
        "type": item.type,
        "lens": item.lens,
        "item": item.item,
        "overall_relevance": getattr(rel, "label", None) or getattr(rel, "code", None),
        "rationale": item.rationale,
        "business_processes": _pestle_business_process_links(db, item.id),
        "clauses": _pestle_clause_links(db, item.id),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def pestle_business_process_changelog_state(
    db: Session, process_or_id: PestleBusinessProcess | uuid.UUID | str
) -> dict[str, Any] | None:
    process = (
        process_or_id if isinstance(process_or_id, PestleBusinessProcess) else None
    )
    if process is None:
        pid = (
            process_or_id
            if isinstance(process_or_id, uuid.UUID)
            else uuid.UUID(str(process_or_id))
        )
        process = (
            db.query(PestleBusinessProcess)
            .filter(PestleBusinessProcess.id == pid)
            .one_or_none()
        )
    if process is None:
        return None
    links: list[dict[str, Any]] = []
    for link in list(process.pestle_links or []):
        item = link.pestle_item
        links.append(
            {
                "id": str(item.id),
                "type": item.type,
                "lens": item.lens,
                "item": item.item,
                "relevance": _pestle_relevance_label(link),
            }
        )
    links.sort(
        key=lambda x: (x.get("type") or "", x.get("lens") or "", x.get("item") or "")
    )
    return {
        "id": str(process.id),
        "name": process.name,
        "business_processes": links,
        "created_at": process.created_at.isoformat() if process.created_at else None,
        "updated_at": process.updated_at.isoformat() if process.updated_at else None,
    }


def changelog_item_out(row: EntityChangelog) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "entity_type": row.entity_type,
        "entity_id": str(row.entity_id),
        "entity_ref": row.entity_ref,
        "entity_title": row.entity_title,
        "action": row.action,
        "summary": row.summary,
        "changes": row.changes or [],
        "changed_at": row.changed_at.isoformat() if row.changed_at else None,
        "changed_by_user_id": (
            str(row.changed_by_user_id) if row.changed_by_user_id else None
        ),
        "changed_by_username": row.changed_by_username,
        "request_method": row.request_method,
        "request_path": row.request_path,
    }


def list_entity_changelogs(
    db: Session,
    *,
    entity_type: str | None = None,
    entity_id: uuid.UUID | str | None = None,
    username: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    qry = db.query(EntityChangelog)
    if entity_type:
        qry = qry.filter(EntityChangelog.entity_type == entity_type.strip().lower())
    if entity_id:
        eid = (
            entity_id if isinstance(entity_id, uuid.UUID) else uuid.UUID(str(entity_id))
        )
        qry = qry.filter(EntityChangelog.entity_id == eid)
    if username:
        qry = qry.filter(
            EntityChangelog.changed_by_username.ilike(f"%{username.strip()}%")
        )
    if q:
        needle = f"%{q.strip()}%"
        qry = qry.filter(
            or_(
                EntityChangelog.entity_ref.ilike(needle),
                EntityChangelog.entity_title.ilike(needle),
                EntityChangelog.summary.ilike(needle),
            )
        )
    total = int(qry.order_by(None).count() or 0)
    rows = (
        qry.order_by(desc(EntityChangelog.changed_at)).limit(limit).offset(offset).all()
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [changelog_item_out(r) for r in rows],
    }


def affected_clause_ids_for_control(
    db: Session, control_id: uuid.UUID, selected_clause_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    current = {
        cid
        for (cid,) in db.query(ControlClauseLink.clause_id)
        .filter(ControlClauseLink.control_item_id == control_id)
        .all()
    }
    return current | set(selected_clause_ids or [])


def affected_control_ids_for_clause(
    db: Session, clause_id: uuid.UUID, selected_control_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    current = {
        cid
        for (cid,) in db.query(ControlClauseLink.control_item_id)
        .filter(ControlClauseLink.clause_id == clause_id)
        .all()
    }
    return current | set(selected_control_ids or [])
