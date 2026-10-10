"""Local MFA primitives. Pending challenges never enter the session namespace."""

import base64
import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from app.core.config import settings
from app.core.valkey import get_valkey
from app.db.models import AuditLog, MfaChallenge, MfaCredential, MfaRecoveryCode, User
from app.security.rate_limit import client_ip, fixed_window_allow
from app.security.roles import attach_effective_role

COOKIE = "keen_mfa_pending"
TTL = 300


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def origin_config():
    origin = (settings.mfa_origin or settings.public_base_url).rstrip("/")
    parsed = urlsplit(origin)
    if (
        (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1"}
            )
        )
        or not parsed.hostname
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(
            503, "Set KEEN_MFA_ORIGIN to the HTTPS origin of this KEEN installation"
        )
    rp = settings.mfa_rp_id or parsed.hostname
    # Exact host scope avoids accidental trust of sibling customer subdomains.
    if rp != parsed.hostname:
        raise HTTPException(503, "KEEN_MFA_RP_ID must match the KEEN origin hostname")
    return origin, rp


def check_origin(request):
    if not (settings.local_auth_enabled or settings.ldap_enabled):
        raise HTTPException(404, "Password authentication is disabled")
    origin, _ = origin_config()
    if request.headers.get("origin") != origin:
        raise HTTPException(403, "Authentication request origin is not allowed")


def cipher():
    try:
        return Fernet(settings.mfa_encryption_key.encode())
    except (ValueError, TypeError):
        raise HTTPException(503, "TOTP requires a valid KEEN_MFA_ENCRYPTION_KEY")


def decrypt(secret):
    try:
        return cipher().decrypt(secret.encode()).decode()
    except InvalidToken:
        raise HTTPException(
            503, "TOTP secret cannot be decrypted; restore the MFA encryption key"
        )


def required(db, user):
    return settings.mfa_policy == "all" or (
        settings.mfa_policy == "admins" and attach_effective_role(db, user) == "admin"
    )


def local_session_allowed(db, user, session):
    if session.get("auth_method", "local") not in {"local", "ldap"}:
        return True
    if int(session.get("mfa_version", 0)) != user.mfa_version:
        return False
    if user.mfa_enabled or required(db, user):
        return bool(user.mfa_enabled and session.get("mfa_verified") is True)
    return True


def limit(request, user_id=None):
    try:
        r = get_valkey()
        for key, maximum in [
            (f"ip:{client_ip(request) or 'unknown'}", 40),
            (f"user:{user_id}", 15),
        ]:
            if user_id is None and key.startswith("user:"):
                continue
            allowed, _ = fixed_window_allow(
                r, "keen:rl:mfa:" + key, maximum, 300, fail_closed=True
            )
            if not allowed:
                raise HTTPException(
                    429, "Too many verification attempts; try again later"
                )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Authentication rate limiter unavailable")


def audit(db, user, action, request=None):
    if action in {
        "factor-enrolled",
        "factor-removed",
        "recovery-codes-regenerated",
        "operator-reset",
    }:
        from app.services.security_notifications import enqueue

        enqueue(db, user, action, request)
    db.add(
        AuditLog(
            username=user.username,
            method="MFA",
            path="/v1/auth/mfa/" + action,
            status_code=200,
            duration_ms=0,
            client_ip=client_ip(request) if request else None,
        )
    )


def start(db, user, request, purpose="login"):
    check_origin(request)
    # Serialize enrolment/reset/login state changes on the authoritative user row.
    password_before_lock = user.password_hash
    user = (
        db.query(User)
        .filter(User.id == user.id)
        .populate_existing()
        .with_for_update()
        .one()
    )
    if user.password_hash != password_before_lock or not user.is_active:
        raise HTTPException(401, "Account changed; sign in again")
    token = secrets.token_urlsafe(32)
    # Bound outstanding challenges per account and invalidate older browser flows.
    db.query(MfaChallenge).filter(MfaChallenge.user_id == user.id).delete(
        synchronize_session=False
    )
    stage = (
        "verify" if user.mfa_enabled else ("enrol" if purpose == "login" else "manage")
    )
    row = MfaChallenge(
        token_hash=digest(token),
        user_id=user.id,
        purpose=purpose,
        stage=stage,
        version=user.mfa_version,
        password_digest=digest(user.password_hash),
        expires_at=now() + timedelta(seconds=TTL),
        data={},
    )
    db.add(row)
    db.commit()
    from fastapi.responses import JSONResponse

    response = JSONResponse({"mfa_required": True, "next": "/mfa.html"})
    response.set_cookie(
        COOKIE,
        token,
        max_age=TTL,
        httponly=True,
        secure=origin_config()[0].startswith("https://"),
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def pending(db, request):
    check_origin(request)
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 128:
        raise HTTPException(401, "Verification expired; start again")
    # Consistent lock order: user first, then challenge; serializes codes/changes.
    row = db.get(MfaChallenge, digest(token))
    if row is None:
        raise HTTPException(401, "Verification expired; start again")
    user = (
        db.query(User)
        .filter(User.id == row.user_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    row = (
        db.query(MfaChallenge)
        .filter(MfaChallenge.token_hash == digest(token))
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if (
        not user
        or not user.is_active
        or not row
        or row.expires_at <= now()
        or row.version != user.mfa_version
        or row.password_digest != digest(user.password_hash)
    ):
        raise HTTPException(401, "Verification expired; start again")
    return row, user


def management(row):
    if row.stage not in {"enrol", "manage"}:
        raise HTTPException(403, "Verify your existing second factor first")


def verified(row):
    row.stage = "manage" if row.purpose == "manage" else "ready"
    row.data = {}  # consume any WebAuthn or pending TOTP challenge


def bump(db, user, row=None):
    user.mfa_version += 1
    if row is not None:
        row.version = user.mfa_version
    db.flush()


def recovery_codes(db, user):
    db.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id == user.id).delete(
        synchronize_session=False
    )
    codes = [
        "-".join(secrets.token_hex(4).upper() for _ in range(4)) for _ in range(10)
    ]
    db.add_all(
        [
            MfaRecoveryCode(user_id=user.id, code_hash=digest(c.replace("-", "")))
            for c in codes
        ]
    )
    return codes


def verify_code(db, user, value, kind):
    if kind == "recovery":
        code = value.strip().replace("-", "").replace(" ", "").upper()
        if len(code) != 32:
            return False
        return (
            db.query(MfaRecoveryCode)
            .filter(
                MfaRecoveryCode.user_id == user.id,
                MfaRecoveryCode.code_hash == digest(code),
            )
            .delete(synchronize_session=False)
            == 1
        )
    if (
        kind != "totp"
        or not user.mfa_totp_secret
        or len(value) != 6
        or not value.isascii()
        or not value.isdigit()
    ):
        return False
    totp = pyotp.TOTP(decrypt(user.mfa_totp_secret))
    current = int(time.time()) // 30
    for step in (current, current - 1, current + 1):
        if step > user.mfa_totp_last_step and secrets.compare_digest(
            totp.at(step * 30), value
        ):
            user.mfa_totp_last_step = step
            return True
    return False


def registered(db, user):
    return (
        db.query(MfaCredential)
        .filter(MfaCredential.user_id == user.id)
        .order_by(MfaCredential.created_at)
        .all()
    )


def activate(db, row, user, request):
    first = not user.mfa_enabled
    user.mfa_enabled = True
    bump(db, user, row)
    codes = recovery_codes(db, user) if first else None
    if row.purpose == "login":
        row.stage = "ready"
    row.data = {}
    audit(db, user, "factor-enrolled", request)
    db.commit()
    return {"ok": True, "recovery_codes": codes}


def check_client_data(credential, user=None):
    try:
        if len(json.dumps(credential)) > 65536:
            raise ValueError()
        client = json.loads(unb64(credential["response"]["clientDataJSON"]))
        if client.get("crossOrigin") or client.get("topOrigin"):
            raise ValueError()
        handle = credential["response"].get("userHandle")
        if handle and user is not None and unb64(handle) != user.id.bytes:
            raise ValueError()
    except Exception:
        raise HTTPException(400, "Invalid WebAuthn response")


def cancel(db, request, response):
    token = request.cookies.get(COOKIE, "")
    if token and len(token) <= 128:
        db.query(MfaChallenge).filter(MfaChallenge.token_hash == digest(token)).delete(
            synchronize_session=False
        )
        db.commit()
    response.delete_cookie(COOKIE, path="/")
    return response
