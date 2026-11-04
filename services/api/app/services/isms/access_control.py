"""ISMS access control domain operations."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive as _utcnow
from app.db.models import (
    IsmsAccessControlMatrixEntry,
    IsmsOrgNode,
    RiskAsset,
    User,
)
from app.services.context import ChangeContext
from app.services.errors import DomainError
from app.services.frameworks import framework_parameter
from app.services.text import clean_text

from .common import _by_id_or_404, _record
from .mutations import _replace_access_control_roles
from .schemas import AccessControlMatrixPayload
from .serializers import _access_control_matrix_out


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
            raise DomainError(status_code=400, detail="Unknown service asset")
    role_ids: list[uuid.UUID] = []
    if not fields or "role_org_node_ids" in fields:
        role_ids.extend(payload.role_org_node_ids or [])
    for role_id in set(role_ids):
        if not db.query(IsmsOrgNode.id).filter(IsmsOrgNode.id == role_id).first():
            raise DomainError(status_code=400, detail="Unknown organisation chart role")


def create_access_control_matrix_entry(
    *,
    payload: AccessControlMatrixPayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = framework_parameter(framework)
    if not payload.service_asset_id:
        raise DomainError(status_code=400, detail="service_asset_id is required")
    _validate_access_control_payload(db, payload)
    row = IsmsAccessControlMatrixEntry(
        task_action=clean_text(
            payload.task_action, max_len=20000, required=True, label="task/action"
        ),
        service_asset_id=payload.service_asset_id,
        notes=clean_text(payload.notes, max_len=12000),
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    _replace_access_control_roles(db, row, payload.role_org_node_ids or [])
    db.flush()
    after = _access_control_matrix_out(db, row, fw)
    _record(db, "access_control_matrix", row, "created", None, after, user, context)
    db.commit()
    db.refresh(row)
    return _access_control_matrix_out(db, row, fw)


def list_access_control_matrix(*, framework: str, user: User, db: Session):
    fw = framework_parameter(framework)
    rows = (
        db.query(IsmsAccessControlMatrixEntry)
        .order_by(IsmsAccessControlMatrixEntry.updated_at.desc())
        .all()
    )
    return {
        "framework": fw,
        "items": [_access_control_matrix_out(db, row, fw) for row in rows],
    }


def get_access_control_matrix_entry(
    *, entry_id: str, framework: str, user: User, db: Session
):
    return _access_control_matrix_out(
        db,
        _by_id_or_404(
            db, IsmsAccessControlMatrixEntry, entry_id, "Access control matrix row"
        ),
        framework_parameter(framework),
    )


def update_access_control_matrix_entry(
    *,
    entry_id: str,
    payload: AccessControlMatrixPayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = framework_parameter(framework)
    row = _by_id_or_404(
        db, IsmsAccessControlMatrixEntry, entry_id, "Access control matrix row"
    )
    before = _access_control_matrix_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    _validate_access_control_payload(db, payload, fields)
    if "task_action" in fields:
        row.task_action = clean_text(
            payload.task_action, max_len=20000, required=True, label="task/action"
        )
    if "service_asset_id" in fields:
        if not payload.service_asset_id:
            raise DomainError(status_code=400, detail="service_asset_id is required")
        row.service_asset_id = payload.service_asset_id
    if "role_org_node_ids" in fields:
        _replace_access_control_roles(db, row, payload.role_org_node_ids or [])
    if "notes" in fields:
        row.notes = clean_text(payload.notes, max_len=12000)
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    after = _access_control_matrix_out(db, row, fw)
    _record(db, "access_control_matrix", row, "updated", before, after, user, context)
    db.commit()
    return _access_control_matrix_out(db, row, fw)


def delete_access_control_matrix_entry(
    *, entry_id: str, context: ChangeContext, framework: str, user: User, db: Session
):
    fw = framework_parameter(framework)
    row = _by_id_or_404(
        db, IsmsAccessControlMatrixEntry, entry_id, "Access control matrix row"
    )
    before = _access_control_matrix_out(db, row, fw)
    _record(db, "access_control_matrix", row, "deleted", before, None, user, context)
    db.delete(row)
    db.commit()
    return {"ok": True}
