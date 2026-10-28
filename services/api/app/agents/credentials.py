"""Agent credentials; profile-before-agent locking serialises revocation.

keen-agent identities authenticate every request with an RFC 9421 signature
made by their current Ed25519 key. Generic OTLP collectors, which cannot sign,
authenticate with a bearer token instead; that credential type is chosen when
an administrator registers the identity and never changes.
"""

import hashlib
import hmac
import re
from datetime import timedelta

from fastapi import HTTPException

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.core.valkey import get_valkey
from app.db.models import AgentEnrollmentProfile, AgentKey, KeenAgent
from app.security.agent_signatures import (
    SignatureError,
    parse_request,
    verify_digest,
    verify_request,
)
from app.security.rate_limit import fixed_window_allow

NONCE_SECONDS = 600
RENEWAL_GRACE = timedelta(hours=24)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def parse_agent_credential(authorization):
    match = re.fullmatch(
        r"Bearer (ka_([0-9a-f-]{36})\.[A-Za-z0-9_-]{43})",
        authorization,
    )
    if not match:
        raise HTTPException(401, "Invalid agent credential")
    return match[1], match[2]


def locked_agent(db, ident, *, lock=True):
    agent = db.query(KeenAgent).filter_by(id=ident).one_or_none()
    if not agent:
        raise HTTPException(401, "Invalid agent credential")
    profile = None
    if agent.enrollment_profile_id:
        query = (
            db.query(AgentEnrollmentProfile)
            .filter_by(id=agent.enrollment_profile_id)
            .populate_existing()
        )
        profile = (query.with_for_update() if lock else query).one_or_none()
        if not profile or profile.state == "revoked":
            raise HTTPException(401, "Invalid agent credential")
    query = db.query(KeenAgent).filter_by(id=ident).populate_existing()
    agent = (query.with_for_update() if lock else query).one()
    if not agent.enabled or agent.expires_at <= utc_now_naive():
        raise HTTPException(401, "Invalid agent credential")
    return agent, profile


def authenticate_credential(authorization, db, *, lock=True):
    """Bearer-token authentication, for token identities only."""
    token, ident = parse_agent_credential(authorization)
    agent, _ = locked_agent(db, ident, lock=lock)
    if agent.credential_type != "token" or not hmac.compare_digest(
        agent.token_hash, digest(token)
    ):
        raise HTTPException(401, "Invalid agent credential")
    return agent


def signing_key(db, agent, key_id, *, retired_since=None):
    """Return the agent's key for key_id.

    The active key is always usable. A key retired after ``retired_since``
    is usable only where the caller allows it (a renewal retry).
    """
    key = db.get(AgentKey, key_id, populate_existing=True)
    if key is None or key.agent_id != agent.id:
        raise HTTPException(401, "Invalid agent credential")
    if key.state == "active":
        return key
    if (
        retired_since is not None
        and key.state == "retired"
        and key.retired_at is not None
        and key.retired_at > retired_since
    ):
        return key
    raise HTTPException(401, "Invalid agent credential")


SIGNED_PER_MINUTE = 180


def remember_nonce(signed):
    """Accept each request signature once. Without Valkey, refuse."""
    try:
        fresh = get_valkey().set(
            "keen:agent:nonce:" + signed.key_id + ":" + signed.nonce,
            "1",
            nx=True,
            ex=NONCE_SECONDS,
        )
    except Exception:
        raise HTTPException(
            503, "Replay protection unavailable", headers={"Retry-After": "30"}
        ) from None
    if not fresh:
        raise HTTPException(401, "Invalid agent credential")


class Authenticated:
    """An authenticated agent request: the agent and, for keys, the signature."""

    def __init__(self, agent, signed=None):
        self.agent_id = agent.id
        self.credential_type = agent.credential_type
        self.signed = signed

    @property
    def key_id(self):
        return self.signed.key_id if self.signed else None

    def check_body(self, body):
        if self.signed is not None:
            try:
                verify_digest(self.signed, body)
            except SignatureError:
                raise HTTPException(401, "Invalid agent credential") from None


def authenticate(request, db, *, operation, lock=False, allow_retired=False):
    """Authenticate an agent request before its body is read.

    A signed request is verified over its covered headers; the caller checks
    the body against the signed Content-Digest once it has read it, then
    reauthenticates under the agent's row lock before writing.
    """
    headers = request.headers
    if "signature-input" not in headers and "signature" not in headers:
        return Authenticated(
            authenticate_credential(headers.get("authorization", ""), db, lock=lock)
        )
    try:
        signed = parse_request(headers, operation)
    except SignatureError:
        raise HTTPException(401, "Invalid agent credential") from None
    agent, _ = locked_agent(db, signed.agent_id, lock=lock)
    if agent.credential_type != "key":
        raise HTTPException(401, "Invalid agent credential")
    since = utc_now_naive() - RENEWAL_GRACE if allow_retired else None
    key = signing_key(db, agent, signed.key_id, retired_since=since)
    try:
        verify_request(
            signed,
            key.public_key,
            request.method,
            operation,
            headers.get("content-type", ""),
        )
    except SignatureError:
        raise HTTPException(401, "Invalid agent credential") from None
    # Bound what a key holder can write to Valkey before storing its nonce;
    # the endpoints apply their own, tighter limits afterwards. Without
    # Valkey the nonce store below refuses the request (503).
    ok, retry = fixed_window_allow(
        get_valkey(), "keen:agent:signed:" + agent.id, SIGNED_PER_MINUTE, 60
    )
    if not ok:
        raise HTTPException(429, "Rate limited", headers={"Retry-After": str(retry)})
    remember_nonce(signed)
    return Authenticated(agent, signed)


def reauthenticate(db, auth, authorization="", *, allow_retired=False):
    """Lock the agent row and check its credential is still current."""
    if auth.signed is None:
        return authenticate_credential(authorization, db, lock=True), None
    agent, _ = locked_agent(db, auth.agent_id, lock=True)
    if agent.credential_type != "key":
        raise HTTPException(401, "Invalid agent credential")
    since = utc_now_naive() - RENEWAL_GRACE if allow_retired else None
    return agent, signing_key(db, agent, auth.key_id, retired_since=since)


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
            "agent_type",
            "person_id",
            "enabled",
            "created_at",
            "expires_at",
            "last_seen",
            "health",
            "enrollment_profile_id",
            "enrollment_labels",
            "credential_type",
            "subject_type",
            "evidence_mode_ceiling",
            "enrollment_code_expires_at",
        )
    }
