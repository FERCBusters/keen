"""ISMS assets; see docs/maintainability-review.md for module boundaries."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.utils import try_uuid as _try_uuid
from app.core.config import settings
from app.db.models import (
    IsmsLicense,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
)
from app.db.session import get_db

from .access import (
    require_isms_manage,
    require_isms_read,
)
from .common import (
    _clean_framework,
    _clean_text,
    _list_response,
    _record,
    _utcnow,
)
from .mutations import (
    _apply_license_payload,
    _apply_links,
    _asset_category_or_create,
    _asset_name_from_payload,
    _asset_or_404,
    _asset_subcategory_or_create,
    _delete_entity_links,
    _license_by_name,
    _license_from_payload,
    _license_or_404,
)
from .schemas import (
    AssetCategoryPayload,
    AssetPayload,
    AssetSubcategoryPayload,
    LicensePayload,
)
from .serializers import (
    _asset_categories_out,
    _asset_out,
    _license_out,
)

router = APIRouter()


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
