from __future__ import annotations

import os
import re
import uuid
from datetime import date as date_type, datetime, time as time_type
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.utils import ref_sort_key as _ref_sort_key, try_uuid as _try_uuid
from app.core.config import settings
from app.core.source_meta import apply_user_source_overrides, get_source_meta
from app.db.models import (
    ControlItem,
    Event,
    FrameworkClause,
    IsmsAccessControlMatrixAwsAccount,
    IsmsAccessControlMatrixEntry,
    IsmsAccessControlMatrixRole,
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
    IsmsMeetingAttendee,
    IsmsMeetingLink,
    IsmsObjective,
    IsmsObjectiveResourceUser,
    IsmsOrgNode,
    IsmsOrgNodeUser,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    User,
)
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.security.permissions import has_permission
from app.security.rich_text import sanitize_rich_text_html
from app.services.entity_changelog import (
    list_entity_changelogs,
    record_entity_changelog,
)
from app.storage.s3 import get_object_stream, iter_stream, parse_s3_uri, put_bytes

router = APIRouter()

ISMS_READ_PERMISSION = "isms.read"
ISMS_MANAGE_PERMISSION = "isms.manage"
RISK_READ_PERMISSION = "risk.read"
RISK_MANAGE_PERMISSION = "risk.manage"
DOCUMENT_TYPES = {
    "policy",
    "process",
    "procedure",
    "standard",
    "guideline",
    "record",
    "other",
}
OBJECTIVE_STATUSES = {
    "not_started",
    "in_progress",
    "completed",
    "deferred",
    "superseded",
}
APP_SOURCE_TYPES = {"document", "person", "asset", "org_node"}
APP_VALUES = {"Low", "Medium", "High"}
ACCESS_STATUSES = {"Pending Approval", "Approved"}
MEETING_LINK_TYPES = {"isms_document", "external_url"}
EFFECTIVENESS_THRESHOLD_OPERATORS = {"", "lt", "lte", "eq", "gte", "gt"}
EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE = "other"
ISMS_SECTION_ALIASES = {
    "overview": "overview",
    "summary": "overview",
    "objective": "objectives",
    "objectives": "objectives",
    "document": "documents",
    "documents": "documents",
    "policies": "documents",
    "policy": "documents",
    "processes": "documents",
    "org": "org",
    "org_node": "org",
    "org_nodes": "org",
    "org-nodes": "org",
    "organisation": "org",
    "organization": "org",
    "assets": "assets",
    "asset": "assets",
    "access": "access",
    "accesscontrol": "access",
    "access_control": "access",
    "access-control": "access",
    "access_control_matrix": "access",
    "access-control-matrix": "access",
    "effectiveness": "effectiveness",
    "effectiveness_measure": "effectiveness",
    "effectiveness-measure": "effectiveness",
    "effectiveness_measures": "effectiveness",
    "effectiveness-measures": "effectiveness",
    "metrics": "effectiveness",
    "app": "app",
    "appconfig": "app",
    "app_config": "app",
    "application_configuration": "app",
    "application-configuration": "app",
    "meetings": "meetings",
    "meeting": "meetings",
    "soa": "soa",
}


def _clean_isms_section(section: str | None) -> str | None:
    if section is None:
        return None
    raw = str(section or "").strip()
    if not raw:
        return None
    key = raw.lower().replace(" ", "_")
    normalized = key.replace("-", "_")
    value = ISMS_SECTION_ALIASES.get(key) or ISMS_SECTION_ALIASES.get(normalized)
    if not value:
        raise HTTPException(status_code=400, detail="Unknown ISMS section")
    return value


ENTITY_TYPES = {
    "objective": (IsmsObjective, "isms_objective"),
    "document": (IsmsDocument, "isms_document"),
    "org_node": (IsmsOrgNode, "isms_org_node"),
    "asset": (RiskAsset, "isms_asset"),
    "license": (IsmsLicense, "isms_license"),
    "application_configuration": (
        IsmsApplicationConfigurationEntry,
        "isms_application_configuration",
    ),
    "access_control_matrix": (
        IsmsAccessControlMatrixEntry,
        "isms_access_control_matrix",
    ),
    "meeting": (IsmsMeeting, "isms_meeting"),
    "effectiveness_measure": (
        IsmsEffectivenessMeasure,
        "isms_effectiveness_measure",
    ),
    "effectiveness_metric": (
        IsmsEffectivenessMetricEntry,
        "isms_effectiveness_metric",
    ),
}


class IdListPayload(BaseModel):
    ids: list[uuid.UUID] = Field(default_factory=list)


class IsmsLinksPayload(BaseModel):
    controls: list[str] | None = None
    clauses: list[str] | None = None


class ObjectivePayload(IsmsLinksPayload):
    requirement: str | None = Field(default=None, max_length=20000)
    goal: str | None = Field(default=None, max_length=20000)
    metric: str | None = Field(default=None, max_length=20000)
    completion_method: str | None = Field(default=None, max_length=12000)
    resource_requirements_text: str | None = Field(default=None, max_length=12000)
    resource_user_ids: list[uuid.UUID] | None = None
    owner_user_id: uuid.UUID | None = None
    completion_target_date: str | None = Field(default=None, max_length=64)
    evaluation_method: str | None = Field(default=None, max_length=12000)
    status: str | None = Field(default=None, max_length=32)


class DocumentPayload(IsmsLinksPayload):
    title: str | None = Field(default=None, max_length=256)
    document_type: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=20000)
    external_url: str | None = Field(default=None, max_length=2048)


class OrgNodePayload(IsmsLinksPayload):
    parent_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=256)
    node_type: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=20000)
    sort_order: int | None = None
    user_ids: list[uuid.UUID] | None = None
    relationship_type: str | None = Field(default="member", max_length=32)


class AssetPayload(IsmsLinksPayload):
    # ``asset`` is retained as the ISMS-facing field name. ``name`` and
    # ``asset_name`` keep compatibility with the CIA Triad risk asset API.
    asset: str | None = Field(default=None, max_length=256)
    name: str | None = Field(default=None, max_length=256)
    asset_name: str | None = Field(default=None, max_length=256)
    category_id: uuid.UUID | None = None
    category_name: str | None = Field(default=None, max_length=128)
    subcategory_id: uuid.UUID | None = None
    subcategory_name: str | None = Field(default=None, max_length=128)
    license_id: uuid.UUID | None = None
    license_name: str | None = Field(default=None, max_length=256)
    # Compatibility for older clients. New UI/API clients should send license_id.
    license: str | None = Field(default=None, max_length=256)
    owner_org_node_id: uuid.UUID | None = None
    register_held_by_org_node_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=20000)


class AssetCategoryPayload(BaseModel):
    name: str | None = Field(default=None, max_length=128)


class AssetSubcategoryPayload(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    category_id: uuid.UUID | None = None
    category_name: str | None = Field(default=None, max_length=128)


class LicensePayload(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=12000)


class BusinessProcessPayload(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=20000)
    sort_order: int | None = None


class AppConfigPayload(BaseModel):
    source_type: str | None = Field(default=None, max_length=32)
    document_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    org_node_id: uuid.UUID | None = None
    business_process_id: uuid.UUID | None = None
    value: str | None = Field(default=None, max_length=16)
    notes: str | None = Field(default=None, max_length=12000)


class AwsAccountPayload(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    account_id: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=12000)


class AccessControlMatrixPayload(BaseModel):
    task_action: str | None = Field(default=None, max_length=20000)
    service_asset_id: uuid.UUID | None = None
    aws_account_ids: list[uuid.UUID] | None = None
    status: str | None = Field(default=None, max_length=32)
    approved_by_user_id: uuid.UUID | None = None
    # New clients send role_org_node_ids because one access row can apply to several
    # organisation-chart roles. role_org_node_id is accepted for older clients.
    role_org_node_ids: list[uuid.UUID] | None = None
    role_org_node_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=12000)


class EffectivenessMeasurePayload(IsmsLinksPayload):
    summary: str | None = Field(default=None, max_length=20000)
    description: str | None = Field(default=None, max_length=40000)
    effectiveness_measure: str | None = Field(default=None, max_length=20000)
    metric: str | None = Field(default=None, max_length=20000)
    metric_key: str | None = Field(default=None, max_length=128)
    target_value: float | None = None
    target_unit: str | None = Field(default=None, max_length=64)
    threshold_operator: str | None = Field(default=None, max_length=16)
    owner_user_id: uuid.UUID | None = None
    frequency: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=12000)


class EffectivenessMetricEntryPayload(BaseModel):
    recorded_at: datetime | None = None
    period_start: date_type | None = None
    period_end: date_type | None = None
    metric_value: float | None = None
    metric_unit: str | None = Field(default=None, max_length=64)
    qualitative_value: str | None = Field(default=None, max_length=20000)
    source_type: str | None = Field(default=None, max_length=64)
    source_title: str | None = Field(default=None, max_length=256)
    source_url: str | None = Field(default=None, max_length=2048)
    source_reference: str | None = Field(default=None, max_length=256)
    source_event_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=12000)
    raw_payload: dict[str, Any] | None = None


class MeetingLinkPayload(BaseModel):
    link_type: str = Field(default="external_url", max_length=32)
    document_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=256)
    url: str | None = Field(default=None, max_length=2048)


class MeetingPayload(IsmsLinksPayload):
    title: str | None = Field(default=None, max_length=256)
    date: date_type | None = None
    start_time: time_type | None = None
    end_time: time_type | None = None
    attendee_user_ids: list[uuid.UUID] | None = None
    apology_user_ids: list[uuid.UUID] | None = None
    links: list[MeetingLinkPayload] | None = None
    agenda_minutes_notes: str | None = Field(default=None, max_length=50000)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _can_read_isms(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return bool(
        has_permission(db, user, ISMS_READ_PERMISSION)
        or has_permission(db, user, ISMS_MANAGE_PERMISSION)
        or has_permission(db, user, RISK_READ_PERMISSION)
        or has_permission(db, user, RISK_MANAGE_PERMISSION)
    )


def _can_manage_isms(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return bool(
        has_permission(db, user, ISMS_MANAGE_PERMISSION)
        or has_permission(db, user, RISK_MANAGE_PERMISSION)
    )


def require_isms_read(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not _can_read_isms(db, user):
        raise HTTPException(status_code=403, detail="isms.read permission required")
    return user


def require_isms_manage(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not _can_manage_isms(db, user):
        raise HTTPException(status_code=403, detail="isms.manage permission required")
    return user


def _clean_text(
    raw: str | None, *, max_len: int, required: bool = False, label: str = "value"
) -> str:
    value = (raw or "").strip()
    if required and not value:
        raise HTTPException(status_code=400, detail=f"{label} is required")
    if len(value) > max_len:
        raise HTTPException(status_code=400, detail=f"{label} is too long")
    return value


def _clean_minutes_html(raw: str | None) -> str:
    # Trim/length-check the raw input, then apply the shared server-side
    # rich-text allowlist sanitiser (single source of truth for all KEEN
    # rich-text fields; see app.security.rich_text).
    value = _clean_text(raw, max_len=50000)
    cleaned = sanitize_rich_text_html(value)
    if len(cleaned) > 50000:
        raise HTTPException(status_code=400, detail="agenda/minutes/notes is too long")
    return cleaned


def _clean_framework(raw: str | None) -> str:
    value = (raw or settings.default_framework_slug or "").strip()
    if not value or len(value) > 64 or not re.match(r"^[A-Za-z0-9._:-]+$", value):
        raise HTTPException(status_code=400, detail="Invalid framework")
    return value


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


def _licenses_out(db: Session) -> list[dict[str, Any]]:
    rows = db.query(IsmsLicense).order_by(func.lower(IsmsLicense.name).asc()).all()
    return [_license_out(row, include_asset_count=True) for row in rows]


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


def _metric_key_from_payload(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if len(value) > 128 or not re.match(r"^[A-Za-z0-9._:-]+$", value):
        raise HTTPException(
            status_code=400,
            detail="metric_key may only contain letters, numbers, dot, underscore, colon and hyphen",
        )
    return value


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


def _validate_effectiveness_measure_payload(
    db: Session,
    payload: EffectivenessMeasurePayload,
    *,
    fields: set[str] | None = None,
    row_id: uuid.UUID | None = None,
) -> None:
    if (fields is None or "owner_user_id" in fields) and payload.owner_user_id:
        if (
            not db.query(User.id)
            .filter(User.id == payload.owner_user_id, User.is_active.is_(True))
            .first()
        ):
            raise HTTPException(status_code=400, detail="Unknown owner")
    if fields is None or "threshold_operator" in fields:
        op = (payload.threshold_operator or "").strip()
        if op not in EFFECTIVENESS_THRESHOLD_OPERATORS:
            raise HTTPException(status_code=400, detail="Invalid threshold operator")
    if fields is None or "metric_key" in fields:
        key = _metric_key_from_payload(payload.metric_key)
        if key:
            qry = db.query(IsmsEffectivenessMeasure.id).filter(
                func.lower(IsmsEffectivenessMeasure.metric_key) == key.lower()
            )
            if row_id:
                qry = qry.filter(IsmsEffectivenessMeasure.id != row_id)
            if qry.first():
                raise HTTPException(status_code=400, detail="metric_key already exists")
    if (fields is None or "clauses" in fields) and payload.clauses:
        raise HTTPException(
            status_code=400,
            detail="Effectiveness measures link controls only; clauses are inferred from those controls",
        )


def _apply_effectiveness_measure_payload(
    row: IsmsEffectivenessMeasure,
    payload: EffectivenessMeasurePayload,
    *,
    partial: bool = False,
) -> None:
    fields = (
        set(payload.model_fields_set or set())
        if partial
        else {
            "summary",
            "description",
            "effectiveness_measure",
            "metric",
            "metric_key",
            "target_value",
            "target_unit",
            "threshold_operator",
            "owner_user_id",
            "frequency",
            "notes",
        }
    )
    if "summary" in fields:
        row.summary = _clean_text(payload.summary, max_len=20000)
    if "description" in fields:
        row.description = _clean_text(payload.description, max_len=40000)
    if "effectiveness_measure" in fields:
        row.effectiveness_measure = _clean_text(
            payload.effectiveness_measure,
            max_len=20000,
            required=not partial,
            label="effectiveness measure",
        )
    if "metric" in fields:
        row.metric = _clean_text(
            payload.metric, max_len=20000, required=not partial, label="metric"
        )
    if "metric_key" in fields:
        row.metric_key = _metric_key_from_payload(payload.metric_key)
    if "target_value" in fields:
        row.target_value = payload.target_value
    if "target_unit" in fields:
        row.target_unit = _clean_text(payload.target_unit, max_len=64)
    if "threshold_operator" in fields:
        row.threshold_operator = (payload.threshold_operator or "").strip()
    if "owner_user_id" in fields:
        row.owner_user_id = payload.owner_user_id
    if "frequency" in fields:
        row.frequency = _clean_text(payload.frequency, max_len=64)
    if "notes" in fields:
        row.notes = _clean_text(payload.notes, max_len=12000)
    row.updated_at = _utcnow()


def _actual_effectiveness_metric_source_names(db: Session) -> list[str]:
    rows = (
        db.query(Event.source)
        .filter(Event.source.isnot(None), func.length(func.trim(Event.source)) > 0)
        .group_by(Event.source)
        .order_by(func.lower(Event.source).asc())
        .all()
    )
    return [str(src).strip() for (src,) in rows if str(src or "").strip()]


def _effectiveness_metric_sources_out(
    db: Session, user: User | None = None
) -> list[dict[str, str]]:
    overrides = (
        getattr(user, "pref_source_colors", None) if isinstance(user, User) else None
    )
    items: list[dict[str, str]] = []
    for source in _actual_effectiveness_metric_source_names(db):
        meta = apply_user_source_overrides(get_source_meta(source), overrides)
        items.append(
            {
                "id": source,
                "source": source,
                "label": meta.label,
                "color": meta.color,
            }
        )
    items.append(
        {
            "id": EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE,
            "source": EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE,
            "label": "Other",
            "color": "",
        }
    )
    return items


def _coerce_metric_source_type(db: Session, raw: str | None) -> str:
    source_type = _clean_text(raw, max_len=64).strip()
    if not source_type:
        return EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE
    if source_type.lower() == EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE:
        return EFFECTIVENESS_METRIC_OTHER_SOURCE_TYPE
    sources = _actual_effectiveness_metric_source_names(db)
    for source in sources:
        if source_type == source:
            return source
    source_key = source_type.lower().replace("-", "_")
    for source in sources:
        if source.lower().replace("-", "_") == source_key:
            return source
    raise HTTPException(
        status_code=400,
        detail="Invalid metric source type; choose an existing KEEN source or other",
    )


def _validate_metric_entry_payload(
    db: Session, payload: EffectivenessMetricEntryPayload, *, partial: bool = False
) -> None:
    fields = set(payload.model_fields_set or set()) if partial else set()
    qualitative = _clean_text(payload.qualitative_value, max_len=20000)
    if not partial or "metric_value" in fields or "qualitative_value" in fields:
        if payload.metric_value is None and not qualitative:
            raise HTTPException(
                status_code=400, detail="metric_value or qualitative_value is required"
            )
    if (
        payload.period_start
        and payload.period_end
        and payload.period_end < payload.period_start
    ):
        raise HTTPException(
            status_code=400, detail="period_end must be after period_start"
        )
    if not partial or "source_type" in fields:
        _coerce_metric_source_type(db, payload.source_type)
    if payload.source_event_id:
        if not db.query(Event.id).filter(Event.id == payload.source_event_id).first():
            raise HTTPException(status_code=400, detail="Unknown source event")


def _apply_metric_entry_payload(
    row: IsmsEffectivenessMetricEntry,
    payload: EffectivenessMetricEntryPayload,
    db: Session,
    *,
    partial: bool = False,
) -> None:
    fields = (
        set(payload.model_fields_set or set())
        if partial
        else {
            "recorded_at",
            "period_start",
            "period_end",
            "metric_value",
            "metric_unit",
            "qualitative_value",
            "source_type",
            "source_title",
            "source_url",
            "source_reference",
            "source_event_id",
            "notes",
            "raw_payload",
        }
    )
    if "recorded_at" in fields:
        row.recorded_at = payload.recorded_at or _utcnow()
    if "period_start" in fields:
        row.period_start = payload.period_start
    if "period_end" in fields:
        row.period_end = payload.period_end
    if "metric_value" in fields:
        row.metric_value = payload.metric_value
    if "metric_unit" in fields:
        row.metric_unit = _clean_text(payload.metric_unit, max_len=64)
    if "qualitative_value" in fields:
        row.qualitative_value = _clean_text(payload.qualitative_value, max_len=20000)
    if "source_type" in fields:
        row.source_type = _coerce_metric_source_type(db, payload.source_type)
    if "source_title" in fields:
        row.source_title = _clean_text(payload.source_title, max_len=256)
    if "source_url" in fields:
        row.source_url = _clean_text(payload.source_url, max_len=2048) or None
    if "source_reference" in fields:
        row.source_reference = _clean_text(payload.source_reference, max_len=256)
    if "source_event_id" in fields:
        row.source_event_id = payload.source_event_id
    if "notes" in fields:
        row.notes = _clean_text(payload.notes, max_len=12000)
    if "raw_payload" in fields:
        row.raw_payload = payload.raw_payload or {}
    row.updated_at = _utcnow()


def _meeting_out(
    db: Session, row: IsmsMeeting, framework: str, *, include_links: bool = True
) -> dict[str, Any]:
    attendees = []
    apologies = []
    for link in list(row.attendees or []):
        target = attendees if link.attendance_type == "attendee" else apologies
        target.append(_user_summary(link.user))
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
        "attendee_user_ids": [x["id"] for x in attendees if x],
        "apology_user_ids": [x["id"] for x in apologies if x],
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


def _by_id_or_404(db: Session, model, raw_id: str, label: str):
    rid = _try_uuid(raw_id)
    if not rid:
        raise HTTPException(status_code=400, detail=f"{label} id must be a UUID")
    row = db.query(model).filter(model.id == rid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return row


def _control_by_ref_or_id(db: Session, raw: str, framework: str) -> ControlItem:
    value = (raw or "").strip()
    cid = _try_uuid(value)
    qry = db.query(ControlItem).filter(ControlItem.framework_slug == framework)
    row = (
        qry.filter(ControlItem.id == cid).one_or_none()
        if cid
        else qry.filter(func.lower(ControlItem.ref) == value.lower()).one_or_none()
    )
    if not row:
        raise HTTPException(status_code=400, detail=f"Unknown control: {value}")
    return row


def _clause_by_ref_or_id(db: Session, raw: str, framework: str) -> FrameworkClause:
    value = (raw or "").strip()
    cid = _try_uuid(value)
    qry = db.query(FrameworkClause).filter(FrameworkClause.framework_slug == framework)
    row = (
        qry.filter(FrameworkClause.id == cid).one_or_none()
        if cid
        else qry.filter(func.lower(FrameworkClause.ref) == value.lower()).one_or_none()
    )
    if not row:
        raise HTTPException(status_code=400, detail=f"Unknown clause: {value}")
    return row


def _asset_name_from_payload(payload: AssetPayload) -> str:
    return _clean_text(
        payload.asset or payload.asset_name or payload.name,
        max_len=256,
        required=True,
        label="asset",
    )


def _asset_category_by_id(db: Session, category_id: uuid.UUID) -> RiskCategory:
    row = db.query(RiskCategory).filter(RiskCategory.id == category_id).one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail="Unknown asset category")
    return row


def _asset_category_or_create(
    db: Session, *, category_id: uuid.UUID | None, category_name: str | None
) -> RiskCategory:
    if category_id:
        return _asset_category_by_id(db, category_id)
    name = _clean_text(category_name, max_len=128, required=True, label="category")
    row = (
        db.query(RiskCategory)
        .filter(func.lower(RiskCategory.name) == name.lower())
        .one_or_none()
    )
    if row:
        return row
    row = RiskCategory(name=name, created_at=_utcnow(), updated_at=_utcnow())
    db.add(row)
    db.flush()
    return row


def _asset_subcategory_by_id(
    db: Session, subcategory_id: uuid.UUID, *, category: RiskCategory | None = None
) -> RiskAssetSubcategory:
    qry = db.query(RiskAssetSubcategory).filter(
        RiskAssetSubcategory.id == subcategory_id
    )
    if category is not None:
        qry = qry.filter(RiskAssetSubcategory.category_id == category.id)
    row = qry.one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail="Unknown asset subcategory")
    return row


def _asset_subcategory_or_create(
    db: Session,
    *,
    category: RiskCategory,
    subcategory_id: uuid.UUID | None,
    subcategory_name: str | None,
) -> RiskAssetSubcategory:
    if subcategory_id:
        return _asset_subcategory_by_id(db, subcategory_id, category=category)
    name = _clean_text(
        subcategory_name, max_len=128, required=True, label="subcategory"
    )
    row = (
        db.query(RiskAssetSubcategory)
        .filter(
            RiskAssetSubcategory.category_id == category.id,
            func.lower(RiskAssetSubcategory.name) == name.lower(),
        )
        .one_or_none()
    )
    if row:
        return row
    row = RiskAssetSubcategory(
        category_id=category.id,
        name=name,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _license_or_404(db: Session, license_id: str | uuid.UUID) -> IsmsLicense:
    lid = (
        license_id if isinstance(license_id, uuid.UUID) else _try_uuid(str(license_id))
    )
    if not lid:
        raise HTTPException(status_code=400, detail="license_id must be a UUID")
    row = db.query(IsmsLicense).filter(IsmsLicense.id == lid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="License not found")
    return row


def _license_by_name(db: Session, name: str) -> IsmsLicense | None:
    cleaned = _clean_text(name, max_len=256)
    if not cleaned:
        return None
    return (
        db.query(IsmsLicense)
        .filter(func.lower(IsmsLicense.name) == cleaned.lower())
        .one_or_none()
    )


def _license_or_create_by_name(
    db: Session, name: str | None, user: User | None = None
) -> IsmsLicense | None:
    cleaned = _clean_text(name, max_len=256)
    if not cleaned:
        return None
    row = _license_by_name(db, cleaned)
    if row:
        return row
    row = IsmsLicense(
        name=cleaned,
        description="",
        created_by_user_id=getattr(user, "id", None),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _license_from_payload(
    db: Session, payload: AssetPayload, user: User | None = None
) -> IsmsLicense | None:
    if payload.license_id:
        return _license_or_404(db, payload.license_id)
    # ``license_name`` is useful for imports/API callers; the UI sends license_id.
    if payload.license_name:
        return _license_or_create_by_name(db, payload.license_name, user)
    # Backwards compatibility for older callers still sending the old free-text field.
    if payload.license:
        return _license_or_create_by_name(db, payload.license, user)
    return None


def _apply_license_payload(row: IsmsLicense, payload: LicensePayload) -> None:
    row.name = _clean_text(payload.name, max_len=256, required=True, label="license")
    row.description = _clean_text(payload.description, max_len=12000)
    row.updated_at = _utcnow()


def _asset_or_404(db: Session, asset_id: str | uuid.UUID) -> RiskAsset:
    aid = asset_id if isinstance(asset_id, uuid.UUID) else _try_uuid(str(asset_id))
    if not aid:
        raise HTTPException(status_code=400, detail="asset_id must be a UUID")
    row = db.query(RiskAsset).filter(RiskAsset.id == aid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Asset not found")
    return row


def _apply_asset_payload(
    db: Session, row: RiskAsset, payload: AssetPayload, user: User | None = None
) -> None:
    row.name = _asset_name_from_payload(payload)
    license_row = _license_from_payload(db, payload, user)
    row.license_id = license_row.id if license_row else None
    row.license = license_row.name if license_row else ""
    row.owner_org_node_id = payload.owner_org_node_id
    row.register_held_by_org_node_id = payload.register_held_by_org_node_id
    row.description = _clean_text(payload.description, max_len=20000)
    row.updated_at = _utcnow()


def _replace_control_links(
    db: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    framework: str,
    values: list[str] | None,
    user: User,
) -> None:
    if values is None:
        return
    db.query(IsmsEntityControlLink).filter(
        IsmsEntityControlLink.entity_type == entity_type,
        IsmsEntityControlLink.entity_id == entity_id,
        IsmsEntityControlLink.framework_slug == framework,
    ).delete(synchronize_session=False)
    seen = set()
    now = _utcnow()
    for raw in values or []:
        control = _control_by_ref_or_id(db, str(raw), framework)
        if control.id in seen:
            continue
        seen.add(control.id)
        db.add(
            IsmsEntityControlLink(
                entity_type=entity_type,
                entity_id=entity_id,
                framework_slug=framework,
                control_item_id=control.id,
                created_by_user_id=user.id,
                created_at=now,
            )
        )
    db.flush()


def _replace_clause_links(
    db: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    framework: str,
    values: list[str] | None,
    user: User,
) -> None:
    if values is None:
        return
    db.query(IsmsEntityClauseLink).filter(
        IsmsEntityClauseLink.entity_type == entity_type,
        IsmsEntityClauseLink.entity_id == entity_id,
        IsmsEntityClauseLink.framework_slug == framework,
    ).delete(synchronize_session=False)
    seen = set()
    now = _utcnow()
    for raw in values or []:
        clause = _clause_by_ref_or_id(db, str(raw), framework)
        if clause.id in seen:
            continue
        seen.add(clause.id)
        db.add(
            IsmsEntityClauseLink(
                entity_type=entity_type,
                entity_id=entity_id,
                framework_slug=framework,
                clause_id=clause.id,
                created_by_user_id=user.id,
                created_at=now,
            )
        )
    db.flush()


def _apply_links(
    db: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    framework: str,
    payload: IsmsLinksPayload,
    user: User,
) -> None:
    _replace_control_links(
        db, entity_type, entity_id, framework, payload.controls, user
    )
    _replace_clause_links(db, entity_type, entity_id, framework, payload.clauses, user)


def _apply_effectiveness_links(
    db: Session,
    entity_id: uuid.UUID,
    framework: str,
    payload: IsmsLinksPayload,
    user: User,
) -> None:
    # Effectiveness Measures are control-scoped ISMS records. They deliberately
    # do not link directly to clauses, because the applicable clauses should be
    # inferred through the linked controls. Remove any legacy clause links if an
    # existing row is edited after older builds allowed them.
    _replace_control_links(
        db, "effectiveness_measure", entity_id, framework, payload.controls, user
    )
    db.query(IsmsEntityClauseLink).filter(
        IsmsEntityClauseLink.entity_type == "effectiveness_measure",
        IsmsEntityClauseLink.entity_id == entity_id,
        IsmsEntityClauseLink.framework_slug == framework,
    ).delete(synchronize_session=False)
    db.flush()


def _delete_entity_links(
    db: Session, entity_type: str, entity_id: uuid.UUID, framework: str
) -> None:
    db.query(IsmsEntityControlLink).filter(
        IsmsEntityControlLink.entity_type == entity_type,
        IsmsEntityControlLink.entity_id == entity_id,
        IsmsEntityControlLink.framework_slug == framework,
    ).delete(synchronize_session=False)
    db.query(IsmsEntityClauseLink).filter(
        IsmsEntityClauseLink.entity_type == entity_type,
        IsmsEntityClauseLink.entity_id == entity_id,
        IsmsEntityClauseLink.framework_slug == framework,
    ).delete(synchronize_session=False)


def _replace_objective_resources(
    db: Session, objective: IsmsObjective, user_ids: list[uuid.UUID] | None
) -> None:
    if user_ids is None:
        return
    db.query(IsmsObjectiveResourceUser).filter(
        IsmsObjectiveResourceUser.objective_id == objective.id
    ).delete(synchronize_session=False)
    seen = set()
    now = _utcnow()
    for uid in user_ids or []:
        if uid in seen:
            continue
        if (
            not db.query(User.id)
            .filter(User.id == uid, User.is_active.is_(True))
            .first()
        ):
            raise HTTPException(status_code=400, detail=f"Unknown user: {uid}")
        seen.add(uid)
        db.add(
            IsmsObjectiveResourceUser(
                objective_id=objective.id, user_id=uid, created_at=now
            )
        )
    db.flush()
    try:
        db.expire(objective, ["resource_users"])
    except Exception:
        pass


def _replace_access_control_accounts(
    db: Session,
    row: IsmsAccessControlMatrixEntry,
    aws_account_ids: list[uuid.UUID] | None,
) -> None:
    if aws_account_ids is None:
        return
    db.query(IsmsAccessControlMatrixAwsAccount).filter(
        IsmsAccessControlMatrixAwsAccount.entry_id == row.id
    ).delete(synchronize_session=False)
    seen = set()
    now = _utcnow()
    for account_id in aws_account_ids or []:
        if account_id in seen:
            continue
        if (
            not db.query(IsmsAwsAccount.id)
            .filter(IsmsAwsAccount.id == account_id)
            .first()
        ):
            raise HTTPException(
                status_code=400, detail=f"Unknown hosting account: {account_id}"
            )
        seen.add(account_id)
        db.add(
            IsmsAccessControlMatrixAwsAccount(
                entry_id=row.id,
                aws_account_id=account_id,
                created_at=now,
            )
        )
    db.flush()
    try:
        db.expire(row, ["aws_account_links"])
    except Exception:
        pass


def _replace_access_control_roles(
    db: Session,
    row: IsmsAccessControlMatrixEntry,
    role_org_node_ids: list[uuid.UUID] | None,
) -> None:
    if role_org_node_ids is None:
        return
    db.query(IsmsAccessControlMatrixRole).filter(
        IsmsAccessControlMatrixRole.entry_id == row.id
    ).delete(synchronize_session=False)
    seen = set()
    now = _utcnow()
    for org_node_id in role_org_node_ids or []:
        if org_node_id in seen:
            continue
        if not db.query(IsmsOrgNode.id).filter(IsmsOrgNode.id == org_node_id).first():
            raise HTTPException(
                status_code=400,
                detail=f"Unknown organisation chart role: {org_node_id}",
            )
        seen.add(org_node_id)
        db.add(
            IsmsAccessControlMatrixRole(
                entry_id=row.id,
                org_node_id=org_node_id,
                created_at=now,
            )
        )
    db.flush()
    try:
        db.expire(row, ["role_links"])
    except Exception:
        pass


def _replace_org_node_users(
    db: Session,
    node: IsmsOrgNode,
    user_ids: list[uuid.UUID] | None,
    relationship_type: str | None,
) -> None:
    if user_ids is None:
        return
    rel = _clean_text(
        relationship_type or "member",
        max_len=32,
        required=True,
        label="relationship_type",
    )
    db.query(IsmsOrgNodeUser).filter(IsmsOrgNodeUser.org_node_id == node.id).delete(
        synchronize_session=False
    )
    seen = set()
    now = _utcnow()
    for uid in user_ids or []:
        if uid in seen:
            continue
        if (
            not db.query(User.id)
            .filter(User.id == uid, User.is_active.is_(True))
            .first()
        ):
            raise HTTPException(status_code=400, detail=f"Unknown user: {uid}")
        seen.add(uid)
        db.add(
            IsmsOrgNodeUser(
                org_node_id=node.id, user_id=uid, relationship_type=rel, created_at=now
            )
        )
    db.flush()
    try:
        db.expire(node, ["users"])
    except Exception:
        pass


def _replace_meeting_people(
    db: Session,
    meeting: IsmsMeeting,
    attendee_ids: list[uuid.UUID] | None,
    apology_ids: list[uuid.UUID] | None,
) -> None:
    if attendee_ids is None and apology_ids is None:
        return
    db.query(IsmsMeetingAttendee).filter(
        IsmsMeetingAttendee.meeting_id == meeting.id
    ).delete(synchronize_session=False)
    now = _utcnow()
    seen = set()
    for kind, ids in (("attendee", attendee_ids or []), ("apology", apology_ids or [])):
        for uid in ids:
            key = (uid, kind)
            if key in seen:
                continue
            if (
                not db.query(User.id)
                .filter(User.id == uid, User.is_active.is_(True))
                .first()
            ):
                raise HTTPException(status_code=400, detail=f"Unknown user: {uid}")
            seen.add(key)
            db.add(
                IsmsMeetingAttendee(
                    meeting_id=meeting.id,
                    user_id=uid,
                    attendance_type=kind,
                    created_at=now,
                )
            )
    db.flush()
    try:
        db.expire(meeting, ["attendees"])
    except Exception:
        pass


def _replace_meeting_links(
    db: Session, meeting: IsmsMeeting, links: list[MeetingLinkPayload] | None
) -> None:
    if links is None:
        return
    db.query(IsmsMeetingLink).filter(IsmsMeetingLink.meeting_id == meeting.id).delete(
        synchronize_session=False
    )
    now = _utcnow()
    for payload in links or []:
        link_type = _clean_text(
            payload.link_type or "external_url",
            max_len=32,
            required=True,
            label="link_type",
        )
        if link_type not in MEETING_LINK_TYPES:
            raise HTTPException(status_code=400, detail="Invalid meeting link_type")
        document_id = payload.document_id
        url = (
            _clean_text(payload.url, max_len=2048, required=False, label="url") or None
        )
        title = _clean_text(payload.title, max_len=256, required=False, label="title")
        if link_type == "isms_document":
            if (
                not document_id
                or not db.query(IsmsDocument.id)
                .filter(IsmsDocument.id == document_id)
                .first()
            ):
                raise HTTPException(status_code=400, detail="Unknown ISMS document")
            url = None
        elif not url:
            raise HTTPException(
                status_code=400, detail="url is required for external links"
            )
        db.add(
            IsmsMeetingLink(
                meeting_id=meeting.id,
                link_type=link_type,
                document_id=document_id if link_type == "isms_document" else None,
                title=title,
                url=url,
                created_at=now,
            )
        )
    db.flush()
    try:
        db.expire(meeting, ["links"])
    except Exception:
        pass


def _record(
    db: Session,
    entity_type: str,
    row: Any,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    user: User,
    request: Request | None = None,
) -> None:
    _, changelog_type = ENTITY_TYPES[entity_type]
    record_entity_changelog(
        db,
        entity_type=changelog_type,
        entity_id=row.id,
        action=action,
        before=before,
        after=after,
        user=user,
        request_method=getattr(request, "method", None),
        request_path=str(getattr(getattr(request, "url", None), "path", "") or "")
        or None,
    )


def _list_response(
    items: list[dict[str, Any]], total: int, limit: int, offset: int, framework: str
) -> dict[str, Any]:
    return {
        "framework": framework,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@router.get("/v1/isms/meta")
def isms_meta(
    framework: str = settings.default_framework_slug,
    section: str | None = None,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    section_key = _clean_isms_section(section)
    include_all = section_key is None

    def wants(*keys: str) -> bool:
        return include_all or section_key in keys

    payload: dict[str, Any] = {
        "framework": fw,
        "section": section_key or "all",
        "can_view": _can_read_isms(db, user),
        "can_manage": _can_manage_isms(db, user),
        "document_types": sorted(DOCUMENT_TYPES),
        "objective_statuses": sorted(OBJECTIVE_STATUSES),
        "application_values": ["Low", "Medium", "High"],
        "application_source_types": sorted(APP_SOURCE_TYPES),
        "access_statuses": ["Pending Approval", "Approved"],
        "effectiveness_threshold_operators": ["", "lt", "lte", "eq", "gte", "gt"],
    }

    if wants(
        "objectives", "org", "assets", "access", "effectiveness", "app", "meetings"
    ):
        users = (
            db.query(User)
            .filter(User.is_active.is_(True))
            .order_by(User.username.asc())
            .all()
        )
        payload["users"] = [_user_summary(u) for u in users]

    if wants("org", "assets", "access", "app"):
        org_nodes = (
            db.query(IsmsOrgNode)
            .order_by(IsmsOrgNode.sort_order.asc(), IsmsOrgNode.name.asc())
            .all()
        )
        payload["org_nodes"] = [
            _org_node_out(n, include_people=False) for n in org_nodes
        ]

    if wants("app", "meetings"):
        docs = db.query(IsmsDocument).order_by(IsmsDocument.title.asc()).all()
        payload["documents"] = [
            _document_out(db, d, fw, include_links=False) for d in docs
        ]

    if wants("access", "app"):
        assets = db.query(RiskAsset).order_by(func.lower(RiskAsset.name).asc()).all()
        payload["assets"] = [_asset_out(db, a, fw, include_links=False) for a in assets]

    if wants("assets"):
        licenses = (
            db.query(IsmsLicense).order_by(func.lower(IsmsLicense.name).asc()).all()
        )
        payload["asset_categories"] = _asset_categories_out(db)
        payload["licenses"] = [
            _license_out(row, include_asset_count=True) for row in licenses
        ]

    if wants("access"):
        aws_accounts = (
            db.query(IsmsAwsAccount)
            .order_by(func.lower(IsmsAwsAccount.name).asc())
            .all()
        )
        payload["aws_accounts"] = [
            _aws_account_out(row, include_entry_count=True) for row in aws_accounts
        ]

    if wants("app"):
        bps = (
            db.query(IsmsBusinessProcess)
            .order_by(
                IsmsBusinessProcess.sort_order.asc(), IsmsBusinessProcess.name.asc()
            )
            .all()
        )
        payload["business_processes"] = [_business_process_out(bp) for bp in bps]

    if wants("objectives", "documents", "org", "assets", "effectiveness", "meetings"):
        controls = (
            db.query(ControlItem)
            .filter(ControlItem.framework_slug == fw)
            .order_by(ControlItem.type.asc(), ControlItem.ref.asc())
            .all()
        )
        controls.sort(key=lambda c: (c.type, _ref_sort_key(c.ref)))
        payload["controls"] = [_control_out(c) for c in controls]

    if wants("objectives", "documents", "org", "assets", "meetings"):
        clauses = (
            db.query(FrameworkClause)
            .filter(FrameworkClause.framework_slug == fw)
            .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
            .all()
        )
        clauses.sort(key=lambda c: (int(c.sort_order or 0), _ref_sort_key(c.ref)))
        payload["clauses"] = [_clause_out(c) for c in clauses]

    if wants("effectiveness"):
        effectiveness_metric_sources = _effectiveness_metric_sources_out(db, user)
        payload["effectiveness_metric_sources"] = effectiveness_metric_sources
        payload["effectiveness_metric_source_types"] = [
            item["id"] for item in effectiveness_metric_sources
        ]

    return payload


@router.get("/v1/isms/summary")
def isms_summary(
    framework: str = settings.default_framework_slug,
    section: str | None = None,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    section_key = _clean_isms_section(section)
    include_all = section_key is None

    def wants(*keys: str) -> bool:
        return include_all or section_key in keys

    counts = {
        "objectives": int(db.query(IsmsObjective.id).count() or 0),
        "documents": int(db.query(IsmsDocument.id).count() or 0),
        "org_nodes": int(db.query(IsmsOrgNode.id).count() or 0),
        "assets": int(db.query(RiskAsset.id).count() or 0),
        "licenses": int(db.query(IsmsLicense.id).count() or 0),
        "aws_accounts": int(db.query(IsmsAwsAccount.id).count() or 0),
        "application_configurations": int(
            db.query(IsmsApplicationConfigurationEntry.id).count() or 0
        ),
        "access_control_matrix": int(
            db.query(IsmsAccessControlMatrixEntry.id).count() or 0
        ),
        "effectiveness_measures": int(
            db.query(IsmsEffectivenessMeasure.id)
            .filter(IsmsEffectivenessMeasure.framework_slug == fw)
            .count()
            or 0
        ),
        "effectiveness_metric_entries": int(
            db.query(IsmsEffectivenessMetricEntry.id)
            .join(
                IsmsEffectivenessMeasure,
                IsmsEffectivenessMeasure.id == IsmsEffectivenessMetricEntry.measure_id,
            )
            .filter(IsmsEffectivenessMeasure.framework_slug == fw)
            .count()
            or 0
        ),
        "meetings": int(db.query(IsmsMeeting.id).count() or 0),
        "business_processes": int(db.query(IsmsBusinessProcess.id).count() or 0),
    }
    payload: dict[str, Any] = {
        "framework": fw,
        "section": section_key or "all",
        "can_manage": _can_manage_isms(db, user),
        "counts": counts,
    }

    if wants("objectives"):
        objectives = (
            db.query(IsmsObjective)
            .order_by(IsmsObjective.updated_at.desc())
            .limit(500)
            .all()
        )
        payload["objectives"] = [_objective_out(db, row, fw) for row in objectives]

    if wants("documents"):
        documents = (
            db.query(IsmsDocument)
            .order_by(IsmsDocument.updated_at.desc())
            .limit(500)
            .all()
        )
        payload["documents"] = [_document_out(db, row, fw) for row in documents]

    if wants("org"):
        org_nodes = (
            db.query(IsmsOrgNode)
            .order_by(IsmsOrgNode.sort_order.asc(), IsmsOrgNode.name.asc())
            .limit(1000)
            .all()
        )
        payload["org_nodes"] = [
            _entity_out(db, "org_node", row, fw) for row in org_nodes
        ]

    if wants("assets"):
        assets = (
            db.query(RiskAsset)
            .order_by(func.lower(RiskAsset.name).asc())
            .limit(1000)
            .all()
        )
        payload["assets"] = [_asset_out(db, row, fw) for row in assets]

    if wants("app"):
        app_config = (
            db.query(IsmsApplicationConfigurationEntry)
            .order_by(IsmsApplicationConfigurationEntry.updated_at.desc())
            .limit(2000)
            .all()
        )
        payload["application_configurations"] = [
            _app_config_out(db, row, fw) for row in app_config
        ]
        bps = (
            db.query(IsmsBusinessProcess)
            .order_by(
                IsmsBusinessProcess.sort_order.asc(), IsmsBusinessProcess.name.asc()
            )
            .all()
        )
        payload["business_processes"] = [_business_process_out(row) for row in bps]

    if wants("access"):
        access_control_matrix = (
            db.query(IsmsAccessControlMatrixEntry)
            .order_by(IsmsAccessControlMatrixEntry.updated_at.desc())
            .limit(2000)
            .all()
        )
        payload["access_control_matrix"] = [
            _access_control_matrix_out(db, row, fw) for row in access_control_matrix
        ]

    if wants("effectiveness"):
        effectiveness_measures = (
            db.query(IsmsEffectivenessMeasure)
            .filter(IsmsEffectivenessMeasure.framework_slug == fw)
            .order_by(IsmsEffectivenessMeasure.updated_at.desc())
            .limit(1000)
            .all()
        )
        payload["effectiveness_measures"] = [
            _effectiveness_measure_out(db, row, fw) for row in effectiveness_measures
        ]

    if wants("meetings"):
        meetings = (
            db.query(IsmsMeeting)
            .order_by(IsmsMeeting.date.desc(), IsmsMeeting.start_time.desc())
            .limit(500)
            .all()
        )
        payload["meetings"] = [_meeting_out(db, row, fw) for row in meetings]

    return payload


@router.post("/v1/isms/objectives")
def create_objective(
    payload: ObjectivePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    status = (payload.status or "not_started").strip() or "not_started"
    if status not in OBJECTIVE_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid objective status")
    if (
        payload.owner_user_id
        and not db.query(User.id)
        .filter(User.id == payload.owner_user_id, User.is_active.is_(True))
        .first()
    ):
        raise HTTPException(status_code=400, detail="Unknown owner user")
    row = IsmsObjective(
        requirement=_clean_text(
            payload.requirement, max_len=20000, required=True, label="requirement"
        ),
        goal=_clean_text(payload.goal, max_len=20000, required=True, label="goal"),
        metric=_clean_text(payload.metric, max_len=20000),
        completion_method=_clean_text(payload.completion_method, max_len=12000),
        resource_requirements_text=_clean_text(
            payload.resource_requirements_text, max_len=12000
        ),
        owner_user_id=payload.owner_user_id,
        completion_target_date=_clean_text(payload.completion_target_date, max_len=64),
        evaluation_method=_clean_text(payload.evaluation_method, max_len=12000),
        status=status,
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    _replace_objective_resources(db, row, payload.resource_user_ids or [])
    _apply_links(db, "objective", row.id, fw, payload, user)
    after = _objective_out(db, row, fw)
    _record(db, "objective", row, "created", None, after, user, request)
    db.commit()
    db.refresh(row)
    return _objective_out(db, row, fw)


@router.get("/v1/isms/objectives")
def list_objectives(
    q: str = "",
    framework: str = settings.default_framework_slug,
    limit: int = 200,
    offset: int = 0,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))
    qry = db.query(IsmsObjective)
    if q.strip():
        needle = f"%{q.strip()}%"
        qry = qry.filter(
            or_(
                IsmsObjective.requirement.ilike(needle),
                IsmsObjective.goal.ilike(needle),
                IsmsObjective.metric.ilike(needle),
            )
        )
    total = int(qry.count() or 0)
    rows = (
        qry.order_by(IsmsObjective.updated_at.desc()).offset(offset).limit(limit).all()
    )
    return _list_response(
        [_objective_out(db, r, fw) for r in rows], total, limit, offset, fw
    )


@router.get("/v1/isms/objectives/{objective_id}")
def get_objective(
    objective_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return _objective_out(
        db,
        _by_id_or_404(db, IsmsObjective, objective_id, "Objective"),
        _clean_framework(framework),
    )


@router.patch("/v1/isms/objectives/{objective_id}")
def update_objective(
    objective_id: str,
    payload: ObjectivePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsObjective, objective_id, "Objective")
    before = _objective_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    for attr, max_len in [
        ("requirement", 20000),
        ("goal", 20000),
        ("metric", 20000),
        ("completion_method", 12000),
        ("resource_requirements_text", 12000),
        ("evaluation_method", 12000),
    ]:
        if attr in fields:
            setattr(
                row,
                attr,
                _clean_text(
                    getattr(payload, attr),
                    max_len=max_len,
                    required=attr in {"requirement", "goal"},
                    label=attr,
                ),
            )
    if "status" in fields:
        status = (payload.status or "not_started").strip() or "not_started"
        if status not in OBJECTIVE_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid objective status")
        row.status = status
    if "owner_user_id" in fields:
        row.owner_user_id = payload.owner_user_id
    if "completion_target_date" in fields:
        row.completion_target_date = _clean_text(
            payload.completion_target_date, max_len=64
        )
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    if "resource_user_ids" in fields:
        _replace_objective_resources(db, row, payload.resource_user_ids or [])
    _apply_links(db, "objective", row.id, fw, payload, user)
    after = _objective_out(db, row, fw)
    _record(db, "objective", row, "updated", before, after, user, request)
    db.commit()
    db.refresh(row)
    return _objective_out(db, row, fw)


@router.delete("/v1/isms/objectives/{objective_id}")
def delete_objective(
    objective_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsObjective, objective_id, "Objective")
    before = _objective_out(db, row, fw)
    _record(db, "objective", row, "deleted", before, None, user, request)
    _delete_entity_links(db, "objective", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/v1/isms/documents")
def create_document(
    payload: DocumentPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    dtype = (payload.document_type or "policy").strip().lower() or "policy"
    if dtype not in DOCUMENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid document_type")
    row = IsmsDocument(
        title=_clean_text(payload.title, max_len=256, required=True, label="title"),
        document_type=dtype,
        description=_clean_text(payload.description, max_len=20000),
        external_url=_clean_text(payload.external_url, max_len=2048) or None,
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    _apply_links(db, "document", row.id, fw, payload, user)
    after = _document_out(db, row, fw)
    _record(db, "document", row, "created", None, after, user, request)
    db.commit()
    db.refresh(row)
    return _document_out(db, row, fw)


@router.get("/v1/isms/documents")
def list_documents(
    q: str = "",
    framework: str = settings.default_framework_slug,
    limit: int = 200,
    offset: int = 0,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))
    qry = db.query(IsmsDocument)
    if q.strip():
        needle = f"%{q.strip()}%"
        qry = qry.filter(
            or_(
                IsmsDocument.title.ilike(needle),
                IsmsDocument.description.ilike(needle),
                IsmsDocument.external_url.ilike(needle),
            )
        )
    total = int(qry.count() or 0)
    rows = (
        qry.order_by(IsmsDocument.updated_at.desc(), IsmsDocument.title.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return _list_response(
        [_document_out(db, r, fw) for r in rows], total, limit, offset, fw
    )


@router.get("/v1/isms/documents/{document_id}")
def get_document(
    document_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return _document_out(
        db,
        _by_id_or_404(db, IsmsDocument, document_id, "Document"),
        _clean_framework(framework),
    )


@router.patch("/v1/isms/documents/{document_id}")
def update_document(
    document_id: str,
    payload: DocumentPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsDocument, document_id, "Document")
    before = _document_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    if "title" in fields:
        row.title = _clean_text(
            payload.title, max_len=256, required=True, label="title"
        )
    if "document_type" in fields:
        dtype = (payload.document_type or "policy").strip().lower() or "policy"
        if dtype not in DOCUMENT_TYPES:
            raise HTTPException(status_code=400, detail="Invalid document_type")
        row.document_type = dtype
    if "description" in fields:
        row.description = _clean_text(payload.description, max_len=20000)
    if "external_url" in fields:
        row.external_url = _clean_text(payload.external_url, max_len=2048) or None
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    _apply_links(db, "document", row.id, fw, payload, user)
    after = _document_out(db, row, fw)
    _record(db, "document", row, "updated", before, after, user, request)
    db.commit()
    db.refresh(row)
    return _document_out(db, row, fw)


@router.post("/v1/isms/documents/{document_id}/file")
async def upload_document_file(
    document_id: str,
    request: Request,
    file: UploadFile = File(...),
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsDocument, document_id, "Document")
    before = _document_out(db, row, fw)
    filename = os.path.basename(getattr(file, "filename", None) or "isms-document")[
        :255
    ]
    content_type = getattr(file, "content_type", None) or "application/octet-stream"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="ISMS document is too large")
    ext = os.path.splitext(filename)[1]
    stored = put_bytes(
        f"isms-documents/{row.id}/{uuid.uuid4()}{ext}", data, content_type
    )
    now = _utcnow()
    row.storage_uri = stored.uri
    row.filename = filename
    row.content_type = content_type
    row.sha256 = stored.sha256
    row.size_bytes = stored.size_bytes
    row.uploaded_at = now
    row.uploaded_by_user_id = user.id
    row.updated_at = now
    db.add(row)
    db.flush()
    after = _document_out(db, row, fw)
    _record(db, "document", row, "updated", before, after, user, request)
    db.commit()
    db.refresh(row)
    return {"ok": True, "document": _document_out(db, row, fw)}


@router.get("/v1/isms/documents/{document_id}/file")
def download_document_file(
    document_id: str, user=Depends(require_isms_read), db: Session = Depends(get_db)
):
    row = _by_id_or_404(db, IsmsDocument, document_id, "Document")
    if not row.storage_uri:
        raise HTTPException(status_code=404, detail="No file uploaded")
    bucket, key = parse_s3_uri(row.storage_uri)
    obj = get_object_stream(bucket, key)
    fname = row.filename or "isms-document"
    media = row.content_type or "application/octet-stream"
    return StreamingResponse(
        iter_stream(obj["Body"]),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.delete("/v1/isms/documents/{document_id}")
def delete_document(
    document_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsDocument, document_id, "Document")
    before = _document_out(db, row, fw)
    _record(db, "document", row, "deleted", before, None, user, request)
    _delete_entity_links(db, "document", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/v1/isms/org-nodes")
def create_org_node(
    payload: OrgNodePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    if (
        payload.parent_id
        and not db.query(IsmsOrgNode.id)
        .filter(IsmsOrgNode.id == payload.parent_id)
        .first()
    ):
        raise HTTPException(status_code=400, detail="Unknown parent org node")
    row = IsmsOrgNode(
        parent_id=payload.parent_id,
        name=_clean_text(payload.name, max_len=256, required=True, label="name"),
        node_type=_clean_text(
            payload.node_type or "role", max_len=64, required=True, label="node_type"
        ),
        description=_clean_text(payload.description, max_len=20000),
        sort_order=int(payload.sort_order or 0),
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    _replace_org_node_users(db, row, payload.user_ids or [], payload.relationship_type)
    _apply_links(db, "org_node", row.id, fw, payload, user)
    after = _entity_out(db, "org_node", row, fw)
    _record(db, "org_node", row, "created", None, after, user, request)
    db.commit()
    db.refresh(row)
    return _entity_out(db, "org_node", row, fw)


@router.get("/v1/isms/org-nodes")
def list_org_nodes(
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    rows = (
        db.query(IsmsOrgNode)
        .order_by(IsmsOrgNode.sort_order.asc(), IsmsOrgNode.name.asc())
        .all()
    )
    return {
        "framework": fw,
        "items": [_entity_out(db, "org_node", r, fw) for r in rows],
    }


@router.patch("/v1/isms/org-nodes/{node_id}")
def update_org_node(
    node_id: str,
    payload: OrgNodePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsOrgNode, node_id, "Organisation chart node")
    before = _entity_out(db, "org_node", row, fw)
    fields = set(payload.model_fields_set or set())
    if "parent_id" in fields:
        row.parent_id = payload.parent_id
    if "name" in fields:
        row.name = _clean_text(payload.name, max_len=256, required=True, label="name")
    if "node_type" in fields:
        row.node_type = _clean_text(
            payload.node_type, max_len=64, required=True, label="node_type"
        )
    if "description" in fields:
        row.description = _clean_text(payload.description, max_len=20000)
    if "sort_order" in fields:
        row.sort_order = int(payload.sort_order or 0)
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    if "user_ids" in fields:
        _replace_org_node_users(
            db, row, payload.user_ids or [], payload.relationship_type
        )
    _apply_links(db, "org_node", row.id, fw, payload, user)
    after = _entity_out(db, "org_node", row, fw)
    _record(db, "org_node", row, "updated", before, after, user, request)
    db.commit()
    db.refresh(row)
    return _entity_out(db, "org_node", row, fw)


@router.delete("/v1/isms/org-nodes/{node_id}")
def delete_org_node(
    node_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsOrgNode, node_id, "Organisation chart node")
    before = _entity_out(db, "org_node", row, fw)
    _record(db, "org_node", row, "deleted", before, None, user, request)
    _delete_entity_links(db, "org_node", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/v1/isms/asset-categories")
def list_asset_categories(
    user=Depends(require_isms_read), db: Session = Depends(get_db)
):
    return {"items": _asset_categories_out(db)}


@router.post("/v1/isms/asset-categories")
def create_asset_category(
    payload: AssetCategoryPayload,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    name = _clean_text(payload.name, max_len=128, required=True, label="name")
    existing = (
        db.query(RiskCategory)
        .filter(func.lower(RiskCategory.name) == name.lower())
        .one_or_none()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Asset category already exists")
    row = RiskCategory(name=name, created_at=_utcnow(), updated_at=_utcnow())
    db.add(row)
    db.flush()
    db.add(
        RiskAssetSubcategory(
            category_id=row.id,
            name="General",
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
    )
    db.commit()
    db.refresh(row)
    return {"id": str(row.id), "name": row.name}


@router.post("/v1/isms/asset-subcategories")
def create_asset_subcategory(
    payload: AssetSubcategoryPayload,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    category = _asset_category_or_create(
        db, category_id=payload.category_id, category_name=payload.category_name
    )
    name = _clean_text(payload.name, max_len=128, required=True, label="name")
    existing = (
        db.query(RiskAssetSubcategory)
        .filter(
            RiskAssetSubcategory.category_id == category.id,
            func.lower(RiskAssetSubcategory.name) == name.lower(),
        )
        .one_or_none()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Asset subcategory already exists")
    row = RiskAssetSubcategory(
        category_id=category.id,
        name=name,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": str(row.id), "name": row.name, "category_id": str(row.category_id)}


@router.get("/v1/isms/licenses")
def list_licenses(
    q: str = "",
    framework: str = settings.default_framework_slug,
    limit: int = 500,
    offset: int = 0,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    limit = max(1, min(int(limit or 500), 1000))
    offset = max(0, int(offset or 0))
    qry = db.query(IsmsLicense)
    if q.strip():
        needle = f"%{q.strip()}%"
        qry = qry.filter(
            or_(
                IsmsLicense.name.ilike(needle),
                IsmsLicense.description.ilike(needle),
            )
        )
    total = int(qry.count() or 0)
    rows = (
        qry.order_by(func.lower(IsmsLicense.name).asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return _list_response(
        [_license_out(row, include_asset_count=True) for row in rows],
        total,
        limit,
        offset,
        fw,
    )


@router.post("/v1/isms/licenses")
def create_license(
    payload: LicensePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    name = _clean_text(payload.name, max_len=256, required=True, label="license")
    existing = _license_by_name(db, name)
    if existing:
        raise HTTPException(status_code=400, detail="License already exists")
    row = IsmsLicense(
        name=name,
        description=_clean_text(payload.description, max_len=12000),
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    after = _license_out(row, include_asset_count=True)
    _record(db, "license", row, "created", None, after, user, request)
    db.commit()
    db.refresh(row)
    return _license_out(row, include_asset_count=True)


@router.patch("/v1/isms/licenses/{license_id}")
def update_license(
    license_id: str,
    payload: LicensePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    _clean_framework(framework)
    row = _license_or_404(db, license_id)
    before = _license_out(row, include_asset_count=True)
    new_name = _clean_text(payload.name, max_len=256, required=True, label="license")
    conflict = (
        db.query(IsmsLicense)
        .filter(
            func.lower(IsmsLicense.name) == new_name.lower(), IsmsLicense.id != row.id
        )
        .one_or_none()
    )
    if conflict:
        raise HTTPException(status_code=400, detail="License already exists")
    _apply_license_payload(row, payload)
    # Keep the legacy text column synchronized for older clients/searches.
    db.query(RiskAsset).filter(RiskAsset.license_id == row.id).update(
        {RiskAsset.license: row.name, RiskAsset.updated_at: _utcnow()},
        synchronize_session=False,
    )
    db.add(row)
    db.flush()
    after = _license_out(row, include_asset_count=True)
    _record(db, "license", row, "updated", before, after, user, request)
    db.commit()
    db.refresh(row)
    return _license_out(row, include_asset_count=True)


@router.delete("/v1/isms/licenses/{license_id}")
def delete_license(
    license_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    _clean_framework(framework)
    row = _license_or_404(db, license_id)
    in_use = db.query(RiskAsset.id).filter(RiskAsset.license_id == row.id).first()
    if in_use:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a license that is related to one or more assets",
        )
    before = _license_out(row, include_asset_count=True)
    _record(db, "license", row, "deleted", before, None, user, request)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/v1/isms/assets")
def create_asset(
    payload: AssetPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    category = _asset_category_or_create(
        db, category_id=payload.category_id, category_name=payload.category_name
    )
    subcategory = _asset_subcategory_or_create(
        db,
        category=category,
        subcategory_id=payload.subcategory_id,
        subcategory_name=payload.subcategory_name,
    )
    row = RiskAsset(
        name=_asset_name_from_payload(payload),
        category_id=category.id,
        subcategory_id=subcategory.id,
        owner_org_node_id=payload.owner_org_node_id,
        register_held_by_org_node_id=payload.register_held_by_org_node_id,
        description=_clean_text(payload.description, max_len=20000),
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    conflict = (
        db.query(RiskAsset)
        .filter(
            func.lower(RiskAsset.name) == row.name.lower(),
            RiskAsset.category_id == category.id,
            RiskAsset.subcategory_id == subcategory.id,
        )
        .one_or_none()
    )
    if conflict:
        raise HTTPException(status_code=400, detail="Asset already exists")
    license_row = _license_from_payload(db, payload, user)
    row.license_id = license_row.id if license_row else None
    row.license = license_row.name if license_row else ""
    db.add(row)
    db.flush()
    _apply_links(db, "asset", row.id, fw, payload, user)
    after = _asset_out(db, row, fw)
    _record(db, "asset", row, "created", None, after, user, request)
    db.commit()
    db.refresh(row)
    return _asset_out(db, row, fw)


@router.get("/v1/isms/assets")
def list_assets(
    q: str = "",
    framework: str = settings.default_framework_slug,
    category_id: str = "",
    subcategory_id: str = "",
    limit: int = 500,
    offset: int = 0,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    limit = max(1, min(int(limit or 500), 1000))
    offset = max(0, int(offset or 0))
    qry = (
        db.query(RiskAsset)
        .outerjoin(RiskCategory)
        .outerjoin(RiskAssetSubcategory)
        .outerjoin(IsmsLicense, IsmsLicense.id == RiskAsset.license_id)
    )
    if q.strip():
        needle = f"%{q.strip()}%"
        qry = qry.filter(
            or_(
                RiskAsset.name.ilike(needle),
                RiskAsset.license.ilike(needle),
                IsmsLicense.name.ilike(needle),
                RiskAsset.description.ilike(needle),
                RiskCategory.name.ilike(needle),
                RiskAssetSubcategory.name.ilike(needle),
            )
        )
    cid = _try_uuid(category_id) if category_id else None
    if category_id and not cid:
        raise HTTPException(status_code=400, detail="category_id must be a UUID")
    if cid:
        qry = qry.filter(RiskAsset.category_id == cid)
    sid = _try_uuid(subcategory_id) if subcategory_id else None
    if subcategory_id and not sid:
        raise HTTPException(status_code=400, detail="subcategory_id must be a UUID")
    if sid:
        qry = qry.filter(RiskAsset.subcategory_id == sid)
    total = int(qry.count() or 0)
    rows = (
        qry.order_by(func.lower(RiskAsset.name).asc()).offset(offset).limit(limit).all()
    )
    return _list_response(
        [_asset_out(db, r, fw) for r in rows], total, limit, offset, fw
    )


@router.get("/v1/isms/assets/{asset_id}")
def get_asset(
    asset_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return _asset_out(db, _asset_or_404(db, asset_id), _clean_framework(framework))


@router.patch("/v1/isms/assets/{asset_id}")
def update_asset(
    asset_id: str,
    payload: AssetPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _asset_or_404(db, asset_id)
    before = _asset_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    category = row.category
    if {"category_id", "category_name"} & fields:
        category = _asset_category_or_create(
            db, category_id=payload.category_id, category_name=payload.category_name
        )
    subcategory = row.subcategory
    if {"subcategory_id", "subcategory_name", "category_id", "category_name"} & fields:
        subcategory = _asset_subcategory_or_create(
            db,
            category=category,
            subcategory_id=payload.subcategory_id,
            subcategory_name=(
                payload.subcategory_name
                if "subcategory_name" in fields
                else (
                    row.subcategory.name
                    if row.subcategory and row.subcategory.category_id == category.id
                    else None
                )
            ),
        )
    if {"asset", "asset_name", "name"} & fields:
        row.name = _asset_name_from_payload(payload)
    if category:
        row.category_id = category.id
    if subcategory:
        row.subcategory_id = subcategory.id
    conflict = (
        db.query(RiskAsset)
        .filter(
            func.lower(RiskAsset.name) == row.name.lower(),
            RiskAsset.category_id == row.category_id,
            RiskAsset.subcategory_id == row.subcategory_id,
            RiskAsset.id != row.id,
        )
        .one_or_none()
    )
    if conflict:
        raise HTTPException(status_code=400, detail="Asset already exists")
    if {"license_id", "license_name", "license"} & fields:
        license_row = _license_from_payload(db, payload, user)
        row.license_id = license_row.id if license_row else None
        row.license = license_row.name if license_row else ""
    if "owner_org_node_id" in fields:
        row.owner_org_node_id = payload.owner_org_node_id
    if "register_held_by_org_node_id" in fields:
        row.register_held_by_org_node_id = payload.register_held_by_org_node_id
    if "description" in fields:
        row.description = _clean_text(payload.description, max_len=20000)
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    _apply_links(db, "asset", row.id, fw, payload, user)
    after = _asset_out(db, row, fw)
    _record(db, "asset", row, "updated", before, after, user, request)
    db.commit()
    db.refresh(row)
    return _asset_out(db, row, fw)


@router.delete("/v1/isms/assets/{asset_id}")
def delete_asset(
    asset_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _asset_or_404(db, asset_id)
    in_use = db.query(Risk.id).filter(Risk.asset_id == row.id).first()
    if in_use:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete an asset referenced by CIA Triad risks",
        )
    before = _asset_out(db, row, fw)
    _record(db, "asset", row, "deleted", before, None, user, request)
    _delete_entity_links(db, "asset", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/v1/isms/business-processes")
def create_business_process(
    payload: BusinessProcessPayload,
    request: Request,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    row = IsmsBusinessProcess(
        name=_clean_text(payload.name, max_len=256, required=True, label="name"),
        description=_clean_text(payload.description, max_len=20000),
        sort_order=int(payload.sort_order or 0),
        is_default=False,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    after = _business_process_out(row)
    record_entity_changelog(
        db,
        entity_type="isms_business_process",
        entity_id=row.id,
        action="created",
        before=None,
        after=after,
        user=user,
        request_method=request.method,
        request_path=request.url.path,
    )
    db.commit()
    return after


@router.get("/v1/isms/business-processes")
def list_business_processes(
    user=Depends(require_isms_read), db: Session = Depends(get_db)
):
    rows = (
        db.query(IsmsBusinessProcess)
        .order_by(IsmsBusinessProcess.sort_order.asc(), IsmsBusinessProcess.name.asc())
        .all()
    )
    return {"items": [_business_process_out(r) for r in rows]}


@router.post("/v1/isms/application-configuration")
def create_app_config(
    payload: AppConfigPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    source_type = (payload.source_type or "").strip()
    value = (payload.value or "Low").strip() or "Low"
    if source_type not in APP_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid source_type")
    if value not in APP_VALUES:
        raise HTTPException(status_code=400, detail="Invalid value")
    ids = {
        "document": payload.document_id,
        "person": payload.user_id,
        "asset": payload.asset_id,
        "org_node": payload.org_node_id,
    }
    if not ids[source_type]:
        raise HTTPException(status_code=400, detail="Selected source id is required")
    if (
        not payload.business_process_id
        or not db.query(IsmsBusinessProcess.id)
        .filter(IsmsBusinessProcess.id == payload.business_process_id)
        .first()
    ):
        raise HTTPException(status_code=400, detail="Unknown business process")
    row = IsmsApplicationConfigurationEntry(
        source_type=source_type,
        document_id=payload.document_id if source_type == "document" else None,
        user_id=payload.user_id if source_type == "person" else None,
        asset_id=payload.asset_id if source_type == "asset" else None,
        org_node_id=payload.org_node_id if source_type == "org_node" else None,
        business_process_id=payload.business_process_id,
        value=value,
        notes=_clean_text(payload.notes, max_len=12000),
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    after = _app_config_out(db, row, fw)
    _record(db, "application_configuration", row, "created", None, after, user, request)
    db.commit()
    db.refresh(row)
    return _app_config_out(db, row, fw)


@router.get("/v1/isms/application-configuration")
def list_app_config(
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    rows = (
        db.query(IsmsApplicationConfigurationEntry)
        .order_by(IsmsApplicationConfigurationEntry.updated_at.desc())
        .all()
    )
    return {"framework": fw, "items": [_app_config_out(db, r, fw) for r in rows]}


@router.patch("/v1/isms/application-configuration/{entry_id}")
def update_app_config(
    entry_id: str,
    payload: AppConfigPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsApplicationConfigurationEntry, entry_id, "Application configuration"
    )
    before = _app_config_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    if "business_process_id" in fields:
        row.business_process_id = payload.business_process_id
    if "value" in fields:
        value = (payload.value or "Low").strip() or "Low"
        if value not in APP_VALUES:
            raise HTTPException(status_code=400, detail="Invalid value")
        row.value = value
    if "notes" in fields:
        row.notes = _clean_text(payload.notes, max_len=12000)
    if "source_type" in fields:
        source_type = (payload.source_type or "").strip()
        if source_type not in APP_SOURCE_TYPES:
            raise HTTPException(status_code=400, detail="Invalid source_type")
        row.source_type = source_type
        row.document_id = payload.document_id if source_type == "document" else None
        row.user_id = payload.user_id if source_type == "person" else None
        row.asset_id = payload.asset_id if source_type == "asset" else None
        row.org_node_id = payload.org_node_id if source_type == "org_node" else None
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    after = _app_config_out(db, row, fw)
    _record(
        db, "application_configuration", row, "updated", before, after, user, request
    )
    _delete_entity_links(db, "application_configuration", row.id, fw)
    db.commit()
    return _app_config_out(db, row, fw)


@router.delete("/v1/isms/application-configuration/{entry_id}")
def delete_app_config(
    entry_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsApplicationConfigurationEntry, entry_id, "Application configuration"
    )
    before = _app_config_out(db, row, fw)
    _record(
        db, "application_configuration", row, "deleted", before, None, user, request
    )
    _delete_entity_links(db, "application_configuration", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/v1/isms/aws-accounts")
def create_aws_account(
    payload: AwsAccountPayload,
    request: Request,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    name = _clean_text(
        payload.name, max_len=256, required=True, label="hosting account name"
    )
    account_id = _clean_text(payload.account_id, max_len=32) or None
    if (
        account_id
        and db.query(IsmsAwsAccount.id)
        .filter(IsmsAwsAccount.account_id == account_id)
        .first()
    ):
        raise HTTPException(status_code=400, detail="Hosting account id already exists")
    row = IsmsAwsAccount(
        name=name,
        account_id=account_id,
        notes=_clean_text(payload.notes, max_len=12000),
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    after = _aws_account_out(row, include_entry_count=True)
    record_entity_changelog(
        db,
        entity_type="isms_aws_account",
        entity_id=row.id,
        action="created",
        before=None,
        after=after,
        user=user,
        request_method=request.method,
        request_path=request.url.path,
    )
    db.commit()
    return after


@router.get("/v1/isms/aws-accounts")
def list_aws_accounts(user=Depends(require_isms_read), db: Session = Depends(get_db)):
    return {"items": _aws_accounts_out(db)}


@router.patch("/v1/isms/aws-accounts/{account_id}")
def update_aws_account(
    account_id: str,
    payload: AwsAccountPayload,
    request: Request,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    row = _by_id_or_404(db, IsmsAwsAccount, account_id, "hosting account")
    before = _aws_account_out(row, include_entry_count=True)
    fields = set(payload.model_fields_set or set())
    if "name" in fields:
        row.name = _clean_text(
            payload.name, max_len=256, required=True, label="hosting account name"
        )
    if "account_id" in fields:
        next_account_id = _clean_text(payload.account_id, max_len=32) or None
        if next_account_id:
            existing = (
                db.query(IsmsAwsAccount.id)
                .filter(
                    IsmsAwsAccount.account_id == next_account_id,
                    IsmsAwsAccount.id != row.id,
                )
                .first()
            )
            if existing:
                raise HTTPException(
                    status_code=400, detail="Hosting account id already exists"
                )
        row.account_id = next_account_id
    if "notes" in fields:
        row.notes = _clean_text(payload.notes, max_len=12000)
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    after = _aws_account_out(row, include_entry_count=True)
    record_entity_changelog(
        db,
        entity_type="isms_aws_account",
        entity_id=row.id,
        action="updated",
        before=before,
        after=after,
        user=user,
        request_method=request.method,
        request_path=request.url.path,
    )
    db.commit()
    return after


@router.delete("/v1/isms/aws-accounts/{account_id}")
def delete_aws_account(
    account_id: str,
    request: Request,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    row = _by_id_or_404(db, IsmsAwsAccount, account_id, "hosting account")
    before = _aws_account_out(row, include_entry_count=True)
    record_entity_changelog(
        db,
        entity_type="isms_aws_account",
        entity_id=row.id,
        action="deleted",
        before=before,
        after=None,
        user=user,
        request_method=request.method,
        request_path=request.url.path,
    )
    db.delete(row)
    db.commit()
    return {"ok": True}


def _validate_access_control_payload(
    db: Session, payload: AccessControlMatrixPayload, fields: set[str] | None = None
) -> None:
    fields = fields or set(payload.model_fields_set or set())
    if (not fields or "service_asset_id" in fields) and payload.service_asset_id:
        if (
            not db.query(RiskAsset.id)
            .filter(RiskAsset.id == payload.service_asset_id)
            .first()
        ):
            raise HTTPException(status_code=400, detail="Unknown service asset")
    if (not fields or "approved_by_user_id" in fields) and payload.approved_by_user_id:
        if (
            not db.query(User.id)
            .filter(User.id == payload.approved_by_user_id, User.is_active.is_(True))
            .first()
        ):
            raise HTTPException(status_code=400, detail="Unknown approved-by user")
    role_ids: list[uuid.UUID] = []
    if not fields or "role_org_node_ids" in fields:
        role_ids.extend(payload.role_org_node_ids or [])
    if (not fields or "role_org_node_id" in fields) and payload.role_org_node_id:
        role_ids.append(payload.role_org_node_id)
    for role_id in set(role_ids):
        if not db.query(IsmsOrgNode.id).filter(IsmsOrgNode.id == role_id).first():
            raise HTTPException(
                status_code=400, detail="Unknown organisation chart role"
            )
    if (not fields or "status" in fields) and payload.status:
        if payload.status.strip() not in ACCESS_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid access control status")


@router.post("/v1/isms/access-control-matrix")
def create_access_control_matrix_entry(
    payload: AccessControlMatrixPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    if not payload.service_asset_id:
        raise HTTPException(status_code=400, detail="service_asset_id is required")
    if not payload.aws_account_ids:
        raise HTTPException(
            status_code=400, detail="At least one hosting account is required"
        )
    _validate_access_control_payload(db, payload)
    status = (payload.status or "Pending Approval").strip() or "Pending Approval"
    row = IsmsAccessControlMatrixEntry(
        task_action=_clean_text(
            payload.task_action, max_len=20000, required=True, label="task/action"
        ),
        service_asset_id=payload.service_asset_id,
        status=status,
        approved_by_user_id=payload.approved_by_user_id,
        notes=_clean_text(payload.notes, max_len=12000),
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    _replace_access_control_accounts(db, row, payload.aws_account_ids)
    _replace_access_control_roles(
        db,
        row,
        (
            payload.role_org_node_ids
            if payload.role_org_node_ids is not None
            else ([payload.role_org_node_id] if payload.role_org_node_id else [])
        ),
    )
    db.flush()
    after = _access_control_matrix_out(db, row, fw)
    _record(db, "access_control_matrix", row, "created", None, after, user, request)
    db.commit()
    db.refresh(row)
    return _access_control_matrix_out(db, row, fw)


@router.get("/v1/isms/access-control-matrix")
def list_access_control_matrix(
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    rows = (
        db.query(IsmsAccessControlMatrixEntry)
        .order_by(IsmsAccessControlMatrixEntry.updated_at.desc())
        .all()
    )
    return {
        "framework": fw,
        "items": [_access_control_matrix_out(db, row, fw) for row in rows],
    }


@router.get("/v1/isms/access-control-matrix/{entry_id}")
def get_access_control_matrix_entry(
    entry_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return _access_control_matrix_out(
        db,
        _by_id_or_404(
            db, IsmsAccessControlMatrixEntry, entry_id, "Access control matrix row"
        ),
        _clean_framework(framework),
    )


@router.patch("/v1/isms/access-control-matrix/{entry_id}")
def update_access_control_matrix_entry(
    entry_id: str,
    payload: AccessControlMatrixPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsAccessControlMatrixEntry, entry_id, "Access control matrix row"
    )
    before = _access_control_matrix_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    _validate_access_control_payload(db, payload, fields)
    if "task_action" in fields:
        row.task_action = _clean_text(
            payload.task_action, max_len=20000, required=True, label="task/action"
        )
    if "service_asset_id" in fields:
        if not payload.service_asset_id:
            raise HTTPException(status_code=400, detail="service_asset_id is required")
        row.service_asset_id = payload.service_asset_id
    if "status" in fields:
        row.status = (
            payload.status or "Pending Approval"
        ).strip() or "Pending Approval"
    if "approved_by_user_id" in fields:
        row.approved_by_user_id = payload.approved_by_user_id
    if "role_org_node_ids" in fields:
        _replace_access_control_roles(db, row, payload.role_org_node_ids or [])
    elif "role_org_node_id" in fields:
        _replace_access_control_roles(
            db, row, [payload.role_org_node_id] if payload.role_org_node_id else []
        )
    if "notes" in fields:
        row.notes = _clean_text(payload.notes, max_len=12000)
    if "aws_account_ids" in fields:
        if not payload.aws_account_ids:
            raise HTTPException(
                status_code=400, detail="At least one hosting account is required"
            )
        _replace_access_control_accounts(db, row, payload.aws_account_ids)
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    after = _access_control_matrix_out(db, row, fw)
    _record(db, "access_control_matrix", row, "updated", before, after, user, request)
    db.commit()
    return _access_control_matrix_out(db, row, fw)


@router.delete("/v1/isms/access-control-matrix/{entry_id}")
def delete_access_control_matrix_entry(
    entry_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsAccessControlMatrixEntry, entry_id, "Access control matrix row"
    )
    before = _access_control_matrix_out(db, row, fw)
    _record(db, "access_control_matrix", row, "deleted", before, None, user, request)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/v1/isms/effectiveness-measures")
def create_effectiveness_measure(
    payload: EffectivenessMeasurePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    _validate_effectiveness_measure_payload(db, payload)
    row = IsmsEffectivenessMeasure(
        framework_slug=fw,
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_effectiveness_measure_payload(row, payload)
    db.add(row)
    db.flush()
    _apply_effectiveness_links(db, row.id, fw, payload, user)
    db.flush()
    after = _effectiveness_measure_out(db, row, fw)
    _record(db, "effectiveness_measure", row, "created", None, after, user, request)
    db.commit()
    return _effectiveness_measure_out(db, row, fw)


@router.get("/v1/isms/effectiveness-measures")
def list_effectiveness_measures(
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    rows = (
        db.query(IsmsEffectivenessMeasure)
        .filter(IsmsEffectivenessMeasure.framework_slug == fw)
        .order_by(IsmsEffectivenessMeasure.updated_at.desc())
        .all()
    )
    return {
        "framework": fw,
        "items": [_effectiveness_measure_out(db, row, fw) for row in rows],
    }


@router.get("/v1/isms/effectiveness-measures/{measure_id}")
def get_effectiveness_measure(
    measure_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if row.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness measure not found")
    return _effectiveness_measure_out(db, row, fw)


@router.patch("/v1/isms/effectiveness-measures/{measure_id}")
def update_effectiveness_measure(
    measure_id: str,
    payload: EffectivenessMeasurePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if row.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness measure not found")
    before = _effectiveness_measure_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    _validate_effectiveness_measure_payload(db, payload, fields=fields, row_id=row.id)
    _apply_effectiveness_measure_payload(row, payload, partial=True)
    _apply_effectiveness_links(db, row.id, fw, payload, user)
    db.add(row)
    db.flush()
    after = _effectiveness_measure_out(db, row, fw)
    _record(db, "effectiveness_measure", row, "updated", before, after, user, request)
    db.commit()
    return _effectiveness_measure_out(db, row, fw)


@router.delete("/v1/isms/effectiveness-measures/{measure_id}")
def delete_effectiveness_measure(
    measure_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if row.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness measure not found")
    before = _effectiveness_measure_out(db, row, fw)
    _record(db, "effectiveness_measure", row, "deleted", before, None, user, request)
    _delete_entity_links(db, "effectiveness_measure", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/v1/isms/effectiveness-measures/{measure_id}/metrics")
def create_effectiveness_metric_entry(
    measure_id: str,
    payload: EffectivenessMetricEntryPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    measure = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if measure.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness measure not found")
    _validate_metric_entry_payload(db, payload)
    row = IsmsEffectivenessMetricEntry(
        measure_id=measure.id,
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_metric_entry_payload(row, payload, db)
    if not row.metric_unit:
        row.metric_unit = measure.target_unit or ""
    db.add(row)
    measure.updated_at = _utcnow()
    db.add(measure)
    db.flush()
    after = _metric_entry_out(row)
    record_entity_changelog(
        db,
        entity_type="isms_effectiveness_metric",
        entity_id=row.id,
        action="created",
        before=None,
        after=after,
        user=user,
        request_method=request.method,
        request_path=request.url.path,
    )
    db.commit()
    return _metric_entry_out(row)


@router.get("/v1/isms/effectiveness-measures/{measure_id}/metrics")
def list_effectiveness_metric_entries(
    measure_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    measure = _by_id_or_404(
        db, IsmsEffectivenessMeasure, measure_id, "Effectiveness measure"
    )
    if measure.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness measure not found")
    rows = (
        db.query(IsmsEffectivenessMetricEntry)
        .filter(IsmsEffectivenessMetricEntry.measure_id == measure.id)
        .order_by(
            IsmsEffectivenessMetricEntry.recorded_at.desc(),
            IsmsEffectivenessMetricEntry.created_at.desc(),
        )
        .all()
    )
    return {"items": [_metric_entry_out(row) for row in rows]}


def _metric_entry_detail_out(
    db: Session, row: IsmsEffectivenessMetricEntry, framework: str
) -> dict[str, Any]:
    out = _metric_entry_out(row) or {}
    measure = row.measure
    if measure and measure.framework_slug == framework:
        out["framework"] = measure.framework_slug
        out["measure"] = _effectiveness_measure_out(
            db, measure, framework, include_entries=False
        )
    else:
        out["framework"] = framework
        out["measure"] = None
    return out


@router.get("/v1/isms/effectiveness-metrics/{entry_id}")
def get_effectiveness_metric_entry(
    entry_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMetricEntry, entry_id, "Effectiveness metric"
    )
    if not row.measure or row.measure.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness metric not found")
    return _metric_entry_detail_out(db, row, fw)


@router.patch("/v1/isms/effectiveness-metrics/{entry_id}")
def update_effectiveness_metric_entry(
    entry_id: str,
    payload: EffectivenessMetricEntryPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMetricEntry, entry_id, "Effectiveness metric"
    )
    if not row.measure or row.measure.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness metric not found")
    before = _metric_entry_out(row)
    _validate_metric_entry_payload(db, payload, partial=True)
    _apply_metric_entry_payload(row, payload, db, partial=True)
    row.measure.updated_at = _utcnow()
    db.add(row)
    db.add(row.measure)
    db.flush()
    after = _metric_entry_out(row)
    record_entity_changelog(
        db,
        entity_type="isms_effectiveness_metric",
        entity_id=row.id,
        action="updated",
        before=before,
        after=after,
        user=user,
        request_method=request.method,
        request_path=request.url.path,
    )
    db.commit()
    return _metric_entry_out(row)


@router.delete("/v1/isms/effectiveness-metrics/{entry_id}")
def delete_effectiveness_metric_entry(
    entry_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(
        db, IsmsEffectivenessMetricEntry, entry_id, "Effectiveness metric"
    )
    if not row.measure or row.measure.framework_slug != fw:
        raise HTTPException(status_code=404, detail="Effectiveness metric not found")
    before = _metric_entry_out(row)
    measure = row.measure
    record_entity_changelog(
        db,
        entity_type="isms_effectiveness_metric",
        entity_id=row.id,
        action="deleted",
        before=before,
        after=None,
        user=user,
        request_method=request.method,
        request_path=request.url.path,
    )
    measure.updated_at = _utcnow()
    db.add(measure)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/v1/isms/meetings")
def create_meeting(
    payload: MeetingPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    if not payload.date:
        raise HTTPException(status_code=400, detail="date is required")
    row = IsmsMeeting(
        title=_clean_text(
            payload.title or "ISMS Meeting", max_len=256, required=True, label="title"
        ),
        date=payload.date,
        start_time=payload.start_time,
        end_time=payload.end_time,
        agenda_minutes_notes=_clean_minutes_html(payload.agenda_minutes_notes),
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    _replace_meeting_people(
        db, row, payload.attendee_user_ids or [], payload.apology_user_ids or []
    )
    _replace_meeting_links(db, row, payload.links or [])
    _apply_links(db, "meeting", row.id, fw, payload, user)
    after = _meeting_out(db, row, fw)
    _record(db, "meeting", row, "created", None, after, user, request)
    db.commit()
    db.refresh(row)
    return _meeting_out(db, row, fw)


@router.get("/v1/isms/meetings")
def list_meetings(
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    rows = (
        db.query(IsmsMeeting)
        .order_by(IsmsMeeting.date.desc(), IsmsMeeting.start_time.desc())
        .all()
    )
    return {"framework": fw, "items": [_meeting_out(db, r, fw) for r in rows]}


@router.get("/v1/isms/meetings/{meeting_id}")
def get_meeting(
    meeting_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsMeeting, meeting_id, "Meeting")
    return _meeting_out(db, row, fw)


@router.patch("/v1/isms/meetings/{meeting_id}")
def update_meeting(
    meeting_id: str,
    payload: MeetingPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsMeeting, meeting_id, "Meeting")
    before = _meeting_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    if "title" in fields:
        row.title = _clean_text(
            payload.title, max_len=256, required=True, label="title"
        )
    if "date" in fields and payload.date:
        row.date = payload.date
    if "start_time" in fields:
        row.start_time = payload.start_time
    if "end_time" in fields:
        row.end_time = payload.end_time
    if "agenda_minutes_notes" in fields:
        row.agenda_minutes_notes = _clean_minutes_html(payload.agenda_minutes_notes)
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    if "attendee_user_ids" in fields or "apology_user_ids" in fields:
        _replace_meeting_people(
            db, row, payload.attendee_user_ids or [], payload.apology_user_ids or []
        )
    if "links" in fields:
        _replace_meeting_links(db, row, payload.links or [])
    _apply_links(db, "meeting", row.id, fw, payload, user)
    after = _meeting_out(db, row, fw)
    _record(db, "meeting", row, "updated", before, after, user, request)
    db.commit()
    db.refresh(row)
    return _meeting_out(db, row, fw)


@router.delete("/v1/isms/meetings/{meeting_id}")
def delete_meeting(
    meeting_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsMeeting, meeting_id, "Meeting")
    before = _meeting_out(db, row, fw)
    _record(db, "meeting", row, "deleted", before, None, user, request)
    _delete_entity_links(db, "meeting", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/v1/isms/{entity_type}/{entity_id}/changelog")
def isms_entity_changelog(
    entity_type: str,
    entity_id: str,
    limit: int = 50,
    offset: int = 0,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    key = (entity_type or "").strip().lower().replace("-", "_")
    if key not in ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="Unknown ISMS entity type")
    _, changelog_type = ENTITY_TYPES[key]
    eid = _try_uuid(entity_id)
    if not eid:
        raise HTTPException(status_code=400, detail="entity_id must be a UUID")
    return list_entity_changelogs(
        db, entity_type=changelog_type, entity_id=eid, limit=limit, offset=offset
    )


def _reverse_refs_for(
    entity_side: str, entity_id: uuid.UUID, framework: str, db: Session
) -> dict[str, Any]:
    if entity_side == "control":
        links = (
            db.query(IsmsEntityControlLink)
            .filter(
                IsmsEntityControlLink.control_item_id == entity_id,
                IsmsEntityControlLink.framework_slug == framework,
            )
            .all()
        )
    else:
        links = (
            db.query(IsmsEntityClauseLink)
            .filter(
                IsmsEntityClauseLink.clause_id == entity_id,
                IsmsEntityClauseLink.framework_slug == framework,
            )
            .all()
        )
    grouped = {
        "objectives": [],
        "documents": [],
        "org_nodes": [],
        "assets": [],
        "effectiveness_measures": [],
        "meetings": [],
    }
    for link in links:
        et = link.entity_type
        if et not in ENTITY_TYPES:
            continue
        row = (
            db.query(ENTITY_TYPES[et][0])
            .filter(ENTITY_TYPES[et][0].id == link.entity_id)
            .one_or_none()
        )
        if not row:
            continue
        if et in {
            "application_configuration",
            "access_control_matrix",
            "effectiveness_metric",
        }:
            continue
        if entity_side == "clause" and et == "effectiveness_measure":
            # Effectiveness measures are control-scoped. Older builds allowed
            # direct clause links, but they should not surface on clause pages.
            continue
        key = {
            "objective": "objectives",
            "document": "documents",
            "org_node": "org_nodes",
            "asset": "assets",
            "effectiveness_measure": "effectiveness_measures",
            "meeting": "meetings",
        }[et]
        grouped[key].append(_entity_out(db, et, row, framework, include_links=False))
    grouped["total"] = sum(len(v) for v in grouped.values() if isinstance(v, list))
    return grouped


@router.get("/v1/controls/{control_id}/effectiveness-measures")
def control_effectiveness_measures(
    control_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    cid = _try_uuid(control_id)
    if not cid:
        raise HTTPException(status_code=400, detail="control_id must be a UUID")
    exists = (
        db.query(ControlItem.id)
        .filter(ControlItem.id == cid, ControlItem.framework_slug == fw)
        .first()
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Control not found")
    rows = (
        db.query(IsmsEffectivenessMeasure)
        .join(
            IsmsEntityControlLink,
            IsmsEntityControlLink.entity_id == IsmsEffectivenessMeasure.id,
        )
        .filter(
            IsmsEffectivenessMeasure.framework_slug == fw,
            IsmsEntityControlLink.entity_type == "effectiveness_measure",
            IsmsEntityControlLink.framework_slug == fw,
            IsmsEntityControlLink.control_item_id == cid,
        )
        .order_by(IsmsEffectivenessMeasure.updated_at.desc())
        .all()
    )
    return {
        "framework": fw,
        "control_id": str(cid),
        "items": [_effectiveness_measure_out(db, row, fw) for row in rows],
    }


@router.get("/v1/controls/{control_id}/isms")
def control_isms(
    control_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    cid = _try_uuid(control_id)
    if not cid:
        raise HTTPException(status_code=400, detail="control_id must be a UUID")
    return {"framework": fw, **_reverse_refs_for("control", cid, fw, db)}


@router.get("/v1/clauses/{clause_id}/isms")
def clause_isms(
    clause_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    cid = _try_uuid(clause_id)
    if not cid:
        raise HTTPException(status_code=400, detail="clause_id must be a UUID")
    return {"framework": fw, **_reverse_refs_for("clause", cid, fw, db)}


@router.get("/v1/me/isms-objectives")
def my_isms_objectives(
    framework: str = settings.default_framework_slug,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    rows = (
        db.query(IsmsObjective)
        .outerjoin(
            IsmsObjectiveResourceUser,
            IsmsObjectiveResourceUser.objective_id == IsmsObjective.id,
        )
        .filter(
            or_(
                IsmsObjective.owner_user_id == user.id,
                IsmsObjectiveResourceUser.user_id == user.id,
            )
        )
        .order_by(
            IsmsObjective.completion_target_date.asc().nullslast(),
            IsmsObjective.updated_at.desc(),
        )
        .all()
    )
    return {"framework": fw, "items": [_objective_out(db, r, fw) for r in rows]}


@router.get("/v1/me/isms-role")
def my_isms_role(user=Depends(require_authenticated), db: Session = Depends(get_db)):
    links = (
        db.query(IsmsOrgNodeUser)
        .join(IsmsOrgNode, IsmsOrgNode.id == IsmsOrgNodeUser.org_node_id)
        .filter(IsmsOrgNodeUser.user_id == user.id)
        .order_by(IsmsOrgNode.sort_order.asc(), IsmsOrgNode.name.asc())
        .all()
    )
    return {
        "items": [
            {
                "relationship_type": link.relationship_type,
                "org_node": _org_node_out(link.org_node, include_people=False),
                "path": _org_path(link.org_node),
            }
            for link in links
        ]
    }


def _org_path(node: IsmsOrgNode | None) -> list[dict[str, Any]]:
    out = []
    cur = node
    while cur:
        out.append({"id": str(cur.id), "name": cur.name, "node_type": cur.node_type})
        cur = getattr(cur, "parent", None)
    return list(reversed(out))


@router.get("/v1/me/isms-meetings")
def my_isms_meetings(
    framework: str = settings.default_framework_slug,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    attendee_links = (
        db.query(IsmsMeetingAttendee)
        .join(IsmsMeeting, IsmsMeeting.id == IsmsMeetingAttendee.meeting_id)
        .filter(IsmsMeetingAttendee.user_id == user.id)
        .order_by(
            IsmsMeeting.date.desc(),
            IsmsMeeting.start_time.desc().nullslast(),
            IsmsMeeting.title.asc(),
        )
        .all()
    )
    grouped: dict[uuid.UUID, dict[str, Any]] = {}
    for link in attendee_links:
        if not link.meeting:
            continue
        row = grouped.setdefault(
            link.meeting_id, {"meeting": link.meeting, "types": []}
        )
        if link.attendance_type not in row["types"]:
            row["types"].append(link.attendance_type)

    items = []
    label_by_type = {"attendee": "Attendee", "apology": "Apology"}
    for row in grouped.values():
        meeting = row["meeting"]
        types = list(row["types"] or [])
        out = _meeting_out(db, meeting, fw)
        out["my_attendance_types"] = types
        out["my_attendance_label"] = ", ".join(
            label_by_type.get(t, t.title()) for t in types
        )
        items.append(out)
    return {"framework": fw, "items": items}


@router.get("/v1/me/isms-assets")
def my_isms_assets(
    framework: str = settings.default_framework_slug,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    node_ids = [
        nid
        for (nid,) in db.query(IsmsOrgNodeUser.org_node_id)
        .filter(IsmsOrgNodeUser.user_id == user.id)
        .all()
    ]
    if not node_ids:
        return {"framework": fw, "items": []}
    rows = (
        db.query(RiskAsset)
        .filter(
            or_(
                RiskAsset.owner_org_node_id.in_(node_ids),
                RiskAsset.register_held_by_org_node_id.in_(node_ids),
            )
        )
        .order_by(func.lower(RiskAsset.name).asc())
        .all()
    )
    items = []
    node_id_set = {str(nid) for nid in node_ids}
    for row in rows:
        out = _asset_out(db, row, fw)
        owner_id = str(row.owner_org_node_id) if row.owner_org_node_id else None
        register_id = (
            str(row.register_held_by_org_node_id)
            if row.register_held_by_org_node_id
            else None
        )
        out["is_owner"] = owner_id in node_id_set
        out["is_register_holder"] = register_id in node_id_set
        items.append(out)
    return {"framework": fw, "items": items}
