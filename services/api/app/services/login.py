"""Session issuance after identity and factor verification."""

from __future__ import annotations

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.core.valkey import get_valkey
from app.security.permissions import get_effective_permission_codes
from app.security.roles import attach_effective_role
from app.security.sessions import create_session, delete_session
from app.services import security_notifications


def complete_local_login(
    db, user, *, existing_sid, client_ip, mfa_verified=False, verified_version=None
):
    """Issue a fresh session after password (and any MFA) verification.

    The browser loads its capabilities from /v1/me after signing in, so the
    response identifies the account only.
    """
    account_session_version = user.session_version
    session_version = user.mfa_version if verified_version is None else verified_version
    # Revoke the session the browser arrived with. This is best effort: a failed
    # revocation does not block sign-in, and the new session always gets a
    # fresh ID.
    if existing_sid:
        try:
            delete_session(get_valkey(), existing_sid)
        except Exception:
            pass

    # Best-effort last-login timestamp
    try:
        user.last_login_at = utc_now_naive()
        db.add(user)
        db.commit()
    except Exception:
        db.rollback()

    eff = attach_effective_role(db, user)
    permission_codes = get_effective_permission_codes(db, user, use_cache=False)
    authz_version = user.authz_version

    # Always issue a fresh session ID on sign-in to prevent session fixation.
    sid = create_session(
        get_valkey(),
        user_id=str(user.id),
        ttl_seconds=settings.session_ttl_seconds,
        auth_method=user.auth_backend or "local",
        mfa_version=session_version,
        session_version=account_session_version,
        mfa_verified=mfa_verified,
        effective_role=eff,
        permission_codes=permission_codes,
        authz_version=authz_version,
    )

    security_notifications.record_login(db, user, ip=client_ip)
    db.commit()

    return sid, {"user": user.username, "user_id": str(user.id)}


def complete_sso_login(db, user, *, provider, id_token, existing_sid, client_ip):
    account_session_version = user.session_version
    eff = attach_effective_role(db, user)
    permission_codes = get_effective_permission_codes(db, user, use_cache=False)
    authz_version = user.authz_version
    if existing_sid:
        delete_session(get_valkey(), existing_sid)
    sid = create_session(
        get_valkey(),
        user_id=str(user.id),
        ttl_seconds=settings.session_ttl_seconds,
        auth_method=provider.key,
        session_version=account_session_version,
        oidc_id_token=id_token,
        effective_role=eff,
        permission_codes=permission_codes,
        authz_version=authz_version,
    )

    security_notifications.record_login(db, user, ip=client_ip)
    db.commit()

    return sid
