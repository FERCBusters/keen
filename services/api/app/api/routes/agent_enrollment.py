"""Fleet bootstrap credentials grant enrollment only; each agent owns its token."""

import base64
import hmac
import ipaddress
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field, field_validator
from sqlalchemy.orm import Session

from app.agents.credentials import (
    agent_credential,
    digest,
    live_only,
    locked_agent,
    view,
)
from app.agents.schema import Strict
from app.core.datetime_utils import utc_now_naive
from app.core.valkey import get_valkey
from app.db.models import AgentEnrollmentProfile, AuditLog, KeenAgent
from app.db.session import get_db
from app.security.auth import require_admin
from app.security.client_ip import request_ip
from app.security.rate_limit import fixed_window_allow

router = APIRouter()
admin = APIRouter(
    prefix="/v1/admin/agent-enrollment-profiles", dependencies=[Depends(require_admin)]
)


class ProfileInput(Strict):
    name: str = Field(min_length=1, max_length=128)
    token_days: int = Field(default=90, ge=1, le=365)
    expires_at: datetime | None = None
    max_enrollments: int | None = Field(default=None, ge=1, le=1000000)
    allowed_cidrs: list[str] = Field(default_factory=list, max_length=32)
    labels: dict[str, str] = Field(default_factory=dict, max_length=32)

    @field_validator("expires_at")
    @classmethod
    def expiry(cls, value):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("Include a timezone in the enrollment expiry")
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
            if value <= utc_now_naive():
                raise ValueError("Enrollment expiry must be in the future")
        return value

    @field_validator("allowed_cidrs")
    @classmethod
    def cidrs(cls, value):
        return sorted({str(ipaddress.ip_network(v, strict=False)) for v in value})

    @field_validator("labels")
    @classmethod
    def labels_valid(cls, value):
        if any(not k or len(k) > 64 or len(v) > 256 for k, v in value.items()):
            raise ValueError(
                "Label keys must be 1–64 characters and values at most 256"
            )
        return value


class Enrollment(Strict):
    name: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$"
    )
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class Renewal(Strict):
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


def audit(db, request, action, profile=None, agent=None):
    actor = getattr(request.state, "user", None)
    db.add(
        AuditLog(
            username=getattr(actor, "username", None),
            method="AGENT",
            path="/v1/agents/" + action,
            query_string="profile=" + str(profile or "") + "&agent=" + str(agent or ""),
            status_code=200,
            duration_ms=0,
            client_ip=request_ip(request),
        )
    )


def public(profile):
    return {
        k: getattr(profile, k)
        for k in (
            "id",
            "name",
            "state",
            "token_days",
            "expires_at",
            "max_enrollments",
            "enrollment_count",
            "allowed_cidrs",
            "labels",
            "created_at",
            "revoked_at",
        )
    }


def new_key(profile):
    value = "ke_" + profile.id + "." + secrets.token_urlsafe(32)
    profile.key_hash = digest(value)
    return value


def rate(request, suffix="ip", ident=None, limit=60):
    key = ident or request_ip(request) or "unknown"
    allowed, retry = fixed_window_allow(
        get_valkey(),
        "keen:enrollment:" + suffix + ":" + key,
        limit,
        60,
        fail_closed=True,
    )
    if not allowed:
        raise HTTPException(
            429,
            "Enrollment temporarily limited; retry later",
            headers={"Retry-After": str(retry)},
        )


def locked_profile(db, ident):
    profile = (
        db.query(AgentEnrollmentProfile)
        .filter_by(id=ident)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if not profile:
        raise HTTPException(404, "Enrollment profile not found")
    return profile


@admin.get("")
def listing(response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return {
        "profiles": [
            public(p)
            for p in db.query(AgentEnrollmentProfile).order_by(
                AgentEnrollmentProfile.name
            )
        ]
    }


@admin.post("", status_code=201)
def create(
    body: ProfileInput,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    live_only()
    profile = AgentEnrollmentProfile(
        id=str(uuid.uuid4()), state="active", enrollment_count=0, **body.model_dump()
    )
    key = new_key(profile)
    db.add(profile)
    audit(db, request, "profile-created", profile.id)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"profile": public(profile), "bootstrap_key": key}


@admin.post("/{ident}/{action}")
def manage(
    ident: str,
    action: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    live_only()
    if action not in {"rotate", "pause", "resume", "revoke"}:
        raise HTTPException(404, "Unknown profile action")
    profile = locked_profile(db, ident)
    if profile.state == "revoked":
        raise HTTPException(
            409, "Revoked profiles cannot be reactivated; create a new profile"
        )
    key = None
    if action == "rotate":
        key = new_key(profile)
    elif action == "revoke":
        profile.state = "revoked"
        profile.key_hash = ""
        profile.revoked_at = utc_now_naive()
        db.query(KeenAgent).filter_by(enrollment_profile_id=ident).update(
            {
                KeenAgent.enabled: False,
                KeenAgent.token_hash: "",
                KeenAgent.previous_token_hash: None,
                KeenAgent.renewal_nonce_hash: None,
                KeenAgent.renewal_retry_until: None,
            },
            synchronize_session=False,
        )
    else:
        profile.state = "paused" if action == "pause" else "active"
    audit(db, request, "profile-" + action, ident)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    result = {"profile": public(profile)}
    if key:
        result["bootstrap_key"] = key
    return result


def derived_token(key, nonce, ident, purpose):
    # Domain separation and a private, persistent 256-bit client nonce make a
    # lost response retryable without storing recoverable credentials in KEEN.
    secret = (
        base64.urlsafe_b64encode(
            hmac.digest(
                key.encode(), (purpose + ":" + ident + ":" + nonce).encode(), "sha256"
            )
        )
        .decode()
        .rstrip("=")
    )
    return "ka_" + ident + "." + secret


@router.post("/v1/agents/enroll", status_code=201)
def enroll(
    body: Enrollment,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    live_only()
    rate(request)
    match = re.fullmatch(
        r"Bearer (ke_([0-9a-f-]{36})\.[A-Za-z0-9_-]{43})",
        request.headers.get("authorization", ""),
    )
    if not match:
        raise HTTPException(401, "Invalid enrollment credential")
    key, ident = match.groups()
    profile = (
        db.query(AgentEnrollmentProfile)
        .filter_by(id=ident)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if (
        not profile
        or profile.state == "revoked"
        or not hmac.compare_digest(profile.key_hash, digest(key))
    ):
        raise HTTPException(401, "Invalid enrollment credential")
    rate(request, "profile", ident, 120)
    if profile.state != "active" or (
        profile.expires_at and profile.expires_at <= utc_now_naive()
    ):
        raise HTTPException(403, "Enrollment is paused or expired")
    address = request_ip(request)
    if profile.allowed_cidrs and (
        not address
        or not any(
            ipaddress.ip_address(address) in ipaddress.ip_network(c)
            for c in profile.allowed_cidrs
        )
    ):
        raise HTTPException(
            403, "Client address is outside the enrollment profile networks"
        )
    nonce_hash = digest(body.nonce)
    agent = (
        db.query(KeenAgent)
        .filter_by(enrollment_profile_id=ident, enrollment_nonce_hash=nonce_hash)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if agent:
        token = derived_token(key, body.nonce, agent.id, "enroll")
        if (
            not agent.enabled
            or agent.expires_at <= utc_now_naive()
            or agent.name != body.name
            or not hmac.compare_digest(agent.token_hash, digest(token))
        ):
            raise HTTPException(
                409,
                "Enrollment attempt is no longer recoverable; restore the agent credential or contact an administrator",
            )
    else:
        if (
            profile.max_enrollments is not None
            and profile.enrollment_count >= profile.max_enrollments
        ):
            raise HTTPException(403, "Enrollment limit reached")
        agent = KeenAgent(
            id=str(uuid.uuid4()),
            name=body.name,
            enabled=True,
            health={},
            enrollment_profile_id=ident,
            enrollment_nonce_hash=nonce_hash,
            enrollment_ip=address,
            enrollment_labels=dict(profile.labels),
            expires_at=utc_now_naive() + timedelta(days=profile.token_days),
        )
        token = derived_token(key, body.nonce, agent.id, "enroll")
        agent.token_hash = digest(token)
        db.add(agent)
        profile.enrollment_count += 1
        audit(db, request, "enrolled", ident, agent.id)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"agent": view(agent), "token": token}


@router.post("/v1/agents/renew")
def renew(
    body: Renewal, request: Request, response: Response, db: Session = Depends(get_db)
):
    live_only()
    rate(request, "renew-ip")
    token, ident = agent_credential(request)
    agent, profile = locked_agent(db, ident)
    rate(request, "renew-agent", ident, 6)
    if profile is None:
        raise HTTPException(
            403, "Individual agent credentials are renewed by an administrator"
        )
    nonce_hash = digest(body.nonce)
    fresh = derived_token(token, body.nonce, agent.id, "renew")
    if hmac.compare_digest(agent.token_hash, digest(token)):
        agent.previous_token_hash = agent.token_hash
        agent.renewal_nonce_hash = nonce_hash
        agent.renewal_retry_until = utc_now_naive() + timedelta(minutes=5)
        agent.token_hash = digest(fresh)
        agent.expires_at = utc_now_naive() + timedelta(days=profile.token_days)
        audit(db, request, "renewed", profile.id, agent.id)
    elif not (
        agent.previous_token_hash
        and hmac.compare_digest(agent.previous_token_hash, digest(token))
        and agent.renewal_nonce_hash == nonce_hash
        and agent.renewal_retry_until > utc_now_naive()
        and hmac.compare_digest(agent.token_hash, digest(fresh))
    ):
        raise HTTPException(401, "Invalid agent credential")
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"agent": view(agent), "token": fresh}


router.include_router(admin)
