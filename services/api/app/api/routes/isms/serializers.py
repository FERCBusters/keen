"""ISMS serializers; see docs/maintainability-review.md for module boundaries."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.utils import ref_sort_key as _ref_sort_key
from app.db.models import (
    ControlItem,
    FrameworkClause,
    IsmsAccessControlMatrixEntry,
    IsmsApplicationConfigurationEntry,
    IsmsAwsAccount,
    IsmsBusinessProcess,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
    IsmsLicense,
    IsmsMeeting,
    IsmsMeetingPerson,
    IsmsObjective,
    IsmsOrgNode,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    User,
)

from .constants import (
    EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE,
)


def _user_summary(u: User | None) -> dict[str, Any] | None:
    if not u:
        return None
    return {"id": str(u.id), "username": u.username, "email": getattr(u, "email", None)}


def _control_out(c: ControlItem | None) -> dict[str, Any]:
    if not c:
        return {}
    return {
        "id": str(c.id),
        "framework": c.framework_slug,
        "type": c.type,
        "ref": c.ref,
        "title": c.title,
        "in_scope": bool(c.in_scope),
    }


def _clause_out(c: FrameworkClause | None) -> dict[str, Any]:
    if not c:
        return {}
    return {
        "id": str(c.id),
        "framework": c.framework_slug,
        "ref": c.ref,
        "title": c.title,
        "parent_id": str(c.parent_clause_id) if c.parent_clause_id else None,
    }


def _org_node_out(
    row: IsmsOrgNode | None, *, include_people: bool = True
) -> dict[str, Any] | None:
    if row is None:
        return None
    people = []
    if include_people:
        people = [
            {
                "relationship_type": link.relationship_type,
                "user": _user_summary(link.user),
            }
            for link in list(row.users or [])
        ]
    return {
        "id": str(row.id),
        "parent_id": str(row.parent_id) if row.parent_id else None,
        "parent": (
            {"id": str(row.parent.id), "name": row.parent.name}
            if getattr(row, "parent", None)
            else None
        ),
        "name": row.name,
        "node_type": row.node_type,
        "description": row.description or "",
        "sort_order": int(row.sort_order or 0),
        "people": people,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _business_process_out(row: IsmsBusinessProcess | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description or "",
        "sort_order": int(row.sort_order or 0),
        "is_default": bool(row.is_default),
    }


def _linked_controls(
    db: Session, entity_type: str, entity_id: uuid.UUID, framework: str
) -> list[dict[str, Any]]:
    rows = (
        db.query(ControlItem)
        .join(
            IsmsEntityControlLink,
            IsmsEntityControlLink.control_item_id == ControlItem.id,
        )
        .filter(
            IsmsEntityControlLink.entity_type == entity_type,
            IsmsEntityControlLink.entity_id == entity_id,
            IsmsEntityControlLink.framework_slug == framework,
            ControlItem.framework_slug == framework,
        )
        .all()
    )
    rows.sort(key=lambda c: (c.type, _ref_sort_key(c.ref)))
    return [_control_out(c) for c in rows]


def _linked_clauses(
    db: Session, entity_type: str, entity_id: uuid.UUID, framework: str
) -> list[dict[str, Any]]:
    rows = (
        db.query(FrameworkClause)
        .join(
            IsmsEntityClauseLink, IsmsEntityClauseLink.clause_id == FrameworkClause.id
        )
        .filter(
            IsmsEntityClauseLink.entity_type == entity_type,
            IsmsEntityClauseLink.entity_id == entity_id,
            IsmsEntityClauseLink.framework_slug == framework,
            FrameworkClause.framework_slug == framework,
        )
        .all()
    )
    rows.sort(key=lambda c: (int(c.sort_order or 0), _ref_sort_key(c.ref)))
    return [_clause_out(c) for c in rows]


def _attach_links(
    db: Session,
    out: dict[str, Any],
    entity_type: str,
    entity_id: uuid.UUID,
    framework: str,
    include_links: bool,
) -> dict[str, Any]:
    if include_links:
        out["controls"] = _linked_controls(db, entity_type, entity_id, framework)
        out["clauses"] = _linked_clauses(db, entity_type, entity_id, framework)
        out["control_count"] = len(out["controls"])
        out["clause_count"] = len(out["clauses"])
    return out


def _objective_out(
    db: Session, row: IsmsObjective, framework: str, *, include_links: bool = True
) -> dict[str, Any]:
    resource_users = [
        _user_summary(link.user) for link in list(row.resource_users or []) if link.user
    ]
    out = {
        "id": str(row.id),
        "requirement": row.requirement or "",
        "goal": row.goal or "",
        "metric": row.metric or "",
        "completion_method": row.completion_method or "",
        "resource_requirements_text": row.resource_requirements_text or "",
        "resource_users": resource_users,
        "resource_user_ids": [u["id"] for u in resource_users if u],
        "owner": _user_summary(row.owner),
        "owner_user_id": str(row.owner_user_id) if row.owner_user_id else None,
        "completion_target_date": row.completion_target_date or None,
        "evaluation_method": row.evaluation_method or "",
        "status": row.status or "not_started",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "display": row.goal or row.requirement or "ISMS Objective",
    }
    return _attach_links(db, out, "objective", row.id, framework, include_links)


def _document_out(
    db: Session, row: IsmsDocument, framework: str, *, include_links: bool = True
) -> dict[str, Any]:
    out = {
        "id": str(row.id),
        "title": row.title,
        "document_type": row.document_type,
        "description": row.description or "",
        "external_url": row.external_url,
        "folder_id": str(row.folder_id) if row.folder_id else None,
        "tags": row.tags or [],
        "content_html": row.content_html or "",
        "content_version": row.content_version or 0,
        "has_file": bool(row.storage_uri),
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else None,
        "uploaded_by": _user_summary(row.uploaded_by),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "display": row.title,
    }
    return _attach_links(db, out, "document", row.id, framework, include_links)


def _asset_category_out(row: RiskCategory | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"id": str(row.id), "name": row.name}


def _asset_subcategory_out(row: RiskAssetSubcategory | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": str(row.id),
        "name": row.name,
        "category_id": str(row.category_id),
    }


def _asset_categories_out(db: Session) -> list[dict[str, Any]]:
    rows = db.query(RiskCategory).order_by(func.lower(RiskCategory.name).asc()).all()
    return [
        {
            "id": str(row.id),
            "name": row.name,
            "subcategories": [
                _asset_subcategory_out(sc)
                for sc in sorted(
                    row.asset_subcategories or [], key=lambda x: x.name.lower()
                )
            ],
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]


def _asset_display_name(row: RiskAsset | None) -> str:
    return row.name if row else "Asset"


def _license_out(
    row: IsmsLicense | None, *, include_asset_count: bool = False
) -> dict[str, Any] | None:
    if row is None:
        return None
    out = {
        "id": str(row.id),
        "name": row.name,
        "description": row.description or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "display": row.name,
    }
    if include_asset_count:
        out["asset_count"] = len(row.assets or [])
    return out


def _asset_out(
    db: Session, row: RiskAsset, framework: str, *, include_links: bool = True
) -> dict[str, Any]:
    category = row.category
    subcategory = row.subcategory
    out = {
        "id": str(row.id),
        # Keep both names so older ISMS UI code and the CIA Triad risk UI can
        # consume the same canonical asset records.
        "asset": row.name,
        "name": row.name,
        "category": _asset_category_out(category),
        "category_id": str(row.category_id) if row.category_id else None,
        "category_name": category.name if category else "",
        "subcategory": _asset_subcategory_out(subcategory),
        "subcategory_id": str(row.subcategory_id) if row.subcategory_id else None,
        "subcategory_name": subcategory.name if subcategory else "",
        "license": (
            row.license_entity.name if row.license_entity else (row.license or "")
        ),
        "license_id": str(row.license_id) if row.license_id else None,
        "license_name": (
            row.license_entity.name if row.license_entity else (row.license or "")
        ),
        "license_entity": _license_out(row.license_entity),
        "owner_org_node": _org_node_out(row.owner_org_node, include_people=False),
        "owner_org_node_id": (
            str(row.owner_org_node_id) if row.owner_org_node_id else None
        ),
        "register_held_by_org_node": _org_node_out(
            row.register_held_by_org_node, include_people=False
        ),
        "register_held_by_org_node_id": (
            str(row.register_held_by_org_node_id)
            if row.register_held_by_org_node_id
            else None
        ),
        "description": row.description or "",
        "risk_count": len(row.risks or []),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "display": row.name,
    }
    return _attach_links(db, out, "asset", row.id, framework, include_links)


def _app_config_source(row: IsmsApplicationConfigurationEntry) -> dict[str, Any]:
    if row.source_type == "document":
        return {
            "id": str(row.document_id) if row.document_id else None,
            "label": row.document.title if row.document else "Document",
        }
    if row.source_type == "person":
        return {
            "id": str(row.user_id) if row.user_id else None,
            "label": row.user.username if row.user else "Person",
        }
    if row.source_type == "asset":
        return {
            "id": str(row.asset_id) if row.asset_id else None,
            "label": _asset_display_name(row.asset),
        }
    return {
        "id": str(row.org_node_id) if row.org_node_id else None,
        "label": row.org_node.name if row.org_node else "Organisation chart node",
    }


def _app_config_out(
    db: Session,
    row: IsmsApplicationConfigurationEntry,
    framework: str,
    *,
    include_links: bool = True,
) -> dict[str, Any]:
    out = {
        "id": str(row.id),
        "source_type": row.source_type,
        "source": _app_config_source(row),
        "document_id": str(row.document_id) if row.document_id else None,
        "user_id": str(row.user_id) if row.user_id else None,
        "asset_id": str(row.asset_id) if row.asset_id else None,
        "org_node_id": str(row.org_node_id) if row.org_node_id else None,
        "business_process": _business_process_out(row.business_process),
        "business_process_id": str(row.business_process_id),
        "value": row.value,
        "notes": row.notes or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "display": f"{_app_config_source(row).get('label')} → {row.business_process.name if row.business_process else 'Business process'}",
    }
    return out


def _aws_account_out(
    row: IsmsAwsAccount | None, *, include_entry_count: bool = False
) -> dict[str, Any] | None:
    if row is None:
        return None
    out = {
        "id": str(row.id),
        "name": row.name,
        "account_id": row.account_id or "",
        "notes": row.notes or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "display": f"{row.name}{f' ({row.account_id})' if row.account_id else ''}",
    }
    if include_entry_count:
        out["entry_count"] = len(row.access_matrix_links or [])
    return out


def _aws_accounts_out(db: Session) -> list[dict[str, Any]]:
    rows = (
        db.query(IsmsAwsAccount).order_by(func.lower(IsmsAwsAccount.name).asc()).all()
    )
    return [_aws_account_out(row, include_entry_count=True) for row in rows]


def _access_control_matrix_out(
    db: Session,
    row: IsmsAccessControlMatrixEntry,
    framework: str,
    *,
    include_links: bool = True,
) -> dict[str, Any]:
    accounts = [
        _aws_account_out(link.aws_account)
        for link in sorted(
            list(row.aws_account_links or []),
            key=lambda link: (
                (link.aws_account.name or "").lower() if link.aws_account else "",
                link.aws_account.account_id or "" if link.aws_account else "",
            ),
        )
        if link.aws_account
    ]
    service = (
        _asset_out(db, row.service_asset, framework, include_links=False)
        if row.service_asset
        else None
    )
    roles = [
        _org_node_out(link.org_node, include_people=False)
        for link in sorted(
            list(row.role_links or []),
            key=lambda link: (
                (link.org_node.name or "").lower() if link.org_node else "",
                str(link.org_node_id),
            ),
        )
        if link.org_node
    ]
    primary_role = roles[0] if roles else None
    out = {
        "id": str(row.id),
        "task_action": row.task_action or "",
        "service_asset_id": str(row.service_asset_id) if row.service_asset_id else None,
        "service": service,
        "aws_accounts": accounts,
        "aws_account_ids": [acct["id"] for acct in accounts if acct],
        "status": row.status,
        "approved_by": _user_summary(row.approved_by),
        "approved_by_user_id": (
            str(row.approved_by_user_id) if row.approved_by_user_id else None
        ),
        "roles": roles,
        "role_org_node_ids": [role["id"] for role in roles if role],
        # Compatibility for older UI/API callers that expected a single role.
        "role": primary_role,
        "role_org_node_id": primary_role.get("id") if primary_role else None,
        "notes": row.notes or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "display": f"{row.task_action or 'Access control'} → {service.get('asset') if service else 'Service'}",
    }
    return out


def _target_display(row: IsmsEffectivenessMeasure | None) -> str:
    if row is None or row.target_value is None:
        return ""
    op = {
        "lt": "<",
        "lte": "≤",
        "eq": "=",
        "gte": "≥",
        "gt": ">",
    }.get(row.threshold_operator or "", "")
    value = f"{row.target_value:g}"
    unit = (row.target_unit or "").strip()
    return " ".join(x for x in [op, value, unit] if x).strip()


def _metric_entry_value_display(row: IsmsEffectivenessMetricEntry | None) -> str:
    if row is None:
        return ""
    parts: list[str] = []
    if row.metric_value is not None:
        parts.append(f"{row.metric_value:g}")
        if row.metric_unit:
            parts.append(row.metric_unit)
    if row.qualitative_value:
        parts.append(row.qualitative_value)
    return " ".join(parts).strip()


def _metric_entry_out(
    row: IsmsEffectivenessMetricEntry | None,
) -> dict[str, Any] | None:
    if row is None:
        return None
    period = ""
    if row.period_start and row.period_end:
        period = f"{row.period_start.isoformat()} → {row.period_end.isoformat()}"
    elif row.period_start:
        period = f"from {row.period_start.isoformat()}"
    elif row.period_end:
        period = f"to {row.period_end.isoformat()}"
    out = {
        "id": str(row.id),
        "measure_id": str(row.measure_id),
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        "period_start": row.period_start.isoformat() if row.period_start else None,
        "period_end": row.period_end.isoformat() if row.period_end else None,
        "period": period,
        "metric_value": row.metric_value,
        "metric_unit": row.metric_unit or "",
        "qualitative_value": row.qualitative_value or "",
        "value_display": _metric_entry_value_display(row),
        "source_type": row.source_type or EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE,
        "source_title": row.source_title or "",
        "source_url": row.source_url,
        "source_reference": row.source_reference or "",
        "source_event_id": str(row.source_event_id) if row.source_event_id else None,
        "source_event_url": (
            f"/event.html?id={row.source_event_id}" if row.source_event_id else None
        ),
        "notes": row.notes or "",
        "raw_payload": row.raw_payload or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "display": _metric_entry_value_display(row)
        or row.source_title
        or "Metric entry",
    }
    return out


def _effectiveness_measure_out(
    db: Session,
    row: IsmsEffectivenessMeasure,
    framework: str,
    *,
    include_links: bool = True,
    include_entries: bool = True,
) -> dict[str, Any]:
    entries = sorted(
        list(row.metric_entries or []),
        key=lambda entry: (entry.recorded_at or entry.created_at, entry.created_at),
        reverse=True,
    )
    latest = entries[0] if entries else None
    out = {
        "id": str(row.id),
        "framework": row.framework_slug,
        "summary": row.summary or "",
        "description": row.description or "",
        "effectiveness_measure": row.effectiveness_measure or "",
        "metric": row.metric or "",
        "metric_key": row.metric_key,
        "target_value": row.target_value,
        "target_unit": row.target_unit or "",
        "threshold_operator": row.threshold_operator or "",
        "target_display": _target_display(row),
        "owner": _user_summary(row.owner),
        "owner_user_id": str(row.owner_user_id) if row.owner_user_id else None,
        "frequency": row.frequency or "",
        "notes": row.notes or "",
        "metric_entry_count": len(entries),
        "latest_entry": _metric_entry_out(latest),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "display": row.summary
        or row.metric
        or row.effectiveness_measure
        or "Effectiveness measure",
    }
    if include_entries:
        out["metric_entries"] = [
            _metric_entry_out(entry) for entry in entries[:25] if entry is not None
        ]
    if include_links:
        out["controls"] = _linked_controls(
            db, "effectiveness_measure", row.id, framework
        )
        out["control_count"] = len(out["controls"])
        # Kept as an empty list for backward-compatible API shape, but the UI no
        # longer exposes direct clause links for effectiveness measures.
        out["clauses"] = []
        out["clause_count"] = 0
    return out


def _meeting_out(
    db: Session, row: IsmsMeeting, framework: str, *, include_links: bool = True
) -> dict[str, Any]:
    attendees = []
    apologies = []
    for link in list(row.attendees or []):
        target = attendees if link.attendance_type == "attendee" else apologies
        target.append(_user_summary(link.user))
    attendee_person_ids = []
    apology_person_ids = []
    for link in (
        db.query(IsmsMeetingPerson).filter(IsmsMeetingPerson.meeting_id == row.id).all()
    ):
        item = {
            "id": str(link.person_id) if link.person_id else None,
            "person_id": str(link.person_id) if link.person_id else None,
            "name": link.person.name if link.person else link.name,
            "email": link.person.email if link.person else link.email,
        }
        (attendees if link.attendance_type == "attendee" else apologies).append(item)
        if link.person_id:
            (
                attendee_person_ids
                if link.attendance_type == "attendee"
                else apology_person_ids
            ).append(str(link.person_id))
    support_links = []
    for link in list(row.links or []):
        support_links.append(
            {
                "id": str(link.id),
                "link_type": link.link_type,
                "document_id": str(link.document_id) if link.document_id else None,
                "document": (
                    _document_out(db, link.document, framework, include_links=False)
                    if link.document
                    else None
                ),
                "title": link.title or (link.document.title if link.document else ""),
                "url": link.url,
            }
        )
    out = {
        "id": str(row.id),
        "title": row.title,
        "date": row.date.isoformat() if row.date else None,
        "start_time": row.start_time.isoformat() if row.start_time else None,
        "end_time": row.end_time.isoformat() if row.end_time else None,
        "attendees": attendees,
        "apologies": apologies,
        "attendee_user_ids": [
            str(x.user_id) for x in row.attendees if x.attendance_type == "attendee"
        ],
        "apology_user_ids": [
            str(x.user_id) for x in row.attendees if x.attendance_type == "apology"
        ],
        "attendee_person_ids": attendee_person_ids,
        "apology_person_ids": apology_person_ids,
        "links": support_links,
        "agenda_minutes_notes": row.agenda_minutes_notes or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "display": row.title,
    }
    return _attach_links(db, out, "meeting", row.id, framework, include_links)


def _entity_out(
    db: Session,
    entity_type: str,
    row: Any,
    framework: str,
    *,
    include_links: bool = True,
) -> dict[str, Any]:
    if entity_type == "objective":
        return _objective_out(db, row, framework, include_links=include_links)
    if entity_type == "document":
        return _document_out(db, row, framework, include_links=include_links)
    if entity_type == "org_node":
        out = _org_node_out(row) or {}
        out["display"] = row.name
        return _attach_links(db, out, "org_node", row.id, framework, include_links)
    if entity_type == "asset":
        return _asset_out(db, row, framework, include_links=include_links)
    if entity_type == "application_configuration":
        return _app_config_out(db, row, framework, include_links=include_links)
    if entity_type == "access_control_matrix":
        return _access_control_matrix_out(
            db, row, framework, include_links=include_links
        )
    if entity_type == "effectiveness_measure":
        return _effectiveness_measure_out(
            db, row, framework, include_links=include_links
        )
    if entity_type == "effectiveness_metric":
        out = _metric_entry_out(row) or {}
        measure = getattr(row, "measure", None)
        out["measure"] = (
            _effectiveness_measure_out(
                db, measure, framework, include_links=False, include_entries=False
            )
            if measure
            else None
        )
        return out
    if entity_type == "meeting":
        return _meeting_out(db, row, framework, include_links=include_links)
    raise HTTPException(status_code=400, detail="Unknown ISMS entity type")
