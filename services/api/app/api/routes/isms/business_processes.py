"""HTTP adapters for ISMS business processes."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies.isms import require_isms_manage, require_isms_read
from app.api.routing import Router
from app.db.session import get_db
from app.services.context import ChangeContext
from app.services.isms import business_processes as service
from app.services.isms.schemas import BusinessProcessPayload

router = Router()


@router.post("/v1/isms/business-processes")
def create_business_process(
    payload: BusinessProcessPayload,
    request: Request,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    return service.create_business_process(
        payload=payload,
        context=ChangeContext(method=request.method, path=request.url.path),
        user=user,
        db=db,
    )


@router.get("/v1/isms/business-processes")
def list_business_processes(
    user=Depends(require_isms_read), db: Session = Depends(get_db)
):
    return service.list_business_processes(user=user, db=db)
