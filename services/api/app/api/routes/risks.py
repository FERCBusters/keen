from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.routing import Router
from app.core.config import settings
from app.db.models import User
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.services import risks as service
from app.services.risks import (
    RiskAssetPayload,
    RiskCategoryPayload,
    RiskControlLinksPayload,
    RiskMitigatorAnalysePayload,
    RiskSubcategoryPayload,
    RiskUpsertPayload,
    can_manage_risks,
    can_read_risks,
)

router = Router()

_RISK_TYPES = {"Confidentiality", "Integrity", "Availability"}


def require_risk_read(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not can_read_risks(db, user):
        raise HTTPException(status_code=403, detail="risk.read permission required")
    return user


def require_risk_manage(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not can_manage_risks(db, user):
        raise HTTPException(status_code=403, detail="risk.manage permission required")
    return user


@router.get("/v1/risks/categories")
def list_risk_categories(
    user=Depends(require_risk_read), db: Session = Depends(get_db)
):
    return service.list_risk_categories(user=user, db=db)


@router.post("/v1/risks/categories")
def create_risk_category(
    payload: RiskCategoryPayload,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.create_risk_category(payload=payload, user=user, db=db)


@router.patch("/v1/risks/categories/{category_id}")
def update_risk_category(
    category_id: str,
    payload: RiskCategoryPayload,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.update_risk_category(
        category_id=category_id, payload=payload, user=user, db=db
    )


@router.delete("/v1/risks/categories/{category_id}")
def delete_risk_category(
    category_id: str,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.delete_risk_category(category_id=category_id, user=user, db=db)


@router.get("/v1/risks/subcategories")
def list_risk_subcategories(
    category_id: str = "",
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    return service.list_risk_subcategories(category_id=category_id, user=user, db=db)


@router.post("/v1/risks/subcategories")
def create_risk_subcategory(
    payload: RiskSubcategoryPayload,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.create_risk_subcategory(payload=payload, user=user, db=db)


@router.patch("/v1/risks/subcategories/{subcategory_id}")
def update_risk_subcategory(
    subcategory_id: str,
    payload: RiskSubcategoryPayload,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.update_risk_subcategory(
        subcategory_id=subcategory_id, payload=payload, user=user, db=db
    )


@router.delete("/v1/risks/subcategories/{subcategory_id}")
def delete_risk_subcategory(
    subcategory_id: str,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.delete_risk_subcategory(
        subcategory_id=subcategory_id, user=user, db=db
    )


@router.get("/v1/risks/assets")
def list_risk_assets(
    q: str = "",
    category_id: str = "",
    subcategory_id: str = "",
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    return service.list_risk_assets(
        q=q, category_id=category_id, subcategory_id=subcategory_id, user=user, db=db
    )


@router.post("/v1/risks/assets")
def create_risk_asset(
    payload: RiskAssetPayload,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.create_risk_asset(payload=payload, user=user, db=db)


@router.patch("/v1/risks/assets/{asset_id}")
def update_risk_asset(
    asset_id: str,
    payload: RiskAssetPayload,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.update_risk_asset(
        asset_id=asset_id, payload=payload, user=user, db=db
    )


@router.delete("/v1/risks/assets/{asset_id}")
def delete_risk_asset(
    asset_id: str,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.delete_risk_asset(asset_id=asset_id, user=user, db=db)


@router.get("/v1/risks/users")
def list_risk_owner_users(
    user=Depends(require_risk_manage), db: Session = Depends(get_db)
):
    return service.list_risk_owner_users(user=user, db=db)


@router.get("/v1/risks/parties")
def list_risk_party_options(
    framework: str = settings.default_framework_slug,
    q: str = "",
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    "Interested parties, people and vendors a risk can name as affected."
    return service.list_risk_party_options(framework=framework, q=q, user=user, db=db)


@router.get("/v1/me/risks")
def list_my_owned_risks(
    framework: str = settings.default_framework_slug,
    limit: int = 100,
    offset: int = 0,
    user: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    "List risks where the current user is explicitly set as the owner."
    return service.list_my_owned_risks(
        framework=framework, limit=limit, offset=offset, user=user, db=db
    )


@router.get("/v1/risks")
def list_risks(
    framework: str = settings.default_framework_slug,
    q: str = "",
    category_id: str = "",
    subcategory_id: str = "",
    risk_type: str = "",
    owner_user_id: str = "",
    owner_role_id: str = "",
    asset_id: str = "",
    limit: int = 200,
    offset: int = 0,
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    return service.list_risks(
        framework=framework,
        q=q,
        category_id=category_id,
        subcategory_id=subcategory_id,
        risk_type=risk_type,
        owner_user_id=owner_user_id,
        owner_role_id=owner_role_id,
        asset_id=asset_id,
        limit=limit,
        offset=offset,
        user=user,
        db=db,
    )


@router.get("/v1/risks/controls-without-risks")
def list_controls_without_risks(
    framework: str = settings.default_framework_slug,
    q: str = "",
    limit: int = 500,
    offset: int = 0,
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    "List framework controls that are not associated with any risk scenario."
    return service.list_controls_without_risks(
        framework=framework, q=q, limit=limit, offset=offset, user=user, db=db
    )


@router.post("/v1/risks/mitigator/analyse")
def analyse_mitigator_risk(
    payload: RiskMitigatorAnalysePayload,
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    return service.analyse_mitigator_risk(payload=payload, user=user, db=db)


@router.post("/v1/risks")
def create_risk(
    payload: RiskUpsertPayload,
    request: Request,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.create_risk(
        payload=payload, context=change_context(request), user=user, db=db
    )


@router.get("/v1/risks/{risk_id}")
def get_risk(
    risk_id: str,
    framework: str = settings.default_framework_slug,
    user: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    return service.get_risk(risk_id=risk_id, framework=framework, user=user, db=db)


@router.get("/v1/risks/{risk_id}/changelog")
def risk_changelog(
    risk_id: str,
    framework: str = settings.default_framework_slug,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    return service.risk_changelog(
        risk_id=risk_id,
        framework=framework,
        limit=limit,
        offset=offset,
        user=user,
        db=db,
    )


@router.patch("/v1/risks/{risk_id}")
def update_risk(
    risk_id: str,
    payload: RiskUpsertPayload,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.update_risk(risk_id=risk_id, payload=payload, user=user, db=db)


@router.delete("/v1/risks/{risk_id}")
def delete_risk(
    risk_id: str,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.delete_risk(risk_id=risk_id, user=user, db=db)


@router.get("/v1/risks/{risk_id}/controls")
def get_risk_controls(
    risk_id: str,
    framework: str = settings.default_framework_slug,
    user: User = Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    return service.get_risk_controls(
        risk_id=risk_id, framework=framework, user=user, db=db
    )


@router.patch("/v1/risks/{risk_id}/controls")
def update_risk_controls(
    risk_id: str,
    payload: RiskControlLinksPayload,
    framework: str = settings.default_framework_slug,
    user=Depends(require_risk_manage),
    db: Session = Depends(get_db),
):
    return service.update_risk_controls(
        risk_id=risk_id, payload=payload, framework=framework, user=user, db=db
    )


@router.get("/v1/controls/{control_id}/risks")
def control_risks(
    control_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_risk_read),
    db: Session = Depends(get_db),
):
    return service.control_risks(
        control_id=control_id, framework=framework, user=user, db=db
    )
