"""HTTP adapters for ISMS assets."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_manage, require_isms_read
from app.api.routing import Router
from app.core.config import settings
from app.db.session import get_db
from app.services.context import ChangeContext
from app.services.isms import assets as service
from app.services.isms.schemas import (
    AssetCategoryPayload,
    AssetPayload,
    AssetSubcategoryPayload,
    LicensePayload,
)

router = Router()


@router.get("/v1/isms/asset-categories")
def list_asset_categories(
    user=Depends(require_isms_read), db: Session = Depends(get_db)
):
    return service.list_asset_categories(user=user, db=db)


@router.post("/v1/isms/asset-categories")
def create_asset_category(
    payload: AssetCategoryPayload,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_asset_category(payload=payload, user=user, db=db)


@router.post("/v1/isms/asset-subcategories")
def create_asset_subcategory(
    payload: AssetSubcategoryPayload,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_asset_subcategory(payload=payload, user=user, db=db)


@router.get("/v1/isms/licenses")
def list_licenses(
    q: str = "",
    framework: str = settings.default_framework_slug,
    limit: int = 500,
    offset: int = 0,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.list_licenses(
        q=q, framework=framework, limit=limit, offset=offset, user=user, db=db
    )


@router.post("/v1/isms/licenses")
def create_license(
    payload: LicensePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_license(
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.patch("/v1/isms/licenses/{license_id}")
def update_license(
    license_id: str,
    payload: LicensePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.update_license(
        license_id=license_id,
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.delete("/v1/isms/licenses/{license_id}")
def delete_license(
    license_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_license(
        license_id=license_id,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.post("/v1/isms/assets")
def create_asset(
    payload: AssetPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_asset(
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


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
    return service.list_assets(
        q=q,
        framework=framework,
        category_id=category_id,
        subcategory_id=subcategory_id,
        limit=limit,
        offset=offset,
        user=user,
        db=db,
    )


@router.get("/v1/isms/assets/{asset_id}")
def get_asset(
    asset_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.get_asset(asset_id=asset_id, framework=framework, user=user, db=db)


@router.patch("/v1/isms/assets/{asset_id}")
def update_asset(
    asset_id: str,
    payload: AssetPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.update_asset(
        asset_id=asset_id,
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )


@router.delete("/v1/isms/assets/{asset_id}")
def delete_asset(
    asset_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_asset(
        asset_id=asset_id,
        context=ChangeContext(method=request.method, path=request.url.path),
        framework=framework,
        user=user,
        db=db,
    )
