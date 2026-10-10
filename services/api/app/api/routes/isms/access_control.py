"""ISMS access control; see docs/maintainability-review.md for module boundaries."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    IsmsAccessControlMatrixEntry,
    IsmsAwsAccount,
    IsmsOrgNode,
    RiskAsset,
    User,
)
from app.db.session import get_db
from app.services.entity_changelog import record_entity_changelog

from .access import (
    require_isms_manage,
    require_isms_read,
)
from .common import (
    _by_id_or_404,
    _clean_framework,
    _clean_text,
    _record,
    _utcnow,
)
from .constants import (
    ACCESS_STATUSES,
)
from .mutations import (
    _replace_access_control_accounts,
    _replace_access_control_roles,
)
from .schemas import (
    AccessControlMatrixPayload,
    AwsAccountPayload,
)
from .serializers import (
    _access_control_matrix_out,
    _aws_account_out,
    _aws_accounts_out,
)

router = APIRouter()


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
