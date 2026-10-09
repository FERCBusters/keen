"""ISMS objectives; see docs/maintainability-review.md for module boundaries."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import IsmsObjective, User
from app.db.session import get_db

from .access import (
    require_isms_manage,
    require_isms_read,
)
from .common import (
    _by_id_or_404,
    _clean_framework,
    _clean_text,
    _list_response,
    _record,
    _utcnow,
)
from .constants import (
    OBJECTIVE_STATUSES,
)
from .mutations import (
    _apply_links,
    _delete_entity_links,
    _replace_objective_resources,
)
from .schemas import (
    ObjectivePayload,
)
from .serializers import (
    _objective_out,
)

router = APIRouter()


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
