"""ISMS objectives domain operations."""

from __future__ import annotations

from datetime import date

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import IsmsObjective, User
from app.services.context import ChangeContext
from app.services.errors import DomainError
from app.services.frameworks import framework_parameter
from app.services.text import clean_text

from .common import _by_id_or_404, _list_response, _record, _utcnow
from .constants import OBJECTIVE_STATUSES
from .mutations import _apply_links, _delete_entity_links, _replace_objective_resources
from .schemas import ObjectivePayload
from .serializers import _objective_out


def create_objective(
    *,
    payload: ObjectivePayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = framework_parameter(framework)
    status = (payload.status or "not_started").strip() or "not_started"
    if status not in OBJECTIVE_STATUSES:
        raise DomainError(status_code=400, detail="Invalid objective status")
    if (
        payload.owner_user_id
        and not db.query(User.id)
        .filter(User.id == payload.owner_user_id, User.is_active.is_(True))
        .first()
    ):
        raise DomainError(status_code=400, detail="Unknown owner user")
    row = IsmsObjective(
        start_date=payload.start_date,
        requirement=clean_text(
            payload.requirement, max_len=20000, required=True, label="requirement"
        ),
        goal=clean_text(payload.goal, max_len=20000, required=True, label="goal"),
        metric=clean_text(payload.metric, max_len=20000),
        completion_method=clean_text(payload.completion_method, max_len=12000),
        resource_requirements_text=clean_text(
            payload.resource_requirements_text, max_len=12000
        ),
        owner_user_id=payload.owner_user_id,
        completion_target_date=clean_text(payload.completion_target_date, max_len=64),
        evaluation_method=clean_text(payload.evaluation_method, max_len=12000),
        status=status,
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _validate_dates(row)
    db.add(row)
    db.flush()
    _replace_objective_resources(db, row, payload.resource_user_ids or [])
    _apply_links(db, "objective", row.id, fw, payload, user)
    after = _objective_out(db, row, fw)
    _record(db, "objective", row, "created", None, after, user, context)
    db.commit()
    db.refresh(row)
    return _objective_out(db, row, fw)


def list_objectives(
    *,
    q: str,
    framework: str,
    limit: int,
    offset: int,
    user: User,
    db: Session,
    status: str = "",
):
    fw = framework_parameter(framework)
    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))
    qry = db.query(IsmsObjective)
    if status:
        if status not in OBJECTIVE_STATUSES:
            raise DomainError(400, "Invalid objective status")
        qry = qry.filter(
            IsmsObjective.status.in_(["defined", "not_started"])
            if status == "defined"
            else IsmsObjective.status == status
        )
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


def get_objective(*, objective_id: str, framework: str, user: User, db: Session):
    return _objective_out(
        db,
        _by_id_or_404(db, IsmsObjective, objective_id, "Objective"),
        framework_parameter(framework),
    )


def update_objective(
    *,
    objective_id: str,
    payload: ObjectivePayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = framework_parameter(framework)
    row = _by_id_or_404(db, IsmsObjective, objective_id, "Objective")
    before = _objective_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    if "start_date" in fields:
        row.start_date = payload.start_date
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
                clean_text(
                    getattr(payload, attr),
                    max_len=max_len,
                    required=attr in {"requirement", "goal"},
                    label=attr,
                ),
            )
    if "status" in fields:
        status = (payload.status or "not_started").strip() or "not_started"
        if status not in OBJECTIVE_STATUSES:
            raise DomainError(status_code=400, detail="Invalid objective status")
        row.status = status
    if "owner_user_id" in fields:
        row.owner_user_id = payload.owner_user_id
    if "completion_target_date" in fields:
        row.completion_target_date = clean_text(
            payload.completion_target_date, max_len=64
        )
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    if "resource_user_ids" in fields:
        _replace_objective_resources(db, row, payload.resource_user_ids or [])
    _validate_dates(row)
    _apply_links(db, "objective", row.id, fw, payload, user)
    after = _objective_out(db, row, fw)
    _record(db, "objective", row, "updated", before, after, user, context)
    db.commit()
    db.refresh(row)
    return _objective_out(db, row, fw)


def delete_objective(
    *,
    objective_id: str,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = framework_parameter(framework)
    row = _by_id_or_404(db, IsmsObjective, objective_id, "Objective")
    before = _objective_out(db, row, fw)
    _record(db, "objective", row, "deleted", before, None, user, context)
    _delete_entity_links(db, "objective", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}


def _validate_dates(row):
    try:
        target = (
            date.fromisoformat(row.completion_target_date)
            if row.completion_target_date
            else None
        )
    except ValueError:
        raise DomainError(400, "Target date must use YYYY-MM-DD") from None
    if target and target.isoformat() != row.completion_target_date:
        raise DomainError(400, "Target date must use YYYY-MM-DD")
    if target and row.start_date and target < row.start_date:
        raise DomainError(400, "Target date must be on or after start date")
