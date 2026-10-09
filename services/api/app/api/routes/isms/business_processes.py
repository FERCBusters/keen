"""ISMS business processes; see docs/maintainability-review.md for module boundaries."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.models import IsmsBusinessProcess
from app.db.session import get_db
from app.services.entity_changelog import record_entity_changelog

from .access import (
    require_isms_manage,
    require_isms_read,
)
from .common import (
    _clean_text,
    _utcnow,
)
from .schemas import (
    BusinessProcessPayload,
)
from .serializers import (
    _business_process_out,
)

router = APIRouter()


@router.post("/v1/isms/business-processes")
def create_business_process(
    payload: BusinessProcessPayload,
    request: Request,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    row = IsmsBusinessProcess(
        name=_clean_text(payload.name, max_len=256, required=True, label="name"),
        description=_clean_text(payload.description, max_len=20000),
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
        request_method=request.method,
        request_path=request.url.path,
    )
    db.commit()
    return after


@router.get("/v1/isms/business-processes")
def list_business_processes(
    user=Depends(require_isms_read), db: Session = Depends(get_db)
):
    rows = (
        db.query(IsmsBusinessProcess)
        .order_by(IsmsBusinessProcess.sort_order.asc(), IsmsBusinessProcess.name.asc())
        .all()
    )
    return {"items": [_business_process_out(r) for r in rows]}
