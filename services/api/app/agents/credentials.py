"""Agent credentials; profile-before-agent locking serializes revocation."""
import hashlib
import hmac
import re
from fastapi import HTTPException
from app.core.datetime_utils import utc_now_naive
from app.core.config import settings
from app.db.models import KeenAgent, AgentEnrollmentProfile


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def agent_credential(request):
    match = re.fullmatch(r'Bearer (ka_([0-9a-f-]{36})\.[A-Za-z0-9_-]{43})', request.headers.get('authorization',''))
    if not match:
        raise HTTPException(401, 'Invalid agent credential')
    return match[1], match[2]


def locked_agent(db, ident, *, lock=True):
    agent = db.query(KeenAgent).filter_by(id=ident).one_or_none()
    if not agent:
        raise HTTPException(401, 'Invalid agent credential')
    profile = None
    if agent.enrollment_profile_id:
        query = db.query(AgentEnrollmentProfile).filter_by(id=agent.enrollment_profile_id).populate_existing()
        profile = (query.with_for_update() if lock else query).one_or_none()
        if not profile or profile.state == 'revoked':
            raise HTTPException(401, 'Invalid agent credential')
    query = db.query(KeenAgent).filter_by(id=ident).populate_existing()
    agent = (query.with_for_update() if lock else query).one()
    if not agent.enabled or agent.expires_at <= utc_now_naive():
        raise HTTPException(401, 'Invalid agent credential')
    return agent, profile


def authenticate(request, db, *, lock=True):
    token, ident = agent_credential(request)
    agent, _ = locked_agent(db, ident, lock=lock)
    if not hmac.compare_digest(agent.token_hash, digest(token)):
        raise HTTPException(401, 'Invalid agent credential')
    return agent


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
            "enrollment_profile_id",
            "enrollment_labels",
        )
    }


