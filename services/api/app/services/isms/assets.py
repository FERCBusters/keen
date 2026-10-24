"""ISMS assets domain operations."""

from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.references import try_uuid as _try_uuid
from app.db.models import (
    IsmsLicense,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    User,
)
from app.services.context import ChangeContext
from app.services.errors import DomainError
from app.services.frameworks import framework_parameter
from app.services.text import clean_text

from .common import _list_response, _record, _utcnow
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
    org_node_or_400,
)
from .schemas import (
    AssetCategoryPayload,
    AssetPayload,
    AssetSubcategoryPayload,
    LicensePayload,
)
from .serializers import _asset_categories_out, _asset_out, _license_out


def list_asset_categories(*, user: User, db: Session):
    return {"items": _asset_categories_out(db)}


def create_asset_category(*, payload: AssetCategoryPayload, user: User, db: Session):
    name = clean_text(payload.name, max_len=128, required=True, label="name")
    existing = (
        db.query(RiskCategory)
        .filter(func.lower(RiskCategory.name) == name.lower())
        .one_or_none()
    )
    if existing:
        raise DomainError(status_code=400, detail="Asset category already exists")
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


def create_asset_subcategory(
    *, payload: AssetSubcategoryPayload, user: User, db: Session
):
    category = _asset_category_or_create(
        db, category_id=payload.category_id, category_name=payload.category_name
    )
    name = clean_text(payload.name, max_len=128, required=True, label="name")
    existing = (
        db.query(RiskAssetSubcategory)
        .filter(
            RiskAssetSubcategory.category_id == category.id,
            func.lower(RiskAssetSubcategory.name) == name.lower(),
        )
        .one_or_none()
    )
    if existing:
        raise DomainError(status_code=400, detail="Asset subcategory already exists")
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


def list_licenses(
    *, q: str, framework: str, limit: int, offset: int, user: User, db: Session
):
    fw = framework_parameter(framework)
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


def create_license(
    *,
    payload: LicensePayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    framework_parameter(framework)
    name = clean_text(payload.name, max_len=256, required=True, label="license")
    existing = _license_by_name(db, name)
    if existing:
        raise DomainError(status_code=400, detail="License already exists")
    row = IsmsLicense(
        name=name,
        description=clean_text(payload.description, max_len=12000),
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    after = _license_out(row, include_asset_count=True)
    _record(db, "license", row, "created", None, after, user, context)
    db.commit()
    db.refresh(row)
    return _license_out(row, include_asset_count=True)


def update_license(
    *,
    license_id: str,
    payload: LicensePayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    framework_parameter(framework)
    row = _license_or_404(db, license_id)
    before = _license_out(row, include_asset_count=True)
    new_name = clean_text(payload.name, max_len=256, required=True, label="license")
    conflict = (
        db.query(IsmsLicense)
        .filter(
            func.lower(IsmsLicense.name) == new_name.lower(), IsmsLicense.id != row.id
        )
        .one_or_none()
    )
    if conflict:
        raise DomainError(status_code=400, detail="License already exists")
    _apply_license_payload(row, payload)
    # Register searches match each asset's stored licence name.
    db.query(RiskAsset).filter(RiskAsset.license_id == row.id).update(
        {RiskAsset.license: row.name, RiskAsset.updated_at: _utcnow()},
        synchronize_session=False,
    )
    db.add(row)
    db.flush()
    after = _license_out(row, include_asset_count=True)
    _record(db, "license", row, "updated", before, after, user, context)
    db.commit()
    db.refresh(row)
    return _license_out(row, include_asset_count=True)


def delete_license(
    *, license_id: str, context: ChangeContext, framework: str, user: User, db: Session
):
    framework_parameter(framework)
    row = _license_or_404(db, license_id)
    in_use = db.query(RiskAsset.id).filter(RiskAsset.license_id == row.id).first()
    if in_use:
        raise DomainError(
            status_code=400,
            detail="Cannot delete a license that is related to one or more assets",
        )
    before = _license_out(row, include_asset_count=True)
    _record(db, "license", row, "deleted", before, None, user, context)
    db.delete(row)
    db.commit()
    return {"ok": True}


def create_asset(
    *,
    payload: AssetPayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = framework_parameter(framework)
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
        owner_org_node_id=org_node_or_400(db, payload.owner_org_node_id),
        register_held_by_org_node_id=org_node_or_400(
            db, payload.register_held_by_org_node_id
        ),
        description=clean_text(payload.description, max_len=20000),
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
        raise DomainError(status_code=400, detail="Asset already exists")
    license_row = _license_from_payload(db, payload, user)
    row.license_id = license_row.id if license_row else None
    row.license = license_row.name if license_row else ""
    db.add(row)
    db.flush()
    _apply_links(db, "asset", row.id, fw, payload, user)
    after = _asset_out(db, row, fw)
    _record(db, "asset", row, "created", None, after, user, context)
    db.commit()
    db.refresh(row)
    return _asset_out(db, row, fw)


def list_assets(
    *,
    q: str,
    framework: str,
    category_id: str,
    subcategory_id: str,
    limit: int,
    offset: int,
    user: User,
    db: Session,
):
    fw = framework_parameter(framework)
    limit = max(1, min(int(limit or 500), 1000))
    offset = max(0, int(offset or 0))
    # Each join names its condition: a subcategory also joins to the category,
    # so an inferred condition would pair every asset with all its siblings.
    qry = (
        db.query(RiskAsset)
        .outerjoin(RiskCategory, RiskCategory.id == RiskAsset.category_id)
        .outerjoin(
            RiskAssetSubcategory, RiskAssetSubcategory.id == RiskAsset.subcategory_id
        )
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
        raise DomainError(status_code=400, detail="category_id must be a UUID")
    if cid:
        qry = qry.filter(RiskAsset.category_id == cid)
    sid = _try_uuid(subcategory_id) if subcategory_id else None
    if subcategory_id and not sid:
        raise DomainError(status_code=400, detail="subcategory_id must be a UUID")
    if sid:
        qry = qry.filter(RiskAsset.subcategory_id == sid)
    total = int(qry.count() or 0)
    rows = (
        qry.order_by(func.lower(RiskAsset.name).asc()).offset(offset).limit(limit).all()
    )
    return _list_response(
        [_asset_out(db, r, fw) for r in rows], total, limit, offset, fw
    )


def get_asset(*, asset_id: str, framework: str, user: User, db: Session):
    return _asset_out(db, _asset_or_404(db, asset_id), framework_parameter(framework))


def update_asset(
    *,
    asset_id: str,
    payload: AssetPayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = framework_parameter(framework)
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
    if "asset" in fields:
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
        raise DomainError(status_code=400, detail="Asset already exists")
    if {"license_id", "license_name"} & fields:
        license_row = _license_from_payload(db, payload, user)
        row.license_id = license_row.id if license_row else None
        row.license = license_row.name if license_row else ""
    if "owner_org_node_id" in fields:
        row.owner_org_node_id = org_node_or_400(db, payload.owner_org_node_id)
    if "register_held_by_org_node_id" in fields:
        row.register_held_by_org_node_id = org_node_or_400(
            db, payload.register_held_by_org_node_id
        )
    if "description" in fields:
        row.description = clean_text(payload.description, max_len=20000)
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    _apply_links(db, "asset", row.id, fw, payload, user)
    after = _asset_out(db, row, fw)
    _record(db, "asset", row, "updated", before, after, user, context)
    db.commit()
    db.refresh(row)
    return _asset_out(db, row, fw)


def delete_asset(
    *, asset_id: str, context: ChangeContext, framework: str, user: User, db: Session
):
    fw = framework_parameter(framework)
    row = _asset_or_404(db, asset_id)
    in_use = db.query(Risk.id).filter(Risk.asset_id == row.id).first()
    if in_use:
        raise DomainError(
            status_code=400,
            detail="Cannot delete an asset referenced by CIA Triad risks",
        )
    before = _asset_out(db, row, fw)
    _record(db, "asset", row, "deleted", before, None, user, context)
    _delete_entity_links(db, "asset", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}
