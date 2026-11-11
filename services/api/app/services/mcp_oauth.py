"""AI agent (MCP) clients, consent, grants and tokens.

The OAuth protocol is the MCP SDK's (app.api.mcp adapts it); this module keeps
its records. A person approves a client's request on Keen's consent page,
which creates a grant of the scopes they tick: read scopes, and change scopes
while KEEN_MCP_WRITE_ENABLED is set. The client then holds a one-hour access
token and a refresh token that rotates on every use. Presenting a used code or
refresh token again revokes the grant, since only a copy could do that.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    McpAuthorizationCode,
    McpClient,
    McpConsentRequest,
    McpGrant,
    McpToken,
    User,
)
from app.security.auth import ROLE_NORMAL
from app.security.mcp_tokens import (
    ACCESS_PREFIX,
    ACCESS_TOKEN_SECONDS,
    AUTHORIZATION_CODE_SECONDS,
    CODE_PREFIX,
    CONSENT_PREFIX,
    CONSENT_REQUEST_SECONDS,
    GRANT_SECONDS,
    REFRESH_PREFIX,
    REFRESH_TOKEN_SECONDS,
    SCOPES,
    WRITE_SCOPES,
    digest,
    grantable_scopes,
    granted_scopes,
    new_secret,
    redirect_allowed,
    well_formed,
    with_read_scopes,
)
from app.security.permissions import get_effective_permission_codes
from app.services.errors import DomainError
from app.services.security_notifications import enqueue as notify

# A registration nobody approved within a day is removed.
UNUSED_CLIENT_SECONDS = 24 * 3600
# Last use is recorded at most this often, so reads do not write each time.
LAST_USED_RESOLUTION = timedelta(minutes=5)
# Each cleanup removes at most this many rows of each kind.
_CLEANUP_BATCH = 500
# Registrations waiting for a first approval. Anyone can register a client, so
# past this many Keen accepts no more until approvals or expiry make room.
UNAPPROVED_CLIENT_LIMIT = 1000


class ConsentLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(min_length=1, max_length=64)


class ConsentAnswer(ConsentLookup):
    approve: bool
    scopes: list[Annotated[str, Field(max_length=64)]] = Field(
        default_factory=list, max_length=len(SCOPES)
    )


@dataclass(frozen=True)
class CodeRecord:
    client_id: str
    user_id: str
    scopes: list[str]
    code_challenge: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    expires_at: datetime


@dataclass(frozen=True)
class TokenRecord:
    grant_id: str
    client_id: str
    user_id: str
    scopes: list[str]
    expires_at: datetime


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    scopes: list[str]


def mcp_url() -> str:
    """The MCP endpoint's public URL, which tokens are issued for."""
    return settings.public_base_url + "/api/v1/mcp"


def issuer_url() -> str:
    return settings.public_base_url + "/api/v1/oauth"


def remove_expired(db: Session) -> None:
    """Delete lapsed consent requests, codes, tokens, grants and unused clients."""
    now = utc_now_naive()
    for model, condition in (
        (McpConsentRequest, McpConsentRequest.expires_at <= now),
        (McpAuthorizationCode, McpAuthorizationCode.expires_at <= now),
        (McpToken, McpToken.expires_at <= now),
        (
            McpGrant,
            or_(McpGrant.expires_at <= now, McpGrant.revoked_at.is_not(None)),
        ),
        (
            McpClient,
            (McpClient.created_at <= now - timedelta(seconds=UNUSED_CLIENT_SECONDS))
            & ~McpClient.id.in_(db.query(McpGrant.client_pk))
            & ~McpClient.id.in_(db.query(McpConsentRequest.client_pk)),
        ),
    ):
        ids = db.query(model.id).filter(condition).limit(_CLEANUP_BATCH).subquery()
        db.query(model).filter(model.id.in_(db.query(ids.c.id))).delete(
            synchronize_session=False
        )
    db.commit()


# --- Clients ------------------------------------------------------------------


def register_client(db: Session, *, client_id: str, name: str, info: dict) -> None:
    """Store a client registration. ``info`` is the metadata returned to it."""
    for uri in info["redirect_uris"]:
        if not redirect_allowed(uri, settings.mcp_redirect_uris):
            raise DomainError(
                400,
                "Keen accepts loopback callbacks and the redirect URIs its "
                "administrator lists in KEEN_MCP_REDIRECT_URIS",
            )
    remove_expired(db)
    waiting = (
        db.query(McpClient.id)
        .filter(~McpClient.id.in_(db.query(McpGrant.client_pk)))
        .limit(UNAPPROVED_CLIENT_LIMIT)
        .count()
    )
    if waiting >= UNAPPROVED_CLIENT_LIMIT:
        raise DomainError(
            429, "Too many clients are waiting for approval; try again later"
        )
    db.add(McpClient(client_id=client_id, client_name=name, info=info))
    db.commit()


def client_info(db: Session, client_id: str) -> dict | None:
    client = db.query(McpClient).filter_by(client_id=client_id).one_or_none()
    return client.info if client else None


def _client(db: Session, client_id: str) -> McpClient:
    return db.query(McpClient).filter_by(client_id=client_id).one()


# --- Consent ------------------------------------------------------------------


def create_consent_request(
    db: Session,
    *,
    client_id: str,
    scopes: list[str] | None,
    state: str | None,
    code_challenge: str,
    redirect_uri: str,
    redirect_uri_provided_explicitly: bool,
) -> str:
    """Record a client's authorisation request; return the consent page's key."""
    if not redirect_allowed(redirect_uri, settings.mcp_redirect_uris):
        # The administrator removed this callback after the client registered.
        raise DomainError(400, "This redirect URI is no longer accepted")
    remove_expired(db)
    secret = new_secret(CONSENT_PREFIX)
    db.add(
        McpConsentRequest(
            request_hash=digest(secret),
            client_pk=_client(db, client_id).id,
            scopes=scopes,
            state=state,
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
            expires_at=utc_now_naive() + timedelta(seconds=CONSENT_REQUEST_SECONDS),
        )
    )
    db.commit()
    return secret


def _consent_request(
    db: Session, secret: str, *, lock: bool = False
) -> McpConsentRequest:
    request = None
    if well_formed(secret, CONSENT_PREFIX):
        query = db.query(McpConsentRequest).filter(
            McpConsentRequest.request_hash == digest(secret),
            McpConsentRequest.expires_at > utc_now_naive(),
        )
        request = (query.with_for_update() if lock else query).one_or_none()
    if request is None:
        raise DomainError(
            404, "This request has expired. Start the connection again from your app."
        )
    return request


def _held(db: Session, user: User) -> set[str]:
    return get_effective_permission_codes(db, user, use_cache=False)


def consent_view(db: Session, *, secret: str, user: User) -> dict:
    """What the consent page shows the signed-in person."""
    request = _consent_request(db, secret)
    requested = request.scopes if request.scopes is not None else list(SCOPES)
    offered = grantable_scopes(_held(db, user), write=settings.mcp_write_enabled)
    return {
        "client_name": request.client.client_name,
        "redirect_uri": request.redirect_uri,
        "redirect_host": urlsplit(request.redirect_uri).netloc,
        "registered_at": request.client.created_at.isoformat(),
        "expires_at": request.expires_at.isoformat(),
        "mcp_url": mcp_url(),
        # Change scopes are listed apart and start unticked: a person turns
        # each on, whatever the app asked for.
        "scopes": [
            {
                "scope": code,
                "description": SCOPES[code][0],
                "requested": code in requested,
                "changes": code in WRITE_SCOPES,
                # The read scope a change scope brings with it.
                "includes": WRITE_SCOPES.get(code),
            }
            for code in offered
        ],
    }


def _with_query(uri: str, values: dict[str, str | None]) -> str:
    parts = urlsplit(uri)
    added = urlencode({key: value for key, value in values.items() if value})
    query = f"{parts.query}&{added}" if parts.query else added
    return urlunsplit(parts._replace(query=query))


def answer_consent(
    db: Session,
    *,
    secret: str,
    user: User,
    approve: bool,
    scopes: list[str],
    ip: str | None = None,
) -> dict:
    """Approve or refuse a request; return where the browser goes next.

    Approval grants the chosen scopes the account may grant, within those the
    client asked for; a change scope brings its read scope. The request is
    answered once.
    """
    request = _consent_request(db, secret, lock=True)
    redirect = {"state": request.state, "iss": issuer_url()}
    db.delete(request)
    if not approve:
        db.commit()
        return {
            "redirect": _with_query(
                request.redirect_uri, {**redirect, "error": "access_denied"}
            )
        }
    requested = request.scopes if request.scopes is not None else list(SCOPES)
    held, write = _held(db, user), settings.mcp_write_enabled
    chosen = granted_scopes(
        [code for code in scopes if code in requested], held, write=write
    )
    now = utc_now_naive()
    grant = McpGrant(
        user_id=user.id,
        client_pk=request.client_pk,
        # The read scope of each change scope granted, which the account holds.
        scopes=granted_scopes(with_read_scopes(chosen), held, write=write),
        session_version=user.session_version,
        mfa_version=user.mfa_version,
        expires_at=now + timedelta(seconds=GRANT_SECONDS),
    )
    db.add(grant)
    db.flush()
    code = new_secret(CODE_PREFIX)
    db.add(
        McpAuthorizationCode(
            code_hash=digest(code),
            grant_id=grant.id,
            code_challenge=request.code_challenge,
            redirect_uri=request.redirect_uri,
            redirect_uri_provided_explicitly=request.redirect_uri_provided_explicitly,
            expires_at=now + timedelta(seconds=AUTHORIZATION_CODE_SECONDS),
        )
    )
    # Someone holding the person's session could connect an app; the person
    # hears of each connection, as of other new ways into their account.
    notify(db, user, "mcp-connected", ip=ip)
    db.commit()
    return {"redirect": _with_query(request.redirect_uri, {"code": code, **redirect})}


# --- Grants, codes and tokens ---------------------------------------------------


def _live(grant: McpGrant, user: User | None, now: datetime) -> bool:
    return (
        settings.mcp_enabled
        and grant.revoked_at is None
        and grant.expires_at > now
        and user is not None
        and user.is_active
        and user.session_version == grant.session_version
        and user.mfa_version == grant.mfa_version
    )


def _revoke(db: Session, grant_id: uuid.UUID) -> None:
    db.query(McpGrant).filter(
        McpGrant.id == grant_id, McpGrant.revoked_at.is_(None)
    ).update({McpGrant.revoked_at: utc_now_naive()}, synchronize_session=False)
    db.commit()


def _issue(db: Session, grant: McpGrant, scopes: list[str]) -> IssuedTokens:
    now = utc_now_naive()
    access, refresh = new_secret(ACCESS_PREFIX), new_secret(REFRESH_PREFIX)
    access_expiry = min(now + timedelta(seconds=ACCESS_TOKEN_SECONDS), grant.expires_at)
    for value, kind, expires_at in (
        (access, "access", access_expiry),
        (
            refresh,
            "refresh",
            min(now + timedelta(seconds=REFRESH_TOKEN_SECONDS), grant.expires_at),
        ),
    ):
        db.add(
            McpToken(
                token_hash=digest(value),
                grant_id=grant.id,
                kind=kind,
                scopes=scopes,
                expires_at=expires_at,
            )
        )
    grant.last_used_at = now
    db.commit()
    return IssuedTokens(
        access_token=access,
        refresh_token=refresh,
        expires_in=int((access_expiry - now).total_seconds()),
        scopes=scopes,
    )


def authorization_code(db: Session, *, client_id: str, code: str) -> CodeRecord | None:
    """The code's record for ``client_id``, or None.

    A code presented after it was used revokes the grant it produced.
    """
    if not well_formed(code, CODE_PREFIX):
        return None
    # Lapsed records go before anything is spent, so a failed cleanup costs
    # the client a retry and never a spent code.
    remove_expired(db)
    row = db.query(McpAuthorizationCode).filter_by(code_hash=digest(code)).one_or_none()
    if row is None or row.grant.client.client_id != client_id:
        return None
    if row.used_at is not None:
        _revoke(db, row.grant_id)
        return None
    grant = row.grant
    if not _live(grant, db.get(User, grant.user_id), utc_now_naive()):
        return None
    return CodeRecord(
        client_id=client_id,
        user_id=str(grant.user_id),
        scopes=list(grant.scopes),
        code_challenge=row.code_challenge,
        redirect_uri=row.redirect_uri,
        redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
        expires_at=row.expires_at,
    )


def exchange_code(db: Session, *, code: str) -> IssuedTokens | None:
    """Spend a code checked by authorization_code; None if it was spent since."""
    row = db.query(McpAuthorizationCode).filter_by(code_hash=digest(code)).one_or_none()
    if row is None:  # Removed since it was checked: its grant ended
        return None
    spent = (
        db.query(McpAuthorizationCode)
        .filter(
            McpAuthorizationCode.id == row.id, McpAuthorizationCode.used_at.is_(None)
        )
        .update(
            {McpAuthorizationCode.used_at: utc_now_naive()}, synchronize_session=False
        )
    )
    if not spent:
        db.rollback()
        _revoke(db, row.grant_id)
        return None
    return _issue(db, row.grant, list(row.grant.scopes))


def _token_row(db: Session, value: str, prefix: str, kind: str) -> McpToken | None:
    if not well_formed(value, prefix):
        return None
    return (
        db.query(McpToken).filter_by(token_hash=digest(value), kind=kind).one_or_none()
    )


def _record(row: McpToken) -> TokenRecord:
    return TokenRecord(
        grant_id=str(row.grant_id),
        client_id=row.grant.client.client_id,
        user_id=str(row.grant.user_id),
        scopes=list(row.scopes),
        expires_at=row.expires_at,
    )


def refresh_token(db: Session, *, client_id: str, token: str) -> TokenRecord | None:
    """The refresh token's record for ``client_id``, or None.

    A refresh token presented after it was used revokes its grant.
    """
    if not well_formed(token, REFRESH_PREFIX):
        return None
    # A connection refreshes hourly; its lapsed tokens go before it spends one.
    remove_expired(db)
    row = _token_row(db, token, REFRESH_PREFIX, "refresh")
    now = utc_now_naive()
    if row is None or row.grant.client.client_id != client_id:
        return None
    if row.used_at is not None:
        _revoke(db, row.grant_id)
        return None
    if row.expires_at <= now or not _live(
        row.grant, db.get(User, row.grant.user_id), now
    ):
        return None
    return _record(row)


def exchange_refresh(
    db: Session, *, token: str, scopes: list[str]
) -> IssuedTokens | None:
    """Rotate a refresh token checked by refresh_token; None if spent since."""
    row = _token_row(db, token, REFRESH_PREFIX, "refresh")
    if row is None:  # Removed since it was checked: its grant ended
        return None
    spent = (
        db.query(McpToken)
        .filter(McpToken.id == row.id, McpToken.used_at.is_(None))
        .update({McpToken.used_at: utc_now_naive()}, synchronize_session=False)
    )
    if not spent:
        db.rollback()
        _revoke(db, row.grant_id)
        return None
    return _issue(db, row.grant, [code for code in row.scopes if code in scopes])


def access_token(db: Session, token: str) -> TokenRecord | None:
    """A current access token's record, or None. Records the grant's use."""
    row = _token_row(db, token, ACCESS_PREFIX, "access")
    now = utc_now_naive()
    if row is None or row.expires_at <= now:
        return None
    grant = row.grant
    if not _live(grant, db.get(User, grant.user_id), now):
        return None
    record = _record(row)
    if grant.last_used_at is None or grant.last_used_at < now - LAST_USED_RESOLUTION:
        grant.last_used_at = now
        db.commit()
    return record


def revoke_token(db: Session, token: str) -> None:
    """Revoke the grant an access or refresh token belongs to."""
    row = _token_row(db, token, ACCESS_PREFIX, "access") or _token_row(
        db, token, REFRESH_PREFIX, "refresh"
    )
    if row is not None:
        _revoke(db, row.grant_id)


def scoped_user(db: Session, token: str) -> tuple[User, TokenRecord] | None:
    """The account behind a current access token, narrowed to its scopes.

    The account acts with no role and with only the permissions that both the
    token's scopes and its current permissions allow; change scopes count only
    while KEEN_MCP_WRITE_ENABLED is set. Returns the account and the token's
    record.
    """
    record = access_token(db, token)
    if record is None:
        return None
    user = db.get(User, uuid.UUID(record.user_id))
    assert user is not None  # access_token checked the account is active
    allowed = granted_scopes(
        record.scopes, _held(db, user), write=settings.mcp_write_enabled
    )
    user.effective_role = ROLE_NORMAL
    user.effective_permission_codes = set(allowed)
    return user, record


# --- The account's own grants -------------------------------------------------


def grants_for(db: Session, *, user: User) -> dict:
    now = utc_now_naive()
    rows = (
        db.query(McpGrant)
        .filter(
            McpGrant.user_id == user.id,
            McpGrant.revoked_at.is_(None),
            McpGrant.expires_at > now,
            McpGrant.session_version == user.session_version,
            McpGrant.mfa_version == user.mfa_version,
        )
        .order_by(McpGrant.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "enabled": settings.mcp_enabled,
        "mcp_url": mcp_url(),
        "grants": [
            {
                "id": str(grant.id),
                "client_name": grant.client.client_name,
                "redirect_host": urlsplit(grant.client.info["redirect_uris"][0]).netloc,
                "scopes": list(grant.scopes),
                # The scopes that add and update records.
                "changes": [code for code in grant.scopes if code in WRITE_SCOPES],
                "created_at": grant.created_at.isoformat(),
                "last_used_at": grant.last_used_at.isoformat()
                if grant.last_used_at
                else None,
                "expires_at": grant.expires_at.isoformat(),
            }
            for grant in rows
        ],
    }


def revoke_own_grant(db: Session, *, user: User, grant_id: str) -> dict:
    try:
        ident = uuid.UUID(grant_id)
    except ValueError:
        ident = None
    grant = db.get(McpGrant, ident) if ident else None
    if grant is None or grant.user_id != user.id:
        raise DomainError(404, "Connection not found")
    _revoke(db, grant.id)
    return {"ok": True}
