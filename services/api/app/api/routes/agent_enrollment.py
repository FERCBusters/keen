"""Enrolment codes and fleet bootstrap keys bind an agent key; the agent signs
every later request with it."""

from __future__ import annotations

from functools import partial

from fastapi import Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

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
async def renew(request: Request, response: Response, db: Session = Depends(get_db)):
    from app.agents.credentials import live_only

    from .agents import authenticate_agent, bounded_body

    live_only()
    # Database work runs in the thread pool, as for the other agent requests.
    auth = await run_in_threadpool(
        partial(
            authenticate_agent,
            request,
            db,
            "keen:agent:renew:auth-failure:ip:",
            operation="renew",
            allow_retired=True,
        )
    )
    if auth.signed is None:
        raise HTTPException(401, "Invalid agent credential")
    await run_in_threadpool(db.rollback)
    body = await bounded_body(request, 4096)
    auth.check_body(body)
    try:
        renewal = Renewal.model_validate_json(body)
    except ValidationError:
        raise HTTPException(400, "Invalid renewal request")
    response.headers["Cache-Control"] = "no-store"
    return await run_in_threadpool(
        partial(
            service.renew,
            body=renewal,
            context=change_context(request),
            db=db,
            auth=auth,
        )
    )


router.include_router(admin)
