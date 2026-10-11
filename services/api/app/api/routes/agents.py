"""Agent administration is session-admin only; ingest uses its own scoped token."""

from __future__ import annotations

import asyncio
import gzip
import io
import logging

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agents.credentials import authenticate, live_only
from app.agents.otlp import decode
from app.agents.schema import Health
from app.api.errors import DomainRoute
from app.api.routing import Router
from app.core.valkey import get_valkey
from app.db.session import get_db
from app.security.auth import require_admin
from app.security.rate_limit import fixed_window_allow
from app.services import agents as service
from app.services.agents import Registration, Rotation
from app.services.errors import DomainError


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


@admin.post("/{agent_id}/rotate")
def rotate(
    agent_id: str, body: Rotation, response: Response, db: Session = Depends(get_db)
):
    response.headers["Cache-Control"] = "no-store"
    return service.rotate(agent_id=agent_id, body=body, db=db)


@admin.post("/{agent_id}/revoke")
def revoke(agent_id: str, db: Session = Depends(get_db)):
    return service.revoke(agent_id=agent_id, db=db)


@router.post("/v1/otlp/logs")
async def ingest(request: Request, response: Response, db: Session = Depends(get_db)):
    live_only()
    ip = request.client.host if request.client else "unknown"
    ok, retry = fixed_window_allow(
        get_valkey(), "keen:agent:ip:" + ip, 120, 60, fail_closed=True
    )
    if not ok:
        raise HTTPException(429, "Rate limited", headers={"Retry-After": str(retry)})
    a = authenticate(request, db, lock=False)
    from app.services.ingestion_pause import require_receiving

    require_receiving(db, "keen-agent")
    agent_id = a.id
    db.rollback()
    ok, retry = fixed_window_allow(
        get_valkey(), "keen:agent:id:" + agent_id, 60, 60, fail_closed=True
    )
    if not ok:
        raise HTTPException(429, "Rate limited", headers={"Retry-After": str(retry)})
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
    try:
        if request.headers.get("content-encoding") == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                body = stream.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise HTTPException(413, "Decompressed batch exceeds 1 MiB")
        events = decode(body, agent_id)
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
    result = service.persist_batch(
        db=db, authorization=request.headers.get("authorization", ""), events=events
    )
    response.headers["Cache-Control"] = "no-store"
    return result


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
    ip = request.client.host if request.client else "unknown"
    ok, retry = fixed_window_allow(
        get_valkey(), "keen:agent:health:ip:" + ip, 120, 60, fail_closed=True
    )
    if not ok:
        raise HTTPException(429, "Rate limited", headers={"Retry-After": str(retry)})
    a = authenticate(request, db, lock=False)
    agent_id = a.id
    db.rollback()
    ok, retry = fixed_window_allow(
        get_valkey(), "keen:agent:health:id:" + agent_id, 6, 60, fail_closed=True
    )
    if not ok:
        raise HTTPException(429, "Rate limited", headers={"Retry-After": str(retry)})
    body = await bounded_body(request, 32768)
    try:
        health = Health.model_validate_json(body)
    except ValidationError:
        raise HTTPException(400, "Invalid health report")
    result = service.record_heartbeat(
        db=db, authorization=request.headers.get("authorization", ""), health=health
    )
    response.headers["Cache-Control"] = "no-store"
    return result


router.include_router(admin)

from .agent_enrollment import router as enrollment_router

router.include_router(enrollment_router)
