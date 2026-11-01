"""Enrolment codes and fleet bootstrap keys bind an agent key; the agent signs
every later request with it."""

from __future__ import annotations

from functools import partial

from fastapi import Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.agents import server_keys
from app.api.context import change_context
from app.api.routing import Router
from app.db.session import get_db
from app.security.auth import require_admin
from app.services import agent_enrollment as service
from app.services.agent_enrollment import Enrollment, ProfileInput, Renewal

# Enrolment and renewal bodies are small; sealing adds a fixed overhead.
ENROLL_BODY = 4096 + server_keys.SEALED_OVERHEAD

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


def _open_enrollment(request, body, db):
    # Bound the decryption work an address can ask for before doing any.
    service.rate(change_context(request), "enroll-open-ip", limit=120)
    opened = server_keys.open_request(db, request.headers, body, "enroll")
    try:
        enrollment = Enrollment.model_validate_json(opened.body)
    except ValidationError:
        raise HTTPException(400, "Invalid enrollment request")
    return opened, enrollment


@router.post("/v1/agents/enroll", status_code=201)
async def enroll(request: Request, db: Session = Depends(get_db)):
    """Bind an agent key. The request, credential included, is encrypted to
    KEEN, and the grant is encrypted with the key the agent sent inside it."""
    from app.agents.credentials import live_only

    from .agents import bounded_body, sealed

    live_only()
    body = await bounded_body(request, ENROLL_BODY)
    opened, enrollment = await run_in_threadpool(_open_enrollment, request, body, db)
    result = await run_in_threadpool(
        partial(service.enroll, body=enrollment, context=change_context(request), db=db)
    )
    return await run_in_threadpool(sealed, db, opened, 201, result)


@router.get("/v1/agents/server-key")
def server_key(request: Request, response: Response, db: Session = Depends(get_db)):
    """KEEN's identity key and its signed current encryption key. Agents check
    the identity against the fingerprint in their credential before use."""
    from app.agents.credentials import live_only

    live_only()
    response.headers["Cache-Control"] = "no-store"
    return service.server_key_document(context=change_context(request), db=db)


@router.post("/v1/agents/renew")
async def renew(request: Request, db: Session = Depends(get_db)):
    from app.agents.credentials import live_only

    from .agents import authenticate_agent, bounded_body, sealed

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
    body = await bounded_body(request, ENROLL_BODY)
    auth.check_body(body)
    opened = await run_in_threadpool(
        server_keys.open_request, db, request.headers, body, "renew", auth.key_id
    )
    try:
        renewal = Renewal.model_validate_json(opened.body)
    except ValidationError:
        raise HTTPException(400, "Invalid renewal request")
    result = await run_in_threadpool(
        partial(
            service.renew,
            body=renewal,
            context=change_context(request),
            db=db,
            auth=auth,
        )
    )
    return await run_in_threadpool(sealed, db, opened, 200, result)


router.include_router(admin)
