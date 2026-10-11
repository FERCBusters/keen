"""Fleet bootstrap credentials grant enrollment only; each agent owns its token."""

from __future__ import annotations

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.routing import Router
from app.db.session import get_db
from app.security.auth import require_admin
from app.services import agent_enrollment as service
from app.services.agent_enrollment import Enrollment, ProfileInput, Renewal

router = Router()
admin = Router(
    prefix="/v1/admin/agent-enrollment-profiles",
    dependencies=[Depends(require_admin)],
)


@admin.get("")
def listing(response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.listing(db=db)


@admin.post("", status_code=201)
def create(
    body: ProfileInput,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    return service.create(body=body, context=change_context(request), db=db)


@admin.post("/{ident}/{action}")
def manage(
    ident: str,
    action: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    return service.manage(
        ident=ident, action=action, context=change_context(request), db=db
    )


@router.post("/v1/agents/enroll", status_code=201)
def enroll(
    body: Enrollment,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    return service.enroll(
        body=body,
        context=change_context(request),
        db=db,
        authorization=request.headers.get("authorization", ""),
    )


@router.post("/v1/agents/renew")
def renew(
    body: Renewal, request: Request, response: Response, db: Session = Depends(get_db)
):
    response.headers["Cache-Control"] = "no-store"
    return service.renew(
        body=body,
        context=change_context(request),
        db=db,
        authorization=request.headers.get("authorization", ""),
    )


router.include_router(admin)
