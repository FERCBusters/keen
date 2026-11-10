"""Sign-in verification, its audit records and session issuance."""

from __future__ import annotations

from sqlalchemy import func

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.core.valkey import get_valkey
from app.db.models import User, UserIdentity
from app.security.auth import authenticate_user
from app.security.ldap import authenticate_ldap
from app.security.permissions import get_effective_permission_codes
from app.security.roles import attach_effective_role
from app.security.sessions import create_session, delete_session
from app.services import security_notifications
from app.services.activity_log import record_action
from app.services.context import ChangeContext
from app.services.errors import DomainError


def record_sign_in(
    db, context: ChangeContext, *, method: str, outcome: str, account=None, **meta
):
    """Add the audit row for one sign-in attempt; the caller commits.

    ``account`` is the account the attempt named or reached. A rejected
    username that matches no account is never stored: it is often a password
    typed into the wrong field.
    """
    record_action(
        db, context, account, "sign-in", {"method": method, "outcome": outcome, **meta}
    )


def record_refused_sign_in(
    db, context: ChangeContext, *, method: str, account_id=None, outcome="failed"
):
    """Audit a refused sign-in; only the audit row is committed."""
    db.rollback()
    account = db.get(User, account_id) if account_id is not None else None
    record_sign_in(db, context, method=method, outcome=outcome, account=account)
    db.commit()


def _attempted_account(db, username: str, method: str) -> User | None:
    """The existing account a rejected username names, if any."""
    name = username.strip()
    if method == "ldap":
        matches = (
            db.query(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .filter(
                UserIdentity.provider == "ldap",
                func.lower(UserIdentity.preferred_username) == name.lower(),
            )
            .limit(2)
            .all()
        )
        return matches[0] if len(matches) == 1 else None
    return db.query(User).filter(User.username == name).one_or_none()


def authenticate_password(
    db,
    context: ChangeContext,
    *,
    username: str,
    password: str,
    method: str,
    recognised: bool = False,
) -> User:
    """Verify a local or directory password, auditing a rejection.

    Every rejection costs the same lookup and audit write whether or not the
    username names an account, and all of them receive the same answer.
    """
    if method == "ldap":
        user = authenticate_ldap(db, username, password, recognised=recognised)
    else:
        user = authenticate_user(db, username, password)
    if user is None:
        # A rejected attempt has written nothing, so the audit row commits alone.
        record_sign_in(
            db,
            context,
            method=method,
            outcome="failed",
            account=_attempted_account(db, username, method),
        )
        db.commit()
        raise DomainError(401, "Invalid username or password")
    return user


def complete_local_login(
    db,
    user,
    *,
    existing_sid,
    context: ChangeContext,
    mfa_verified=False,
    verified_version=None,
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
    method = user.auth_backend or "local"
    sid = create_session(
        get_valkey(),
        user_id=str(user.id),
        ttl_seconds=settings.session_ttl_seconds,
        auth_method=method,
        mfa_version=session_version,
        session_version=account_session_version,
        mfa_verified=mfa_verified,
        effective_role=eff,
        permission_codes=permission_codes,
        authz_version=authz_version,
    )

    security_notifications.record_login(db, user, ip=context.client_ip)
    extra = {"mfa": "verified"} if mfa_verified else {}
    record_sign_in(
        db, context, method=method, outcome="signed-in", account=user, **extra
    )
    db.commit()

    return sid, {"user": user.username, "user_id": str(user.id)}


def complete_sso_login(db, user, *, provider, id_token, existing_sid, context):
    # Read the active state and session version together under the row lock: a
    # deactivation committed since the identity check then refuses the sign-in,
    # and one that waits for this lock advances the version issued here.
    user = (
        db.query(User)
        .filter(User.id == user.id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if user is None or not user.is_active:
        raise DomainError(403, "User is inactive")
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

    security_notifications.record_login(db, user, ip=context.client_ip)
    record_sign_in(db, context, method=provider.key, outcome="signed-in", account=user)
    db.commit()

    return sid
