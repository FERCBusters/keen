"""Agent administration is session-admin only; agents sign with their own key."""

from __future__ import annotations

import asyncio
import gzip
import io
import logging
from functools import partial

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.agents.credentials import authenticate, live_only
from app.agents.otlp import decode_batch
from app.agents.schema import Health
from app.api.errors import DomainRoute
from app.api.routing import Router
from app.core.valkey import get_valkey
from app.db.session import get_db
from app.security.auth import require_admin
from app.security.client_ip import request_ip
from app.security.rate_limit import fixed_window_allow
from app.services import agents as service
from app.services.agents import Assignment, EvidencePolicy, Registration, Rotation
from app.services.errors import DomainError

from .agent_enrollment import router as enrollment_router


class AgentRoute(DomainRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request):
            try:
                return await handler(request)
            except (HTTPException, DomainError) as exc:
                if request.url.path != "/v1/otlp/logs":
                    raise
                code = {
                    400: 3,
                    401: 16,
                    403: 7,
                    408: 4,
                    409: 6,
                    413: 8,
                    415: 12,
                    429: 8,
                }.get(exc.status_code, 13)
                return JSONResponse(
                    {"code": code, "message": str(exc.detail)},
                    status_code=exc.status_code,
                    headers=exc.headers,
                )
            except Exception:
                if request.url.path != "/v1/otlp/logs":
                    raise
                logging.getLogger(__name__).exception("OTLP batch failed")
                return JSONResponse(
                    {
                        "code": 13,
                        "message": "Evidence persistence failed; retry the batch",
                    },
                    status_code=500,
                )

        return wrapped


router = Router(route_class=AgentRoute)
admin = Router(
    prefix="/v1/admin/agents",
    dependencies=[Depends(require_admin)],
)
MAX_BODY = 1024 * 1024


@admin.get("")
def listing(response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.listing(db=db)


@admin.post("", status_code=201)
def register(body: Registration, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.register(body=body, db=db)


@admin.patch("/{agent_id}")
def assign(agent_id: str, body: Assignment, db: Session = Depends(get_db)):
    return service.assign(agent_id=agent_id, body=body, db=db)


@admin.post("/{agent_id}/rotate")
def rotate(
    agent_id: str, body: Rotation, response: Response, db: Session = Depends(get_db)
):
    response.headers["Cache-Control"] = "no-store"
    return service.rotate(agent_id=agent_id, body=body, db=db)


@admin.post("/{agent_id}/revoke")
def revoke(agent_id: str, db: Session = Depends(get_db)):
    return service.revoke(agent_id=agent_id, db=db)


@admin.patch("/{agent_id}/evidence")
def evidence_policy(agent_id: str, body: EvidencePolicy, db: Session = Depends(get_db)):
    return service.set_evidence_policy(agent_id=agent_id, body=body, db=db)


def _admit_batch(request, db):
    """Authenticate an evidence batch, check intake is open and apply its limit."""
    auth = authenticate_agent(
        request, db, "keen:agent:auth-failure:ip:", operation="otlp-logs"
    )
    from app.services.ingestion_pause import require_receiving

    require_receiving(db, "keen-agent")
    db.rollback()
    ok, retry = fixed_window_allow(
        get_valkey(), "keen:agent:id:" + auth.agent_id, 60, 60, fail_closed=True
    )
    if not ok:
        raise HTTPException(429, "Rate limited", headers={"Retry-After": str(retry)})
    return auth


# Agent requests read their bodies asynchronously, and run their database,
# Valkey and storage work in the thread pool, as synchronous routes do. A batch
# writes and syncs each artifact to storage, which must not hold up the event
# loop and every other request with it.
@router.post("/v1/otlp/logs")
async def ingest(request: Request, response: Response, db: Session = Depends(get_db)):
    live_only()
    auth = await run_in_threadpool(_admit_batch, request, db)
    if (
        request.headers.get("content-type", "").split(";")[0].strip()
        != "application/json"
    ):
        raise HTTPException(
            415,
            "This endpoint supports OTLP/HTTP JSON; configure exporter encoding=json",
        )
    if request.headers.get("content-encoding", "identity") not in ("identity", "gzip"):
        raise HTTPException(415, "Unsupported encoding")
    body = await bounded_body(request, MAX_BODY)
    # A signed request's digest covers the bytes as sent.
    auth.check_body(body)
    try:
        if request.headers.get("content-encoding") == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                body = stream.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise HTTPException(413, "Decompressed batch exceeds 1 MiB")
        batch = decode_batch(body, auth.agent_id)
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        OverflowError,
        OSError,
        EOFError,
        RecursionError,
    ):
        raise HTTPException(
            400, "Invalid OTLP JSON logs; maximum 64 records and bounded attributes"
        )
    result = await run_in_threadpool(
        partial(
            service.persist_batch,
            db=db,
            auth=auth,
            authorization=request.headers.get("authorization", ""),
            batch=batch,
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return result


def authenticate_agent(request, db, failure_key, *, operation, allow_retired=False):
    """Authenticate before applying address limits.

    Agents commonly share one proxy address. Counting only rejected credentials
    per address stops unauthenticated traffic from exhausting a shared bucket;
    accepted agents are bounded by their own per-agent limits.
    """
    try:
        return authenticate(
            request, db, operation=operation, lock=False, allow_retired=allow_retired
        )
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        ok, retry = fixed_window_allow(
            get_valkey(),
            failure_key + (request_ip(request) or "unknown"),
            120,
            60,
            fail_closed=True,
        )
        if not ok:
            raise HTTPException(
                429, "Rate limited", headers={"Retry-After": str(retry)}
            ) from None
        raise


async def bounded_body(request, limit):
    body = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(body) + len(chunk) > limit:
                    raise HTTPException(413, "Request exceeds limit")
                body.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "Request body timeout")
    return body


@router.post("/v1/agents/heartbeat")
async def heartbeat(
    request: Request, response: Response, db: Session = Depends(get_db)
):
    live_only()
    auth = await run_in_threadpool(_admit_heartbeat, request, db)
    body = await bounded_body(request, 32768)
    auth.check_body(body)
    try:
        health = Health.model_validate_json(body)
    except ValidationError:
        raise HTTPException(400, "Invalid health report")
    result = await run_in_threadpool(
        partial(
            service.record_heartbeat,
            db=db,
            auth=auth,
            authorization=request.headers.get("authorization", ""),
            health=health,
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return result


def _admit_heartbeat(request, db):
    """Authenticate a health report and apply its limit."""
    auth = authenticate_agent(
        request, db, "keen:agent:health:auth-failure:ip:", operation="heartbeat"
    )
    db.rollback()
    ok, retry = fixed_window_allow(
        get_valkey(), "keen:agent:health:id:" + auth.agent_id, 6, 60, fail_closed=True
    )
    if not ok:
        raise HTTPException(429, "Rate limited", headers={"Retry-After": str(retry)})
    return auth


router.include_router(admin)


router.include_router(enrollment_router)
