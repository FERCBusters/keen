from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.api.utils import control_justification as _control_justification
from app.api.utils import control_upstream_url as _control_upstream_url
from app.api.utils import ref_sort_key as _ref_sort_key
from app.api.utils import try_uuid as _try_uuid
from app.core.config import settings
from app.db.models import (
    ControlItem,
    IsmsLicense,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    RiskControlLink,
    User,
)
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.security.permissions import has_permission
from app.services.risk_mitigator import analyse_risk_mitigation
from app.services.entity_changelog import (
    list_entity_changelogs,
    record_entity_changelog,
    risk_changelog_state,
)

router = APIRouter()

RISK_READ_PERMISSION = "risk.read"
RISK_MANAGE_PERMISSION = "risk.manage"
_RISK_TYPES = {"Confidentiality", "Integrity", "Availability"}
_RISK_TYPE_CANON = {x.lower(): x for x in _RISK_TYPES}


def _split_filter_values(value: str | None) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _uuid_filter_values(value: str | None, *, label: str) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for part in _split_filter_values(value):
        parsed = _try_uuid(part)
        if not parsed:
            raise HTTPException(
                status_code=400, detail=f"{label} must contain UUID values"
            )
        out.append(parsed)
    return out


class RiskCategoryPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class RiskSubcategoryPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    category_id: uuid.UUID | None = None


class RiskAssetPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
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


class RiskControlLinksPayload(BaseModel):
    controls: list[str] = Field(default_factory=list)


class RiskMitigatorAnalysePayload(BaseModel):
    issue: str = Field(..., min_length=1, max_length=12000)
    asset_name: str | None = Field(default=None, max_length=256)
    category_id: uuid.UUID | None = None
    category_name: str | None = Field(default=None, max_length=128)
    subcategory_id: uuid.UUID | None = None
    subcategory_name: str | None = Field(default=None, max_length=128)
    risk_types: list[str] | None = None
    risk_weights: dict[str, int] | None = None
    impact_flags: dict[str, bool] | None = None
    framework: str | None = Field(default=None, max_length=64)
    limit: int | None = Field(default=10, ge=1, le=25)


class RiskUpsertPayload(BaseModel):
    # asset is retained as a backwards-compatible alias for asset_name.
    asset: str | None = Field(default=None, max_length=256)
    asset_id: uuid.UUID | None = None
    asset_name: str | None = Field(default=None, max_length=256)
    category_id: uuid.UUID | None = None
    category_name: str | None = Field(default=None, max_length=128)
    subcategory_id: uuid.UUID | None = None
    subcategory_name: str | None = Field(default=None, max_length=128)
    risk_types: list[str] | None = None
    risk_owner_user_id: uuid.UUID | None = None
    risk_owner_username: str | None = Field(default=None, max_length=128)
    threat_summary: str | None = Field(default=None, max_length=12000)
    threat_score: int | None = None
    vulnerability_score: int | None = None
    impact_score: int | None = None
    residual_vulnerability_score: int | None = None
    residual_impact_score: int | None = None
    note: str | None = Field(default=None, max_length=20000)
    mitigator_context: dict[str, Any] | None = None
    framework: str | None = Field(default=None, max_length=64)
    controls: list[str] | None = None


def _utcnow() -> datetime:
    return datetime.utcnow()


def _can_read_risks(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return has_permission(db, user, RISK_MANAGE_PERMISSION) or has_permission(
        db, user, RISK_READ_PERMISSION
    )


def _can_manage_risks(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return has_permission(db, user, RISK_MANAGE_PERMISSION)


def require_risk_read(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not _can_read_risks(db, user):
        raise HTTPException(status_code=403, detail="risk.read permission required")
    return user


def require_risk_manage(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not _can_manage_risks(db, user):
        raise HTTPException(status_code=403, detail="risk.manage permission required")
    return user


def _is_risk_owner(user: User | None, risk: Risk | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    if risk is None or risk.risk_owner_user_id is None:
        return False
    return risk.risk_owner_user_id == user.id


def _can_read_specific_risk(db: Session, user: User | None, risk: Risk | None) -> bool:
    return _can_read_risks(db, user) or _is_risk_owner(user, risk)


def _require_risk_read_or_owner(db: Session, user: User, risk: Risk) -> None:
    if not _can_read_specific_risk(db, user, risk):
        raise HTTPException(
            status_code=403,
            detail="risk.read permission or risk ownership required",
        )


def _clean_text(
    raw: str | None, *, max_len: int, required: bool = False, label: str = "value"
) -> str:
    value = (raw or "").strip()
    if required and not value:
        raise HTTPException(status_code=400, detail=f"{label} is required")
    if len(value) > max_len:
        raise HTTPException(status_code=400, detail=f"{label} is too long")
    return value


def _clean_framework(raw: str | None) -> str:
    value = (raw or settings.default_framework_slug or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="framework is required")
    if len(value) > 64 or not re.match(r"^[A-Za-z0-9._:-]+$", value):
        raise HTTPException(status_code=400, detail="Invalid framework")
    return value


def _clean_score(raw: int | None, *, default: int = 1, label: str = "score") -> int:
    try:
        n = int(default if raw is None else raw)
    except Exception:
        raise HTTPException(status_code=400, detail=f"{label} must be a number")
    if n < 0 or n > 5:
        raise HTTPException(status_code=400, detail=f"{label} must be between 0 and 5")
    return n


def _clean_risk_types(raw: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        key = str(item or "").strip().lower()
        if not key:
            continue
        value = _RISK_TYPE_CANON.get(key)
        if not value:
            raise HTTPException(
                status_code=400,
                detail="risk_types must contain only Confidentiality, Integrity or Availability",
            )
        if value not in seen:
            seen.add(value)
            out.append(value)
    if not out:
        raise HTTPException(
            status_code=400, detail="At least one risk type is required"
        )
    return out


def _risk_or_404(db: Session, risk_id: str) -> Risk:
    rid = _try_uuid(risk_id)
    if not rid:
        raise HTTPException(status_code=400, detail="risk_id must be a UUID")
    row = db.query(Risk).filter(Risk.id == rid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Risk not found")
    return row


def _category_by_id(db: Session, category_id: uuid.UUID) -> RiskCategory:
    row = db.query(RiskCategory).filter(RiskCategory.id == category_id).one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail="Unknown asset category")
    return row


def _category_or_create(
    db: Session,
    *,
    category_id: uuid.UUID | None,
    category_name: str | None,
    existing: RiskCategory | None = None,
    required: bool = True,
) -> RiskCategory | None:
    if category_id:
        return _category_by_id(db, category_id)

    name = _clean_text(category_name, max_len=128, required=False, label="category")
    if name:
        row = (
            db.query(RiskCategory)
            .filter(func.lower(RiskCategory.name) == name.lower())
            .one_or_none()
        )
        if not row:
            row = RiskCategory(name=name, created_at=_utcnow(), updated_at=_utcnow())
            db.add(row)
            db.flush()
        return row

    if existing is not None:
        return existing
    if required:
        raise HTTPException(status_code=400, detail="asset category is required")
    return None


def _subcategory_by_id(
    db: Session, subcategory_id: uuid.UUID, *, category: RiskCategory | None = None
) -> RiskAssetSubcategory:
    q = db.query(RiskAssetSubcategory).filter(RiskAssetSubcategory.id == subcategory_id)
    if category is not None:
        q = q.filter(RiskAssetSubcategory.category_id == category.id)
    row = q.one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail="Unknown asset subcategory")
    return row


def _subcategory_or_create(
    db: Session,
    *,
    category: RiskCategory,
    subcategory_id: uuid.UUID | None,
    subcategory_name: str | None,
    existing: RiskAssetSubcategory | None = None,
    required: bool = True,
) -> RiskAssetSubcategory | None:
    if subcategory_id:
        return _subcategory_by_id(db, subcategory_id, category=category)

    name = _clean_text(
        subcategory_name, max_len=128, required=False, label="subcategory"
    )
    if name:
        row = (
            db.query(RiskAssetSubcategory)
            .filter(
                RiskAssetSubcategory.category_id == category.id,
                func.lower(RiskAssetSubcategory.name) == name.lower(),
            )
            .one_or_none()
        )
        if not row:
            row = RiskAssetSubcategory(
                category_id=category.id,
                name=name,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            db.add(row)
            db.flush()
        return row

    if existing is not None and existing.category_id == category.id:
        return existing
    if required:
        raise HTTPException(status_code=400, detail="asset subcategory is required")
    return None


def _asset_or_404(db: Session, asset_id: str | uuid.UUID) -> RiskAsset:
    aid = asset_id if isinstance(asset_id, uuid.UUID) else _try_uuid(str(asset_id))
    if not aid:
        raise HTTPException(status_code=400, detail="asset_id must be a UUID")
    row = db.query(RiskAsset).filter(RiskAsset.id == aid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Asset not found")
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


def _license_or_create_by_name(db: Session, name: str | None) -> IsmsLicense | None:
    cleaned = _clean_text(name, max_len=256)
    if not cleaned:
        return None
    row = _license_by_name(db, cleaned)
    if row:
        return row
    row = IsmsLicense(
        name=cleaned, description="", created_at=_utcnow(), updated_at=_utcnow()
    )
    db.add(row)
    db.flush()
    return row


def _license_from_payload(
    db: Session, payload: RiskUpsertPayload | RiskAssetPayload
) -> IsmsLicense | None:
    license_id = getattr(payload, "license_id", None)
    if license_id:
        return _license_or_404(db, license_id)
    license_name = getattr(payload, "license_name", None)
    if license_name:
        return _license_or_create_by_name(db, license_name)
    legacy_name = getattr(payload, "license", None)
    if legacy_name:
        return _license_or_create_by_name(db, legacy_name)
    return None


def _asset_name_from_payload(
    payload: RiskUpsertPayload | RiskAssetPayload,
) -> str | None:
    return (
        getattr(payload, "asset_name", None)
        or getattr(payload, "asset", None)
        or getattr(payload, "name", None)
    )


def _asset_or_create(
    db: Session,
    payload: RiskUpsertPayload | RiskAssetPayload,
    *,
    existing: RiskAsset | None = None,
    require_asset: bool = True,
) -> RiskAsset:
    fields = set(getattr(payload, "model_fields_set", set()) or set())
    asset_detail_fields = {
        "asset",
        "asset_id",
        "asset_name",
        "name",
        "category_id",
        "category_name",
        "subcategory_id",
        "subcategory_name",
        "license_id",
        "license_name",
        "license",
        "owner_org_node_id",
        "register_held_by_org_node_id",
        "description",
    }

    if existing is not None and not (fields & asset_detail_fields):
        return existing

    asset_id = getattr(payload, "asset_id", None)
    if asset_id and not (
        fields
        & {
            "asset",
            "asset_name",
            "name",
            "category_id",
            "category_name",
            "subcategory_id",
            "subcategory_name",
            "license_id",
            "license_name",
            "license",
        }
    ):
        return _asset_or_404(db, asset_id)

    base_asset = _asset_or_404(db, asset_id) if asset_id else existing
    name = _clean_text(
        _asset_name_from_payload(payload) or (base_asset.name if base_asset else None),
        max_len=256,
        required=require_asset,
        label="asset",
    )
    if not name:
        raise HTTPException(status_code=400, detail="asset is required")

    category = _category_or_create(
        db,
        category_id=getattr(payload, "category_id", None),
        category_name=getattr(payload, "category_name", None),
        existing=base_asset.category if base_asset else None,
        required=True,
    )
    assert category is not None
    subcategory = _subcategory_or_create(
        db,
        category=category,
        subcategory_id=getattr(payload, "subcategory_id", None),
        subcategory_name=getattr(payload, "subcategory_name", None),
        existing=base_asset.subcategory if base_asset else None,
        required=True,
    )
    assert subcategory is not None

    row = (
        db.query(RiskAsset)
        .filter(
            func.lower(RiskAsset.name) == name.lower(),
            RiskAsset.category_id == category.id,
            RiskAsset.subcategory_id == subcategory.id,
        )
        .one_or_none()
    )
    if row:
        return row

    row = RiskAsset(
        name=name,
        category_id=category.id,
        subcategory_id=subcategory.id,
        owner_org_node_id=getattr(payload, "owner_org_node_id", None),
        register_held_by_org_node_id=getattr(
            payload, "register_held_by_org_node_id", None
        ),
        description=_clean_text(getattr(payload, "description", None), max_len=20000),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    license_row = _license_from_payload(db, payload)
    row.license_id = license_row.id if license_row else None
    row.license = license_row.name if license_row else ""
    db.add(row)
    db.flush()
    return row


def _owner_or_none(
    db: Session, payload: RiskUpsertPayload, existing: Risk | None = None
) -> User | None:
    # PATCH semantics: if neither owner field was sent, keep existing owner.
    sent_fields = set(payload.model_fields_set or set())
    if existing is not None and not (
        {"risk_owner_user_id", "risk_owner_username"} & sent_fields
    ):
        return existing.owner

    if (
        payload.risk_owner_user_id is None
        and not (payload.risk_owner_username or "").strip()
    ):
        return None
    if payload.risk_owner_user_id is not None:
        owner = (
            db.query(User)
            .filter(User.id == payload.risk_owner_user_id, User.is_active.is_(True))
            .one_or_none()
        )
    else:
        owner = (
            db.query(User)
            .filter(User.username == (payload.risk_owner_username or "").strip())
            .filter(User.is_active.is_(True))
            .one_or_none()
        )
    if not owner:
        raise HTTPException(status_code=400, detail="Unknown or inactive risk owner")
    return owner


def _control_or_404(db: Session, framework: str, value: str) -> ControlItem:
    val = (value or "").strip()
    cid = _try_uuid(val)
    q = db.query(ControlItem).filter(ControlItem.framework_slug == framework)
    if cid:
        row = q.filter(ControlItem.id == cid).one_or_none()
    else:
        row = q.filter(ControlItem.ref == val).one_or_none()
    if not row:
        raise HTTPException(
            status_code=400, detail=f"Unknown control for {framework}: {val}"
        )
    return row


def _dedupe_controls(values: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        v = (raw or "").strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    if len(out) > 1000:
        raise HTTPException(status_code=400, detail="Too many controls")
    return out


def _replace_control_links(
    db: Session, risk: Risk, *, framework: str, control_values: list[str] | None
) -> list[ControlItem]:
    vals = _dedupe_controls(control_values)
    controls = [_control_or_404(db, framework, v) for v in vals]
    db.query(RiskControlLink).filter(
        RiskControlLink.risk_id == risk.id,
        RiskControlLink.framework_slug == framework,
    ).delete()
    now = _utcnow()
    for control in controls:
        db.add(
            RiskControlLink(
                risk_id=risk.id,
                framework_slug=framework,
                control_item_id=control.id,
                created_at=now,
            )
        )
    return controls


def _score_class(score: int | None) -> str:
    n = int(score or 0)
    if n < 5:
        return "low"
    if n <= 12:
        return "medium"
    return "high"


def _control_out(control: ControlItem) -> dict[str, Any]:
    return {
        "id": str(control.id),
        "framework": control.framework_slug,
        "type": control.type,
        "ref": control.ref,
        "title": control.title,
        "in_scope": control.in_scope,
        "justification": _control_justification(control),
        "upstream_url": _control_upstream_url(control),
    }


def _category_out(category: RiskCategory | None) -> dict[str, Any]:
    return {
        "id": str(category.id) if category else None,
        "name": category.name if category else None,
    }


def _subcategory_out(subcategory: RiskAssetSubcategory | None) -> dict[str, Any]:
    return {
        "id": str(subcategory.id) if subcategory else None,
        "name": subcategory.name if subcategory else None,
        "category_id": str(subcategory.category_id) if subcategory else None,
    }


def _org_node_summary(node: Any | None) -> dict[str, Any] | None:
    if not node:
        return None
    return {
        "id": str(node.id),
        "name": node.name,
        "node_type": getattr(node, "node_type", None),
    }


def _asset_out(asset: RiskAsset | None) -> dict[str, Any]:
    return {
        "id": str(asset.id) if asset else None,
        "name": asset.name if asset else None,
        "asset": asset.name if asset else None,
        "category": _category_out(asset.category if asset else None),
        "subcategory": _subcategory_out(asset.subcategory if asset else None),
        "license": (
            asset.license_entity.name
            if asset and asset.license_entity
            else (asset.license if asset else "")
        ),
        "license_id": str(asset.license_id) if asset and asset.license_id else None,
        "license_name": (
            asset.license_entity.name
            if asset and asset.license_entity
            else (asset.license if asset else "")
        ),
        "license_entity": (
            {"id": str(asset.license_entity.id), "name": asset.license_entity.name}
            if asset and asset.license_entity
            else None
        ),
        "owner_org_node_id": (
            str(asset.owner_org_node_id) if asset and asset.owner_org_node_id else None
        ),
        "owner_org_node": _org_node_summary(asset.owner_org_node if asset else None),
        "register_held_by_org_node_id": (
            str(asset.register_held_by_org_node_id)
            if asset and asset.register_held_by_org_node_id
            else None
        ),
        "register_held_by_org_node": _org_node_summary(
            asset.register_held_by_org_node if asset else None
        ),
        "description": asset.description if asset else "",
        "created_at": (
            asset.created_at.isoformat() if asset and asset.created_at else None
        ),
        "updated_at": (
            asset.updated_at.isoformat() if asset and asset.updated_at else None
        ),
    }


def _risk_controls(
    db: Session, risk_id: uuid.UUID, framework: str
) -> list[ControlItem]:
    rows = (
        db.query(ControlItem)
        .join(RiskControlLink, RiskControlLink.control_item_id == ControlItem.id)
        .filter(
            RiskControlLink.risk_id == risk_id,
            RiskControlLink.framework_slug == framework,
            ControlItem.framework_slug == framework,
        )
        .all()
    )
    rows.sort(key=lambda c: (c.type, _ref_sort_key(c.ref)))
    return rows


def _risk_out(
    db: Session,
    risk: Risk,
    *,
    framework: str | None = None,
    include_controls: bool = False,
) -> dict[str, Any]:
    fw = (
        _clean_framework(framework)
        if framework is not None
        else settings.default_framework_slug
    )
    controls = _risk_controls(db, risk.id, fw) if include_controls else []
    if include_controls:
        control_count = len(controls)
    else:
        control_count = int(
            db.query(func.count(RiskControlLink.control_item_id))
            .filter(
                RiskControlLink.risk_id == risk.id,
                RiskControlLink.framework_slug == fw,
            )
            .scalar()
            or 0
        )
    asset = risk.asset
    category = asset.category if asset else None
    subcategory = asset.subcategory if asset else None
    return {
        "id": str(risk.id),
        # Keep asset/category as convenient flattened properties for existing pages.
        "asset": asset.name if asset else "",
        "asset_id": str(asset.id) if asset else None,
        "asset_entity": _asset_out(asset),
        "category": _category_out(category),
        "subcategory": _subcategory_out(subcategory),
        "risk_types": list(risk.risk_types or []),
        "risk_owner": {
            "id": str(risk.owner.id) if risk.owner else None,
            "username": risk.owner.username if risk.owner else None,
        },
        "threat_summary": risk.threat_summary or "",
        "threat_score": int(risk.threat_score or 0),
        "vulnerability_score": int(risk.vulnerability_score or 0),
        "impact_score": int(risk.impact_score or 0),
        "risk_score": int(risk.risk_score or 0),
        "risk_score_class": _score_class(risk.risk_score),
        "residual_vulnerability_score": int(risk.residual_vulnerability_score or 0),
        "residual_impact_score": int(risk.residual_impact_score or 0),
        "residual_risk_score": int(risk.residual_risk_score or 0),
        "residual_risk_score_class": _score_class(risk.residual_risk_score),
        "note": risk.note or "",
        "mitigator_context": risk.mitigator_context or {},
        "created_by": getattr(risk.created_by, "username", None),
        "created_at": risk.created_at.isoformat() if risk.created_at else None,
        "updated_at": risk.updated_at.isoformat() if risk.updated_at else None,
        "framework": fw,
        "control_count": control_count,
        "controls": [_control_out(c) for c in controls] if include_controls else None,
    }


def _apply_payload(
    db: Session, risk: Risk, payload: RiskUpsertPayload, *, is_create: bool
) -> None:
    fields = set(payload.model_fields_set or set())

    asset_fields = {
        "asset",
        "asset_id",
        "asset_name",
        "category_id",
        "category_name",
        "subcategory_id",
        "subcategory_name",
    }
    if is_create or (fields & asset_fields):
        risk.asset = _asset_or_create(
            db,
            payload,
            existing=None if is_create else risk.asset,
            require_asset=True,
        )

    if is_create or "risk_types" in fields:
        risk.risk_types = _clean_risk_types(payload.risk_types)

    if is_create or "risk_owner_user_id" in fields or "risk_owner_username" in fields:
        risk.owner = _owner_or_none(db, payload, existing=None if is_create else risk)

    if is_create or "threat_summary" in fields:
        risk.threat_summary = _clean_text(
            payload.threat_summary,
            max_len=12000,
            required=False,
            label="threat_summary",
        )

    if is_create or "threat_score" in fields:
        risk.threat_score = _clean_score(payload.threat_score, label="threat_score")
    if is_create or "vulnerability_score" in fields:
        risk.vulnerability_score = _clean_score(
            payload.vulnerability_score, label="vulnerability_score"
        )
    if is_create or "impact_score" in fields:
        risk.impact_score = _clean_score(payload.impact_score, label="impact_score")

    if is_create or "residual_vulnerability_score" in fields:
        risk.residual_vulnerability_score = _clean_score(
            payload.residual_vulnerability_score,
            label="residual_vulnerability_score",
        )
    if is_create or "residual_impact_score" in fields:
        risk.residual_impact_score = _clean_score(
            payload.residual_impact_score, label="residual_impact_score"
        )

    if is_create or "note" in fields:
        risk.note = _clean_text(
            payload.note, max_len=20000, required=False, label="note"
        )

    if is_create or "mitigator_context" in fields:
        context = (
            payload.mitigator_context
            if isinstance(payload.mitigator_context, dict)
            else {}
        )
        # Keep this bounded because it is surfaced in changelogs/admin review and should
        # remain questionnaire metadata, not a generic document store.
        if len(str(context)) > 50000:
            raise HTTPException(
                status_code=400, detail="mitigator_context is too large"
            )
        risk.mitigator_context = context

    risk.risk_score = (
        int(risk.threat_score) * int(risk.vulnerability_score) * int(risk.impact_score)
    )
    risk.residual_risk_score = int(risk.residual_vulnerability_score) * int(
        risk.residual_impact_score
    )
    risk.updated_at = _utcnow()


@router.get("/v1/risks/categories")
def list_risk_categories(
    user=Depends(require_risk_read), db: Session = Depends(get_db)
):
    rows = db.query(RiskCategory).order_by(func.lower(RiskCategory.name).asc()).all()
    return {
        "items": [
            {
                "id": str(c.id),
                "name": c.name,
                "subcategories": [
                    _subcategory_out(sc)
                    for sc in sorted(
                        c.asset_subcategories, key=lambda x: x.name.lower()
                    )
                ],
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in rows
        ]
    }


@router.post("/v1/risks/categories")
def create_risk_category(
    payload: RiskCategoryPayload,
    user=Depends(require_risk_manage),
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
    # Make the category immediately usable for assets.
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


@router.patch("/v1/risks/categories/{category_id}")
def update_risk_category(
    category_id: str,
    payload: RiskCategoryPayload,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    cid = _try_uuid(category_id)
    if not cid:
        raise HTTPException(status_code=400, detail="category_id must be a UUID")
    row = db.query(RiskCategory).filter(RiskCategory.id == cid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Asset category not found")
    name = _clean_text(payload.name, max_len=128, required=True, label="name")
    conflict = (
        db.query(RiskCategory)
        .filter(
            func.lower(RiskCategory.name) == name.lower(), RiskCategory.id != row.id
        )
        .one_or_none()
    )
    if conflict:
        raise HTTPException(status_code=400, detail="Asset category already exists")
    row.name = name
    row.updated_at = _utcnow()
    db.add(row)
    db.commit()
    return {"id": str(row.id), "name": row.name}


@router.delete("/v1/risks/categories/{category_id}")
def delete_risk_category(
    category_id: str,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    cid = _try_uuid(category_id)
    if not cid:
        raise HTTPException(status_code=400, detail="category_id must be a UUID")
    row = db.query(RiskCategory).filter(RiskCategory.id == cid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Asset category not found")
    in_use = db.query(RiskAsset.id).filter(RiskAsset.category_id == row.id).first()
    if in_use:
        raise HTTPException(
            status_code=400, detail="Cannot delete a category used by assets"
        )
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/v1/risks/subcategories")
def list_risk_subcategories(
    category_id: str = "",
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    qry = db.query(RiskAssetSubcategory).join(
        RiskCategory, RiskCategory.id == RiskAssetSubcategory.category_id
    )
    cid = _try_uuid(category_id) if category_id else None
    if category_id and not cid:
        raise HTTPException(status_code=400, detail="category_id must be a UUID")
    if cid:
        qry = qry.filter(RiskAssetSubcategory.category_id == cid)
    rows = qry.order_by(
        func.lower(RiskCategory.name), func.lower(RiskAssetSubcategory.name)
    ).all()
    return {
        "items": [
            {
                **_subcategory_out(sc),
                "category": _category_out(sc.category),
                "created_at": sc.created_at.isoformat() if sc.created_at else None,
                "updated_at": sc.updated_at.isoformat() if sc.updated_at else None,
            }
            for sc in rows
        ]
    }


@router.post("/v1/risks/subcategories")
def create_risk_subcategory(
    payload: RiskSubcategoryPayload,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    category = _category_or_create(
        db,
        category_id=payload.category_id,
        category_name=None,
        required=True,
    )
    assert category is not None
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


@router.patch("/v1/risks/subcategories/{subcategory_id}")
def update_risk_subcategory(
    subcategory_id: str,
    payload: RiskSubcategoryPayload,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    sid = _try_uuid(subcategory_id)
    if not sid:
        raise HTTPException(status_code=400, detail="subcategory_id must be a UUID")
    row = _subcategory_by_id(db, sid)
    category = row.category
    if payload.category_id and payload.category_id != row.category_id:
        category = _category_by_id(db, payload.category_id)
    name = _clean_text(payload.name, max_len=128, required=True, label="name")
    conflict = (
        db.query(RiskAssetSubcategory)
        .filter(
            RiskAssetSubcategory.category_id == category.id,
            func.lower(RiskAssetSubcategory.name) == name.lower(),
            RiskAssetSubcategory.id != row.id,
        )
        .one_or_none()
    )
    if conflict:
        raise HTTPException(status_code=400, detail="Asset subcategory already exists")
    row.category_id = category.id
    row.name = name
    row.updated_at = _utcnow()
    db.add(row)
    db.commit()
    return {"id": str(row.id), "name": row.name, "category_id": str(row.category_id)}


@router.delete("/v1/risks/subcategories/{subcategory_id}")
def delete_risk_subcategory(
    subcategory_id: str,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    sid = _try_uuid(subcategory_id)
    if not sid:
        raise HTTPException(status_code=400, detail="subcategory_id must be a UUID")
    row = (
        db.query(RiskAssetSubcategory)
        .filter(RiskAssetSubcategory.id == sid)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Asset subcategory not found")
    in_use = db.query(RiskAsset.id).filter(RiskAsset.subcategory_id == row.id).first()
    if in_use:
        raise HTTPException(
            status_code=400, detail="Cannot delete a subcategory used by assets"
        )
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/v1/risks/assets")
def list_risk_assets(
    q: str = "",
    category_id: str = "",
    subcategory_id: str = "",
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    qry = (
        db.query(RiskAsset)
        .join(RiskCategory, RiskCategory.id == RiskAsset.category_id)
        .join(RiskAssetSubcategory, RiskAssetSubcategory.id == RiskAsset.subcategory_id)
        .outerjoin(IsmsLicense, IsmsLicense.id == RiskAsset.license_id)
    )
    sq = (q or "").strip()
    if sq:
        like = f"%{sq.lower()}%"
        qry = qry.filter(
            or_(
                func.lower(RiskAsset.name).like(like),
                func.lower(RiskAsset.license).like(like),
                func.lower(IsmsLicense.name).like(like),
                func.lower(RiskCategory.name).like(like),
                func.lower(RiskAssetSubcategory.name).like(like),
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
    rows = qry.order_by(func.lower(RiskAsset.name)).all()
    return {"items": [_asset_out(a) for a in rows]}


@router.post("/v1/risks/assets")
def create_risk_asset(
    payload: RiskAssetPayload,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    row = _asset_or_create(db, payload, require_asset=True)
    db.commit()
    db.refresh(row)
    return _asset_out(row)


@router.patch("/v1/risks/assets/{asset_id}")
def update_risk_asset(
    asset_id: str,
    payload: RiskAssetPayload,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    row = _asset_or_404(db, asset_id)
    category = _category_or_create(
        db,
        category_id=payload.category_id,
        category_name=payload.category_name,
        existing=row.category,
        required=True,
    )
    assert category is not None
    subcategory = _subcategory_or_create(
        db,
        category=category,
        subcategory_id=payload.subcategory_id,
        subcategory_name=payload.subcategory_name,
        existing=row.subcategory,
        required=True,
    )
    assert subcategory is not None
    name = _clean_text(payload.name, max_len=256, required=True, label="asset")
    conflict = (
        db.query(RiskAsset)
        .filter(
            func.lower(RiskAsset.name) == name.lower(),
            RiskAsset.category_id == category.id,
            RiskAsset.subcategory_id == subcategory.id,
            RiskAsset.id != row.id,
        )
        .one_or_none()
    )
    if conflict:
        raise HTTPException(status_code=400, detail="Asset already exists")
    row.name = name
    row.category_id = category.id
    row.subcategory_id = subcategory.id
    fields = set(payload.model_fields_set or set())
    if {"license_id", "license_name", "license"} & fields:
        license_row = _license_from_payload(db, payload)
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
    db.commit()
    return _asset_out(row)


@router.delete("/v1/risks/assets/{asset_id}")
def delete_risk_asset(
    asset_id: str,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    row = _asset_or_404(db, asset_id)
    in_use = db.query(Risk.id).filter(Risk.asset_id == row.id).first()
    if in_use:
        raise HTTPException(
            status_code=400, detail="Cannot delete an asset used by risks"
        )
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/v1/risks/users")
def list_risk_owner_users(
    user=Depends(require_risk_manage), db: Session = Depends(get_db)
):
    rows = (
        db.query(User)
        .filter(User.is_active.is_(True))
        .order_by(User.username.asc())
        .all()
    )
    return {"items": [{"id": str(u.id), "username": u.username} for u in rows]}


@router.get("/v1/me/risks")
def list_my_owned_risks(
    framework: str = settings.default_framework_slug,
    limit: int = 100,
    offset: int = 0,
    user: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    """List risks where the current user is explicitly set as the owner."""

    fw = _clean_framework(framework)
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))

    qry = (
        db.query(Risk)
        .join(RiskAsset, RiskAsset.id == Risk.asset_id)
        .filter(Risk.risk_owner_user_id == user.id)
    )

    total = int(qry.count() or 0)
    rows = (
        qry.order_by(
            Risk.risk_score.desc(),
            Risk.updated_at.desc(),
            RiskAsset.name.asc(),
            Risk.threat_summary.asc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "framework": fw,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_risk_out(db, r, framework=fw, include_controls=False) for r in rows],
    }


@router.get("/v1/risks")
def list_risks(
    framework: str = settings.default_framework_slug,
    q: str = "",
    category_id: str = "",
    subcategory_id: str = "",
    risk_type: str = "",
    owner_user_id: str = "",
    limit: int = 200,
    offset: int = 0,
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))

    qry = (
        db.query(Risk)
        .join(RiskAsset, RiskAsset.id == Risk.asset_id)
        .join(RiskCategory, RiskCategory.id == RiskAsset.category_id)
        .join(RiskAssetSubcategory, RiskAssetSubcategory.id == RiskAsset.subcategory_id)
    )
    sq = (q or "").strip()
    if sq:
        like = f"%{sq.lower()}%"
        qry = qry.filter(
            or_(
                func.lower(RiskAsset.name).like(like),
                func.lower(Risk.threat_summary).like(like),
                func.lower(Risk.note).like(like),
                func.lower(RiskCategory.name).like(like),
                func.lower(RiskAssetSubcategory.name).like(like),
            )
        )
    cids = _uuid_filter_values(category_id, label="category_id")
    if cids:
        qry = qry.filter(RiskAsset.category_id.in_(cids))
    sids = _uuid_filter_values(subcategory_id, label="subcategory_id")
    if sids:
        qry = qry.filter(RiskAsset.subcategory_id.in_(sids))
    risk_types = []
    for rt in _split_filter_values(risk_type):
        canon = _RISK_TYPE_CANON.get(rt.lower())
        if not canon:
            raise HTTPException(status_code=400, detail="Invalid risk_type")
        risk_types.append(canon)
    if risk_types:
        qry = qry.filter(
            or_(*(Risk.risk_types.contains([canon]) for canon in set(risk_types)))
        )
    oid = _try_uuid(owner_user_id) if owner_user_id else None
    if owner_user_id and not oid:
        raise HTTPException(status_code=400, detail="owner_user_id must be a UUID")
    if oid:
        qry = qry.filter(Risk.risk_owner_user_id == oid)

    total = int(qry.count() or 0)
    rows = (
        qry.order_by(
            Risk.risk_score.desc(),
            RiskAsset.name.asc(),
            Risk.threat_summary.asc(),
            Risk.updated_at.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "framework": fw,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_risk_out(db, r, framework=fw, include_controls=False) for r in rows],
    }


@router.get("/v1/risks/controls-without-risks")
def list_controls_without_risks(
    framework: str = settings.default_framework_slug,
    q: str = "",
    limit: int = 500,
    offset: int = 0,
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    """List framework controls that are not associated with any risk scenario."""

    fw = _clean_framework(framework)
    limit = max(1, min(int(limit or 500), 5000))
    offset = max(0, int(offset or 0))

    qry = (
        db.query(ControlItem)
        .outerjoin(
            RiskControlLink,
            and_(
                RiskControlLink.control_item_id == ControlItem.id,
                RiskControlLink.framework_slug == fw,
            ),
        )
        .filter(
            ControlItem.framework_slug == fw,
            ControlItem.type != "clause",
            RiskControlLink.control_item_id.is_(None),
        )
    )

    sq = (q or "").strip()
    if sq:
        like = f"%{sq.lower()}%"
        qry = qry.filter(
            or_(
                func.lower(ControlItem.ref).like(like),
                func.lower(ControlItem.title).like(like),
                func.lower(ControlItem.type).like(like),
            )
        )

    items = [_control_out(c) for c in qry.all()]
    items.sort(
        key=lambda it: (
            it.get("type") or "",
            _ref_sort_key(it.get("ref") or ""),
        )
    )
    total = len(items)

    return {
        "framework": fw,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items[offset : offset + limit],
    }


@router.post("/v1/risks/mitigator/analyse")
def analyse_mitigator_risk(
    payload: RiskMitigatorAnalysePayload,
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(payload.framework)
    # Reuse existing validation helpers for framework/category/subcategory shape where
    # possible, but do not create assets/categories merely to run an analysis.
    category = None
    if payload.category_id:
        category = _category_by_id(db, payload.category_id)
    if payload.subcategory_id:
        _subcategory_by_id(db, payload.subcategory_id, category=category)
    risk_types = None
    if payload.risk_types:
        risk_types = _clean_risk_types(payload.risk_types)
    return analyse_risk_mitigation(
        db,
        framework=fw,
        issue=_clean_text(payload.issue, max_len=12000, required=True, label="issue"),
        asset_name=_clean_text(
            payload.asset_name, max_len=256, required=False, label="asset_name"
        ),
        category_id=payload.category_id,
        category_name=_clean_text(
            payload.category_name, max_len=128, required=False, label="category_name"
        ),
        subcategory_id=payload.subcategory_id,
        subcategory_name=_clean_text(
            payload.subcategory_name,
            max_len=128,
            required=False,
            label="subcategory_name",
        ),
        risk_types=risk_types,
        risk_weights=payload.risk_weights or {},
        impact_flags=payload.impact_flags or {},
        limit=payload.limit or 10,
    )


@router.post("/v1/risks")
def create_risk(
    payload: RiskUpsertPayload,
    request: Request,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    now = _utcnow()
    risk = Risk(created_at=now, updated_at=now, created_by_user_id=user.id)
    _apply_payload(db, risk, payload, is_create=True)
    db.add(risk)
    db.flush()
    fw = _clean_framework(payload.framework)
    if payload.controls is not None:
        _replace_control_links(db, risk, framework=fw, control_values=payload.controls)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="risk",
        entity_id=risk.id,
        action="created",
        before=None,
        after=risk_changelog_state(db, risk.id, framework=fw),
        user=user,
        request_method="POST",
        request_path="/v1/risks",
    )
    db.commit()
    db.refresh(risk)
    return _risk_out(db, risk, framework=fw, include_controls=True)


@router.get("/v1/risks/{risk_id}")
def get_risk(
    risk_id: str,
    framework: str = settings.default_framework_slug,
    user: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    risk = _risk_or_404(db, risk_id)
    _require_risk_read_or_owner(db, user, risk)
    return _risk_out(db, risk, framework=fw, include_controls=True)


@router.get("/v1/risks/{risk_id}/changelog")
def risk_changelog(
    risk_id: str,
    framework: str = settings.default_framework_slug,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    risk = _risk_or_404(db, risk_id)
    _require_risk_read_or_owner(db, user, risk)
    return list_entity_changelogs(
        db, entity_type="risk", entity_id=risk.id, limit=limit, offset=offset
    )


@router.patch("/v1/risks/{risk_id}")
def update_risk(
    risk_id: str,
    payload: RiskUpsertPayload,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    risk = _risk_or_404(db, risk_id)
    fw = _clean_framework(payload.framework)
    before_risk = risk_changelog_state(db, risk.id, framework=fw)
    _apply_payload(db, risk, payload, is_create=False)
    if payload.controls is not None:
        _replace_control_links(db, risk, framework=fw, control_values=payload.controls)
    db.add(risk)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="risk",
        entity_id=risk.id,
        action="updated",
        before=before_risk,
        after=risk_changelog_state(db, risk.id, framework=fw),
        user=user,
        request_method="PATCH",
        request_path=f"/v1/risks/{risk_id}",
    )
    db.commit()
    db.refresh(risk)
    return _risk_out(db, risk, framework=fw, include_controls=True)


@router.delete("/v1/risks/{risk_id}")
def delete_risk(
    risk_id: str,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    risk = _risk_or_404(db, risk_id)
    before_risk = risk_changelog_state(db, risk.id, framework=None)
    record_entity_changelog(
        db,
        entity_type="risk",
        entity_id=risk.id,
        action="deleted",
        before=before_risk,
        after=None,
        user=user,
        request_method="DELETE",
        request_path=f"/v1/risks/{risk_id}",
    )
    db.delete(risk)
    db.commit()
    return {"ok": True}


@router.get("/v1/risks/{risk_id}/controls")
def get_risk_controls(
    risk_id: str,
    framework: str = settings.default_framework_slug,
    user: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    risk = _risk_or_404(db, risk_id)
    _require_risk_read_or_owner(db, user, risk)
    controls = _risk_controls(db, risk.id, fw)
    return {
        "risk_id": str(risk.id),
        "framework": fw,
        "items": [_control_out(c) for c in controls],
    }


@router.patch("/v1/risks/{risk_id}/controls")
def update_risk_controls(
    risk_id: str,
    payload: RiskControlLinksPayload,
    framework: str = settings.default_framework_slug,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    risk = _risk_or_404(db, risk_id)
    before_risk = risk_changelog_state(db, risk.id, framework=fw)
    controls = _replace_control_links(
        db, risk, framework=fw, control_values=payload.controls
    )
    risk.updated_at = _utcnow()
    db.add(risk)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="risk",
        entity_id=risk.id,
        action="updated",
        before=before_risk,
        after=risk_changelog_state(db, risk.id, framework=fw),
        user=user,
        request_method="PATCH",
        request_path=f"/v1/risks/{risk_id}/controls",
    )
    db.commit()
    return {
        "ok": True,
        "risk_id": str(risk.id),
        "framework": fw,
        "controls": [_control_out(c) for c in controls],
    }


@router.get("/v1/controls/{control_id}/risks")
def control_risks(
    control_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    cid = _try_uuid(control_id)
    if not cid:
        raise HTTPException(status_code=400, detail="control_id must be a UUID")
    control = (
        db.query(ControlItem)
        .filter(ControlItem.id == cid, ControlItem.framework_slug == fw)
        .one_or_none()
    )
    if not control:
        raise HTTPException(status_code=404, detail="Control not found")
    rows = (
        db.query(Risk)
        .join(RiskControlLink, RiskControlLink.risk_id == Risk.id)
        .join(RiskAsset, RiskAsset.id == Risk.asset_id)
        .filter(
            RiskControlLink.control_item_id == control.id,
            RiskControlLink.framework_slug == fw,
        )
        .order_by(
            Risk.risk_score.desc(), RiskAsset.name.asc(), Risk.threat_summary.asc()
        )
        .all()
    )
    return {
        "control": _control_out(control),
        "framework": fw,
        "items": [_risk_out(db, r, framework=fw, include_controls=False) for r in rows],
    }
