from __future__ import annotations

import uuid

from collections.abc import Mapping

from fastapi import HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.valkey import get_valkey
from app.db.models import User
from app.security.passwords import verify_password, hash_password
from app.security.sessions import delete_session, get_session

ROLE_ADMIN = "admin"
ROLE_NORMAL = "normal"
ROLE_INHERIT = "inherit"


def _remote_user_from_headers(headers: Mapping[str, str]) -> str | None:
    """Extract a trusted remote user identity from headers.

    This is intended for deployments where an upstream reverse proxy sets an
    identity header (e.g. REMOTE_USER) after authenticating the user.

    IMPORTANT: Only enable this feature (KEEN_TRUST_REMOTE_USER=true) when
    you have a trusted proxy that *strips* any client-supplied REMOTE_USER
    header and injects its own.
    """

    if not bool(getattr(settings, "trust_remote_user", False)):
        return None

    # Native OIDC must not be bypassable by a request header. If OIDC is enabled,
    # ignore REMOTE_USER-style headers entirely and require KEEN's own session
    # cookie that was minted after validating the configured SSO identity.
    if bool(getattr(settings, "oidc_enabled", False)):
        return None

    header_name = (
        getattr(settings, "remote_user_header_name", "") or "REMOTE_USER"
    ).strip()
    if not header_name:
        header_name = "REMOTE_USER"

    raw = headers.get(header_name)
    if raw is None:
        return None

    val = str(raw).strip()
    if not val:
        return None

    # Basic hardening: reject control characters/newlines.
    if any(ord(c) < 32 or ord(c) == 127 for c in val):
        return None

    return val


def _remote_user_from_request(request: Request) -> str | None:
    # Backwards-compatible wrapper.
    return _remote_user_from_headers(request.headers)


def cookie_name() -> str:
    return settings.session_cookie_name


def _session_id_from_request(request: Request) -> str | None:
    sid = request.cookies.get(cookie_name())
    return sid.strip() if isinstance(sid, str) and sid.strip() else None


def get_current_user_from_headers_cookies(
    headers: Mapping[str, str], cookies: Mapping[str, str], db: Session
) -> User | None:
    """Return the current user, or None if not authenticated.

    This helper is used by both HTTP requests and WebSocket handshakes.
    """

    # 1) Trusted upstream identity header (optional).
    remote_username = _remote_user_from_headers(headers)
    if remote_username:
        user = db.query(User).filter(User.username == remote_username).one_or_none()
        if user and user.is_active:
            return user
        return None

    # 2) Cookie-backed session.
    sid = cookies.get(cookie_name())
    sid = sid.strip() if isinstance(sid, str) else None
    if not sid:
        return None

    r = get_valkey()
    sess = get_session(r, sid, refresh_ttl_seconds=settings.session_ttl_seconds)
    if not sess or not sess.get("user_id"):
        return None

    try:
        uid = uuid.UUID(str(sess["user_id"]))
    except Exception:
        return None

    user = db.query(User).filter(User.id == uid).one_or_none()
    if not user or not user.is_active:
        # Stale session; best effort delete
        delete_session(r, sid)
        return None

    # Attach the server-side session authorisation snapshot to this ORM instance.
    # The middleware/route-level permission helpers will validate the authz
    # version before trusting it, so this remains a cache rather than an
    # authority separate from the DB.
    try:
        setattr(user, "session_id", sid)
        setattr(user, "session_authz_version", sess.get("authz_version"))
        if sess.get("effective_role"):
            setattr(user, "effective_role", str(sess.get("effective_role")))
        codes = sess.get("permission_codes")
        if isinstance(codes, list):
            setattr(user, "effective_permission_codes", {str(c) for c in codes if c})
    except Exception:
        pass

    return user


def get_current_user_from_request(request: Request, db: Session) -> User | None:
    return get_current_user_from_headers_cookies(request.headers, request.cookies, db)


def require_authenticated(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request) -> User:
    user = require_authenticated(request)
    eff = getattr(user, "effective_role", None) or getattr(user, "role", None)
    if (eff or "") != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def set_session_cookie(response: Response, session_id: str) -> None:
    # NOTE: cookie_samesite must be one of: lax|strict|none
    samesite = (settings.cookie_samesite or "lax").lower()
    if samesite not in ("lax", "strict", "none"):
        samesite = "lax"

    domain = (settings.cookie_domain or "").strip() or None

    response.set_cookie(
        key=cookie_name(),
        value=session_id,
        max_age=int(settings.session_ttl_seconds),
        httponly=True,
        secure=bool(settings.cookie_secure),
        samesite=samesite,
        domain=domain,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    domain = (settings.cookie_domain or "").strip() or None
    response.delete_cookie(key=cookie_name(), domain=domain, path="/")


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    u = (username or "").strip()
    if not u:
        return None
    user = db.query(User).filter(User.username == u).one_or_none()
    if not user or not user.is_active:
        return None
    if not verify_password(password or "", user.password_hash):
        return None
    return user


def create_user(
    db: Session, *, username: str, password: str, role: str, email: str | None = None
) -> User:
    uname = (username or "").strip()
    if not uname:
        raise ValueError("username required")

    r = (role or ROLE_NORMAL).strip() or ROLE_NORMAL
    if r not in (ROLE_ADMIN, ROLE_NORMAL, ROLE_INHERIT):
        raise ValueError("role must be 'admin', 'normal', or 'inherit'")

    existing = db.query(User).filter(User.username == uname).one_or_none()
    if existing:
        raise ValueError("username already exists")

    user = User(
        username=uname,
        password_hash=hash_password(password),
        email=(email or "").strip() or None,
        role=r,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
