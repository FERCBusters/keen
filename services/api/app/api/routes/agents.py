"""Agent administration is session-admin only; ingest uses its own scoped token."""

import asyncio
import gzip
import io
import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
import logging
from pydantic import Field, ValidationError
from sqlalchemy.orm import Session
from app.agents.schema import Strict, Health, fingerprint
from app.agents.otlp import decode
from app.core.config import settings
from app.core.valkey import get_valkey
from app.security.rate_limit import fixed_window_allow
from app.security.auth import require_admin
from app.db.session import get_db
from app.db.models import KeenAgent, Event
from app.ingest.common import store_event_with_artifact
from app.security.redaction import redact_obj


class AgentRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request):
            try:
                return await handler(request)
            except HTTPException as exc:
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


router = APIRouter(route_class=AgentRoute)
admin = APIRouter(prefix="/v1/admin/agents", dependencies=[Depends(require_admin)])
MAX_BODY = 1024 * 1024


class Registration(Strict):
    name: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$"
    )
    expires_days: int = Field(default=90, ge=1, le=365)


class Rotation(Strict):
    expires_days: int = Field(default=90, ge=1, le=365)


def live_only():
    if settings.demo_mode:
        raise HTTPException(
            403,
            "Agent ingestion and credential management are disabled in demo environments",
        )


def view(agent):
    return {
        key: getattr(agent, key)
        for key in (
            "id",
            "name",
            "enabled",
            "created_at",
            "expires_at",
            "last_seen",
            "health",
        )
    }


def issue(agent, days):
    token = "ka_" + agent.id + "." + secrets.token_urlsafe(32)
    agent.token_hash = hashlib.sha256(token.encode()).hexdigest()
    agent.expires_at = datetime.utcnow() + timedelta(days=days)
    return token


@admin.get("")
def listing(response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return {
        "demo_mode": settings.demo_mode,
        "agents": [view(a) for a in db.query(KeenAgent).order_by(KeenAgent.name)],
    }


@admin.post("", status_code=201)
def register(body: Registration, response: Response, db: Session = Depends(get_db)):
    live_only()
    a = KeenAgent(id=str(uuid.uuid4()), name=body.name, enabled=True, health={})
    token = issue(a, body.expires_days)
    db.add(a)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"agent": view(a), "token": token}


@admin.post("/{agent_id}/rotate")
def rotate(
    agent_id: str, body: Rotation, response: Response, db: Session = Depends(get_db)
):
    live_only()
    a = db.query(KeenAgent).filter_by(id=agent_id).with_for_update().one_or_none()
    if not a:
        raise HTTPException(404, "Agent not found")
    if not a.enabled:
        raise HTTPException(
            409, "Revoked agents cannot be reactivated; register a new identity"
        )
    token = issue(a, body.expires_days)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"agent": view(a), "token": token}


@admin.post("/{agent_id}/revoke")
def revoke(agent_id: str, db: Session = Depends(get_db)):
    live_only()
    a = db.query(KeenAgent).filter_by(id=agent_id).with_for_update().one_or_none()
    if not a:
        raise HTTPException(404, "Agent not found")
    a.enabled = False
    a.token_hash = ""
    db.commit()
    return {"ok": True}


def authenticate(request, db, *, lock=True):
    header = request.headers.get("authorization", "")
    match = re.fullmatch(r"Bearer (ka_([0-9a-f-]{36})\.[A-Za-z0-9_-]{43})", header)
    if not match:
        raise HTTPException(401, "Invalid agent credential")
    query = db.query(KeenAgent).filter_by(id=match[2])
    if lock:
        query = query.with_for_update()
    a = query.one_or_none()
    digest = hashlib.sha256(match[1].encode()).hexdigest()
    if (
        not a
        or not hmac.compare_digest(a.token_hash, digest)
        or not a.enabled
        or a.expires_at <= datetime.utcnow()
    ):
        raise HTTPException(401, "Invalid agent credential")
    return a


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
    require_receiving(db, 'keen-agent')
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
    # Per-agent row lock serializes ingestion and credential revocation. One transaction
    # persists the whole batch; a lost acknowledgement is safely retried.
    a = authenticate(request, db)
    accepted = []
    for event in events:
        external = a.id + ":" + str(event.id)
        digest = fingerprint(event)
        old = (
            db.query(Event)
            .filter_by(source="keen-agent", external_id=external)
            .one_or_none()
        )
        if old:
            if old.raw_pointer.get("agent", {}).get("sha256") != digest:
                raise HTTPException(409, "Event UUID reused with different content")
        else:
            payload = event.model_dump(mode="json")
            store_event_with_artifact(
                db,
                timestamp=event.timestamp.astimezone(timezone.utc).replace(tzinfo=None),
                source="keen-agent",
                system=a.name,
                actor=redact_obj(event.actor),
                action=redact_obj(event.action),
                outcome=redact_obj(event.outcome),
                severity=event.severity,
                summary=redact_obj(event.summary),
                raw_pointer={
                    "agent": {
                        "id": a.id,
                        "event_id": str(event.id),
                        "sha256": digest,
                        "received_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
                normalized_payload=payload,
                external_id=external,
                artifact_kind="agent_record",
                artifact_bytes=json.dumps(payload).encode(),
                artifact_content_type="application/json",
                artifact_key=f"agents/{a.id}/{event.id}/{uuid.uuid4()}.json",
                captured_by="keen-agent:" + a.id,
                commit=False,
            )
        accepted.append(str(event.id))
    a.last_seen = datetime.utcnow()
    a.health = {
        **a.health,
        "last_batch_records": len(events),
        "received_total": a.health.get("received_total", 0) + len(accepted),
    }
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {}


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
    a = authenticate(request, db)
    a.last_seen = datetime.utcnow()
    a.health = {
        **a.health,
        **redact_obj(health.model_dump(mode="json")),
        "health_received_at": datetime.utcnow().isoformat(),
    }
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


router.include_router(admin)
