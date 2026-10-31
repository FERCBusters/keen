"""ISMS business processes domain operations."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.datetime_utils import utc_now_naive as _utcnow
from app.db.models import IsmsBusinessProcess, User
from app.services.context import ChangeContext
from app.services.entity_changelog import record_entity_changelog
from app.services.text import clean_text

from .schemas import BusinessProcessPayload
from .serializers import _business_process_out


def create_business_process(
    *, payload: BusinessProcessPayload, context: ChangeContext, user: User, db: Session
):
    row = IsmsBusinessProcess(
        name=clean_text(payload.name, max_len=256, required=True, label="name"),
        description=clean_text(payload.description, max_len=20000),
        sort_order=int(payload.sort_order or 0),
        is_default=False,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    after = _business_process_out(row)
    record_entity_changelog(
        db,
        entity_type="isms_business_process",
        entity_id=row.id,
        action="created",
        before=None,
        after=after,
        user=user,
        request_method=context.method,
        request_path=context.path,
    )
    db.commit()
    return after


def list_business_processes(*, user: User, db: Session):
    rows = (
        db.query(IsmsBusinessProcess)
        .order_by(IsmsBusinessProcess.sort_order.asc(), IsmsBusinessProcess.name.asc())
        .all()
    )
    return {"items": [_business_process_out(r) for r in rows]}
