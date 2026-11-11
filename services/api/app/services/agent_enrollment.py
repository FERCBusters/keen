from __future__ import annotations

import hmac
import ipaddress
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import Field, field_validator
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.agents import server_keys
from app.agents.credentials import (
    bounded_lock_wait,
    digest,
    live_only,
    reauthenticate,
    revoke_keys,
)
from app.agents.schema import Strict
from app.core.datetime_utils import utc_now_naive
from app.core.valkey import get_valkey
from app.db.models import AgentEnrollmentProfile, AgentKey, AuditLog, KeenAgent
from app.security.agent_signatures import SignatureError, key_id, verify_proof
from app.security.rate_limit import address_window_allow, fixed_window_allow
from app.security.regex import scan_pattern
from app.services.context import ChangeContext
from app.services.errors import DomainError
from app.services.ueu_evidence import catalogue_grant
from app.services.ueu_subjects import public_subject_key, subject_grant


class ProfileInput(Strict):
    name: str = Field(min_length=1, max_length=128)
    token_days: int = Field(default=90, ge=1, le=365)
    subject_type: Literal["pairwise", "public"] = "pairwise"
    evidence_mode_ceiling: Literal["events", "events_with_lines", "all_lines"] = (
        "events"
    )
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
                "Label keys must be 1-64 characters and values at most 256"
            )
        return value


PUBLIC_KEY = r"^[A-Za-z0-9_-]{43}$"
# kc_ codes and ke_ bootstrap keys end with KEEN's identity fingerprint.
CREDENTIAL = r"^(k[ce])_([0-9a-f-]{36})\.[A-Za-z0-9_-]{43}\.([A-Za-z0-9_-]{22})$"
PROOF = r"^[A-Za-z0-9_-]{86}$"
NONCE = r"^[A-Za-z0-9_-]{43}$"


class Enrollment(Strict):
    # The credential travels inside the encrypted request, never in a header.
    credential: str = Field(max_length=128)
    public_key: str = Field(pattern=PUBLIC_KEY)
    # Each attempt signs a fresh proof: its creation time and a nonce KEEN
    # accepts once, so a recorded request cannot be replayed for the grant.
    created: int = Field(ge=0, le=2**53 - 1)
    proof_nonce: str = Field(pattern=NONCE)
    proof: str = Field(pattern=PROOF)
    # Profile enrolment names the new agent and carries the agent's saved
    # nonce, so a retry after a lost response returns the same agent.
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$",
    )
    nonce: str | None = Field(default=None, pattern=NONCE)


class Renewal(Strict):
    public_key: str = Field(pattern=PUBLIC_KEY)
    nonce: str = Field(pattern=NONCE)
    proof: str = Field(pattern=PROOF)


# Transaction lock namespace for claims on an agent name.
_AGENT_NAME_LOCK = 75431020


def lock_agent_name(db: Session, name: str) -> None:
    """Serialise claims on one agent name, whatever its case, until commit.

    Profile enrolments and administrator registrations take it, so a profile
    enrolment's check sees every agent that claimed the name before it.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(:space, hashtext(:name))"),
        {"space": _AGENT_NAME_LOCK, "name": name.lower()},
    )


def name_held_outside(db: Session, name: str, profile_id: str) -> bool:
    """Whether an active agent outside the profile holds the name.

    The name becomes the trusted ``system`` label of the agent's evidence, which
    mapping rules match, so a profile's key enrols only under names no other
    active device holds; machines within one profile may share a name.
    """
    return (
        db.query(KeenAgent.id)
        .filter(
            func.lower(KeenAgent.name) == name.lower(),
            KeenAgent.enabled.is_(True),
            KeenAgent.expires_at > utc_now_naive(),
            or_(
                KeenAgent.enrollment_profile_id.is_(None),
                KeenAgent.enrollment_profile_id != profile_id,
            ),
        )
        .first()
        is not None
    )


def audit(db, context: ChangeContext, action, profile=None, agent=None):
    actor = context.actor
    db.add(
        AuditLog(
            username=getattr(actor, "username", None),
            method="AGENT",
            path="/v1/agents/" + action,
            query_string="profile=" + str(profile or "") + "&agent=" + str(agent or ""),
            status_code=200,
            duration_ms=0,
            client_ip=context.client_ip,
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
            "subject_type",
            "evidence_mode_ceiling",
            "expires_at",
            "max_enrollments",
            "enrollment_count",
            "allowed_cidrs",
            "labels",
            "created_at",
            "revoked_at",
        )
    }


def new_key(db, profile):
    value = (
        "ke_"
        + profile.id
        + "."
        + secrets.token_urlsafe(32)
        + "."
        + server_keys.identity(db).fingerprint
    )
    profile.key_hash = digest(value)
    return value


def rate(context: ChangeContext, suffix="ip", ident=None, limit=60):
    """Limit enrolment work by credential or agent, or else by client address."""
    if ident:
        allowed, retry = fixed_window_allow(
            get_valkey(),
            f"keen:enrollment:{suffix}:{ident}",
            limit,
            60,
            fail_closed=True,
        )
    else:
        allowed, retry = address_window_allow(
            get_valkey(),
            f"enrollment:{suffix}",
            context.client_ip,
            limit,
            60,
            fail_closed=True,
        )
    if not allowed:
        raise DomainError(
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
        raise DomainError(404, "Enrollment profile not found")
    return profile


def listing(*, db: Session):
    return {
        "profiles": [
            public(p)
            for p in db.query(AgentEnrollmentProfile).order_by(
                AgentEnrollmentProfile.name
            )
        ]
    }


def create(*, body: ProfileInput, context: ChangeContext, db: Session):
    live_only()
    if body.subject_type == "public":
        public_subject_key(db)
    profile = AgentEnrollmentProfile(
        id=str(uuid.uuid4()), state="active", enrollment_count=0, **body.model_dump()
    )
    key = new_key(db, profile)
    db.add(profile)
    audit(db, context, "profile-created", profile.id)
    db.commit()
    return {"profile": public(profile), "bootstrap_key": key}


def manage(*, ident: str, action: str, context: ChangeContext, db: Session):
    live_only()
    if action not in {"rotate", "pause", "resume", "revoke"}:
        raise DomainError(404, "Unknown profile action")
    profile = locked_profile(db, ident)
    if profile.state == "revoked":
        raise DomainError(
            409, "Revoked profiles cannot be reactivated; create a new profile"
        )
    key = None
    if action == "rotate":
        key = new_key(db, profile)
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
        enrolled = db.query(KeenAgent.id).filter_by(enrollment_profile_id=ident)
        revoke_keys(db, AgentKey.agent_id.in_(enrolled.scalar_subquery()))
    else:
        profile.state = "paused" if action == "pause" else "active"
    audit(db, context, "profile-" + action, ident)
    db.commit()
    result = {"profile": public(profile)}
    if key:
        result["bootstrap_key"] = key
    return result


def server_key_document(*, context: ChangeContext, db: Session):
    """KEEN's identity and its current signed encryption key, rotating the
    encryption key when due. Nothing exists until KEEN issues its first agent
    credential, so an anonymous request never creates KEEN's keys."""
    rate(context, "server-key-ip", limit=120)
    if server_keys.current_fingerprint(db) is None:
        raise DomainError(404, "This KEEN has issued no agent credentials yet")
    document = server_keys.document(db)
    db.commit()
    return document


def grant(db, agent, key):
    """What an agent learns from enrolment or renewal."""
    return {
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "agent_type": agent.agent_type,
            "enrollment_profile_id": agent.enrollment_profile_id,
            "expires_at": agent.expires_at,
            "enabled": agent.enabled,
        },
        "key_id": key.key_id,
        "subject": subject_grant(db, agent.subject_type),
        "evidence_mode_ceiling": agent.evidence_mode_ceiling,
        "catalogue": catalogue_grant(),
    }


def bind(db, agent, public_key, origin, credential_hash, previous=None):
    """Make public_key the agent's only active key."""
    ident = key_id(agent.id, public_key)
    if db.get(AgentKey, ident) is not None:
        raise DomainError(409, "This key has already been used; generate a new key")
    now = utc_now_naive()
    db.query(AgentKey).filter_by(agent_id=agent.id, state="active").update(
        {AgentKey.state: "retired", AgentKey.retired_at: now},
        synchronize_session=False,
    )
    db.flush()
    key = AgentKey(
        key_id=ident,
        agent_id=agent.id,
        public_key=public_key,
        state="active",
        created_at=now,
        previous_key_id=previous,
        origin=origin,
        credential_hash=credential_hash,
    )
    db.add(key)
    agent.expires_at = now + timedelta(days=agent.validity_days)
    db.flush()
    return key


PROOF_SECONDS = 300
PROOF_NONCE_SECONDS = 2 * PROOF_SECONDS


def proof_store():
    return get_valkey()


def remember_proof(public_key, nonce):
    """Accept each enrolment proof once. Without Valkey, refuse."""
    try:
        fresh = proof_store().set(
            "keen:enrollment:proof:" + digest(public_key) + ":" + nonce,
            "1",
            nx=True,
            ex=PROOF_NONCE_SECONDS,
        )
    except Exception:
        raise DomainError(
            503, "Enrollment unavailable; retry later", headers={"Retry-After": "30"}
        ) from None
    if not fresh:
        raise DomainError(400, "Key proof has been used; sign a new proof and retry")


def prove(body, statement):
    try:
        verify_proof(body.public_key, statement, body.proof)
    except SignatureError:
        raise DomainError(400, "Key proof invalid") from None


def previous_binding(db, agent, credential_hash, public_key):
    """A retried request returns the binding it made, and nothing else."""
    key = (
        db.query(AgentKey)
        .filter_by(agent_id=agent.id, credential_hash=credential_hash)
        .one_or_none()
    )
    if key is None:
        return None
    if key.state != "active" or key.public_key != public_key:
        raise DomainError(
            409,
            "This enrolment credential has been used; ask an administrator for a new code",
        )
    return key


def require_pinned_identity(db, pinned):
    if pinned != server_keys.current_fingerprint(db):
        raise DomainError(
            401,
            "This credential was issued under a different KEEN identity key; "
            "ask an administrator for a new one",
        )


def enroll(*, body: Enrollment, context: ChangeContext, db: Session):
    live_only()
    rate(context)
    match = scan_pattern(CREDENTIAL).fullmatch(body.credential)
    if not match:
        raise DomainError(401, "Invalid enrollment credential")
    credential = body.credential
    kind, ident, pinned = match.groups()
    require_pinned_identity(db, pinned)
    if abs(time.time() - body.created) > PROOF_SECONDS:
        raise DomainError(
            400, "Key proof is not current; check the machine's clock and retry"
        )
    statement = {
        "context": "keen-agent-enroll-v2",
        "created": body.created,
        "credential_sha256": digest(credential),
        "proof_nonce": body.proof_nonce,
        "public_key": body.public_key,
    }
    prove(body, statement)
    remember_proof(body.public_key, body.proof_nonce)
    if kind == "kc":
        agent, key = enroll_with_code(db, context, credential, ident, body)
    else:
        agent, key = enroll_with_profile(db, context, credential, ident, body)
    # Both paths now hold the lock an identity reset takes first, so the
    # identity stays as read here until this commits. A reset that committed
    # while they waited for it strands the credential, and nothing binds.
    require_pinned_identity(db, pinned)
    result = grant(db, agent, key)
    db.commit()
    return result


def _holds_credential(db, model, ident, credential_hash, *, stored):
    """Whether a credential could be the record's own, judged without a lock.

    Only then does enrolment take the record's row lock and check again, so a
    request without the credential never waits for, or holds, that lock.
    """
    record = db.query(model).filter_by(id=ident).one_or_none()
    if record is None:
        return False
    own = getattr(record, stored)
    if own and hmac.compare_digest(own, credential_hash):
        return True
    # A retry of a completed enrolment carries a code that is spent.
    return (
        model is KeenAgent
        and db.query(AgentKey.key_id)
        .filter_by(agent_id=ident, credential_hash=credential_hash)
        .first()
        is not None
    )


def enroll_with_code(db, context, code, ident, body):
    if body.name is not None or body.nonce is not None:
        raise DomainError(400, "Enrolment codes take only a key and its proof")
    if not _holds_credential(
        db, KeenAgent, ident, digest(code), stored="enrollment_code_hash"
    ):
        raise DomainError(401, "Invalid enrollment credential")
    with bounded_lock_wait(db):
        agent = (
            db.query(KeenAgent)
            .filter_by(id=ident)
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
    if agent is None or not agent.enabled or agent.credential_type != "key":
        raise DomainError(401, "Invalid enrollment credential")
    code_hash = digest(code)
    key = previous_binding(db, agent, code_hash, body.public_key)
    if key is not None:
        # The agent's own allowance is spent only by holders of its code, as
        # a profile's is by holders of its key, so wrong codes from anyone
        # who knows the agent's identifier cannot hold up its enrolment.
        rate(context, "code", ident, 10)
        return agent, key
    if (
        not agent.enrollment_code_hash
        or not hmac.compare_digest(agent.enrollment_code_hash, code_hash)
        or agent.enrollment_code_expires_at is None
        or agent.enrollment_code_expires_at <= utc_now_naive()
    ):
        raise DomainError(401, "Invalid enrollment credential")
    if agent.enrollment_profile_id is not None:
        # Codes are issued to individually registered agents only.
        raise DomainError(401, "Invalid enrollment credential")
    rate(context, "code", ident, 10)
    agent.enrollment_code_hash = None
    agent.enrollment_code_expires_at = None
    agent.enrollment_ip = context.client_ip
    key = bind(db, agent, body.public_key, "code", code_hash)
    audit(db, context, "enrolled", None, agent.id)
    return agent, key


def enroll_with_profile(db, context, bootstrap, ident, body):
    if body.name is None or body.nonce is None:
        raise DomainError(400, "Profile enrolment needs a name and a nonce")
    if not _holds_credential(
        db, AgentEnrollmentProfile, ident, digest(bootstrap), stored="key_hash"
    ):
        raise DomainError(401, "Invalid enrollment credential")
    # The profile's agents hold its row shared while their requests run.
    with bounded_lock_wait(db):
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
        or not hmac.compare_digest(profile.key_hash, digest(bootstrap))
    ):
        raise DomainError(401, "Invalid enrollment credential")
    rate(context, "profile", ident, 120)
    if profile.state != "active" or (
        profile.expires_at and profile.expires_at <= utc_now_naive()
    ):
        raise DomainError(403, "Enrollment is paused or expired")
    address = context.client_ip
    if profile.allowed_cidrs and (
        not address
        or not any(
            ipaddress.ip_address(address) in ipaddress.ip_network(c)
            for c in profile.allowed_cidrs
        )
    ):
        raise DomainError(
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
        key = None
        if (
            agent.enabled
            and agent.expires_at > utc_now_naive()
            and agent.name == body.name
        ):
            key = previous_binding(db, agent, nonce_hash, body.public_key)
        if key is None:
            raise DomainError(
                409,
                "Enrollment attempt is no longer recoverable; restore the agent state or contact an administrator",
            )
        return agent, key
    if (
        profile.max_enrollments is not None
        and profile.enrollment_count >= profile.max_enrollments
    ):
        raise DomainError(403, "Enrollment limit reached")
    lock_agent_name(db, body.name)
    if name_held_outside(db, body.name, ident):
        raise DomainError(
            409,
            "Agent name is in use outside this enrollment profile; choose another name",
        )
    agent = KeenAgent(
        id=str(uuid.uuid4()),
        name=body.name,
        enabled=True,
        health={},
        token_hash="",
        credential_type="key",
        subject_type=profile.subject_type,
        evidence_mode_ceiling=profile.evidence_mode_ceiling,
        validity_days=profile.token_days,
        enrollment_profile_id=ident,
        enrollment_nonce_hash=nonce_hash,
        enrollment_ip=address,
        enrollment_labels=dict(profile.labels),
        expires_at=utc_now_naive() + timedelta(days=profile.token_days),
    )
    db.add(agent)
    db.flush()
    key = bind(db, agent, body.public_key, "profile", nonce_hash)
    profile.enrollment_count += 1
    audit(db, context, "enrolled", ident, agent.id)
    return agent, key


def renew(*, body: Renewal, context: ChangeContext, db: Session, auth):
    """Replace the agent's key. The request is signed by the current key and
    the body proves possession of the new one. A retry signed by the key the
    renewal retired returns the same result for 24 hours."""
    live_only()
    rate(context, "renew-ip")
    agent, current = reauthenticate(db, auth, allow_retired=True)
    rate(context, "renew-agent", agent.id, 6)
    nonce_hash = digest(body.nonce)
    statement = {
        "context": "keen-agent-renew-v1",
        "agent_id": agent.id,
        "previous_key_id": current.key_id,
        "public_key": body.public_key,
    }
    prove(body, statement)
    key = previous_binding(db, agent, nonce_hash, body.public_key)
    if key is not None:
        if key.previous_key_id != current.key_id:
            raise DomainError(401, "Invalid agent credential")
    elif current.state != "active":
        raise DomainError(401, "Invalid agent credential")
    else:
        key = bind(
            db, agent, body.public_key, "renewal", nonce_hash, previous=current.key_id
        )
        audit(db, context, "renewed", agent.enrollment_profile_id, agent.id)
    result = grant(db, agent, key)
    db.commit()
    return result
