"""HTTP adapters for the legal register; every route uses the application's
protected middleware, and the ISMS prefix rule gates each write."""

from uuid import UUID

from fastapi import Depends, Query, Request
from fastapi.responses import StreamingResponse
from mosp.api.context import change_context
from mosp.api.routing import Router
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_manage, require_isms_read
from app.core.config import settings
from app.db.session import get_db
from app.services.isms import legal_register as service

router = Router()
BASE = "/v1/isms/legal-register"


def _filters(
    q: str = Query("", max_length=256),
    category_id: UUID | None = None,
    in_scope: bool | None = None,
    status: str = Query("", max_length=24),
    owner_user_id: UUID | None = None,
    due: bool | None = None,
    target_type: str = Query("", max_length=24),
    target_id: UUID | None = None,
    control: str = Query("", max_length=128),
    clause: str = Query("", max_length=128),
    framework: str = settings.default_framework_slug,
) -> dict:
    """What the register is narrowed by; the list and its export share these."""
    return {
        "q": q,
        "category_id": category_id,
        "in_scope": in_scope,
        "status": status,
        "owner_user_id": owner_user_id,
        "due": due,
        "target_type": target_type,
        "target_id": target_id,
        "control": control,
        "clause": clause,
        "framework": framework,
    }


@router.get(BASE)
def list_items(
    filters: dict = Depends(_filters),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.list_items(user=user, db=db, limit=limit, offset=offset, **filters)


@router.get(BASE + "/meta")
def meta(
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    "Categories, compliance statuses and the controls a new item starts with."
    return service.meta(framework=framework, user=user, db=db)


@router.get(BASE + "/export.csv")
def export_register(
    filters: dict = Depends(_filters),
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return StreamingResponse(
        service.export_register(user=user, db=db, **filters),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=keen-legal-register.csv"},
    )


@router.get(BASE + "/choices/{target_type}")
def choices(
    target_type: str,
    q: str = Query("", max_length=256),
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.choices(target_type=target_type, q=q, user=user, db=db)


@router.post(BASE + "/categories")
def create_category(
    payload: service.CategoryPayload,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.save_category(payload=payload, user=user, db=db)


@router.patch(BASE + "/categories/{category_id}")
def update_category(
    category_id: UUID,
    payload: service.CategoryPayload,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.save_category(
        category_id=category_id, payload=payload, user=user, db=db
    )


@router.delete(BASE + "/categories/{category_id}")
def delete_category(
    category_id: UUID,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_category(category_id=category_id, user=user, db=db)


@router.post(BASE)
def create_item(
    payload: service.LegalItemPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.save_item(
        payload=payload,
        framework=framework,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.get(BASE + "/{item_id}")
def get_item(
    item_id: UUID,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    return service.get_item(item_id=item_id, framework=framework, user=user, db=db)


@router.patch(BASE + "/{item_id}")
def update_item(
    item_id: UUID,
    payload: service.LegalItemPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.save_item(
        item_id=item_id,
        payload=payload,
        framework=framework,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.delete(BASE + "/{item_id}")
def delete_item(
    item_id: UUID,
    request: Request,
    expected_version: int = Query(..., ge=1),
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_item(
        item_id=item_id,
        expected_version=expected_version,
        framework=framework,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.post(BASE + "/{item_id}/links")
def add_link(
    item_id: UUID,
    payload: service.LinkPayload,
    request: Request,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.change_link(
        item_id=item_id,
        payload=payload,
        remove=False,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.delete(BASE + "/{item_id}/links/{target_type}/{target_id}")
def remove_link(
    item_id: UUID,
    target_type: service.LinkType,
    target_id: UUID,
    request: Request,
    expected_version: int = Query(..., ge=1),
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.change_link(
        item_id=item_id,
        payload=service.LinkPayload(
            target_type=target_type,
            target_id=target_id,
            expected_version=expected_version,
        ),
        remove=True,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.post(BASE + "/{item_id}/assessments")
def add_assessment(
    item_id: UUID,
    payload: service.AssessmentPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.add_assessment(
        item_id=item_id,
        payload=payload,
        framework=framework,
        user=user,
        db=db,
        context=change_context(request),
    )


@router.delete(BASE + "/{item_id}/assessments/{assessment_id}")
def delete_assessment(
    item_id: UUID,
    assessment_id: UUID,
    request: Request,
    expected_version: int = Query(..., ge=1),
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.delete_assessment(
        item_id=item_id,
        assessment_id=assessment_id,
        expected_version=expected_version,
        framework=framework,
        user=user,
        db=db,
        context=change_context(request),
    )
