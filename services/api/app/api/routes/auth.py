from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.valkey import get_valkey
from app.db.models import User
from app.db.session import get_db
from app.security.auth import (
    ROLE_ADMIN,
    authenticate_user,
    clear_session_cookie,
    set_session_cookie,
)
from app.security.permissions import get_effective_permission_codes, has_permission
from app.security.roles import attach_effective_role
from app.security.csrf import clear_csrf_cookie, generate_csrf_token, set_csrf_cookie
from app.security.sessions import (
    create_session,
    delete_session,
    get_session,
)
from app.security.rate_limit import client_ip as _client_ip, fixed_window_allow
from app.security.oidc import (
    any_sso_enabled,
    build_authorize_redirect,
    configured_sso_providers,
    get_sso_provider,
    handle_callback,
)

from app.api.payloads import LoginPayload

router = APIRouter()


def _oidc_configured() -> bool:
    return any(provider.key == "oidc" for provider in configured_sso_providers())


@router.get("/v1/auth/methods")
def auth_methods() -> dict:
    providers = configured_sso_providers()
    oidc_enabled = _oidc_configured()
    oidc_provider = get_sso_provider("oidc")
    return {
        "local_enabled": bool(getattr(settings, "local_auth_enabled", True)),
        "remote_user_enabled": bool(
            getattr(settings, "trust_remote_user", False)
            and not getattr(settings, "oidc_enabled", False)
        ),
        "oidc_enabled": oidc_enabled,
        "oidc": (
            {
                "label": (
                    oidc_provider.label if oidc_provider else "Single sign-on"
                ),
                "start_url": "/api/v1/auth/oidc/start",
            }
            if oidc_enabled
            else None
        ),
        "sso_enabled": bool(providers),
        "sso_providers": [provider.public_dict() for provider in providers],
    }


@router.post("/v1/auth/login")
def login(
    payload: LoginPayload, request: Request, db: Session = Depends(get_db)
) -> Response:
    if not bool(getattr(settings, "local_auth_enabled", True)):
        raise HTTPException(status_code=404, detail="Local login is disabled")

    # Defense-in-depth: rate limit login attempts (also enforce at reverse proxy).
    # Fail closed on Redis unavailability to prevent brute force attacks.
    try:
        r = get_valkey()
        window = int(settings.login_rate_limit_window_seconds)
        per_ip = int(settings.login_rate_limit_per_ip)
        per_user = int(settings.login_rate_limit_per_username)

        ip = _client_ip(request) or "unknown"
        uname = (payload.username or "").strip().lower() or "unknown"

        if per_ip > 0:
            ok, retry = fixed_window_allow(
                r, f"keen:rl:login:ip:{ip}", per_ip, window, fail_closed=True
            )
            if not ok:
                resp = JSONResponse(
                    {"detail": "Too many login attempts. Please try again later."},
                    status_code=429,
                )
                resp.headers["Retry-After"] = str(retry)
                return resp

        if per_user > 0 and uname != "unknown":
            ok, retry = fixed_window_allow(
                r, f"keen:rl:login:user:{uname}", per_user, window, fail_closed=True
            )
            if not ok:
                resp = JSONResponse(
                    {"detail": "Too many login attempts. Please try again later."},
                    status_code=429,
                )
                resp.headers["Retry-After"] = str(retry)
                return resp
    except Exception:
        # If Redis is unavailable, fail closed and deny login.
        resp = JSONResponse(
            {"detail": "Service temporarily unavailable. Please try again later."},
            status_code=503,
        )
        return resp

    user = authenticate_user(db, payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Delete any existing session for this user to prevent session fixation.
    existing_sid = request.cookies.get(settings.session_cookie_name)
    if existing_sid:
        try:
            delete_session(get_valkey(), existing_sid)
        except Exception:
            pass

    # Best-effort last-login timestamp
    try:
        user.last_login_at = datetime.utcnow()
        db.add(user)
        db.commit()
    except Exception:
        db.rollback()

    eff = attach_effective_role(db, user)
    permission_codes = get_effective_permission_codes(db, user, use_cache=False)
    authz_version = int(getattr(user, "authz_version", 0) or 0)

    # Create new Valkey-backed session + set cookie.
    # Always create a fresh session ID on login to prevent session fixation attacks.
    sid = create_session(
        get_valkey(),
        user_id=str(user.id),
        ttl_seconds=int(settings.session_ttl_seconds),
        auth_method="local",
        effective_role=eff,
        permission_codes=permission_codes,
        authz_version=authz_version,
    )
    try:
        setattr(user, "session_id", sid)
        setattr(user, "session_authz_version", authz_version)
        setattr(user, "effective_permission_codes", set(permission_codes))
    except Exception:
        pass

    can_manage_audits = has_permission(db, user, "audits.manage")
    can_view_audits = can_manage_audits or has_permission(db, user, "audits.read")
    can_manage_risks = has_permission(db, user, "risk.manage")
    can_view_risks = can_manage_risks or has_permission(db, user, "risk.read")
    can_manage_interested_parties = bool(
        has_permission(db, user, "interested_parties.manage") or can_manage_risks
    )
    can_view_interested_parties = bool(
        can_manage_interested_parties
        or has_permission(db, user, "interested_parties.read")
        or can_view_risks
    )
    can_manage_isms = bool(has_permission(db, user, "isms.manage") or can_manage_risks)
    can_view_isms = bool(
        can_manage_isms or has_permission(db, user, "isms.read") or can_view_risks
    )
    can_view_events = bool(has_permission(db, user, "events.read"))
    can_create_questions = bool(has_permission(db, user, "question.create"))
    can_delete_questions = bool(has_permission(db, user, "question.delete"))
    incident_webhook_enabled = bool(
        (getattr(settings, "incident_webhook_url", "") or "").strip()
    )
    can_create_incidents = bool(
        incident_webhook_enabled and has_permission(db, user, "incident.create")
    )
    can_delete_incidents = bool(has_permission(db, user, "incident.delete"))

    resp = JSONResponse(
        {
            "user": user.username,
            "role": user.role,
            "effective_role": eff,
            "is_admin": (eff or "") == ROLE_ADMIN,
            "can_view_audits": can_view_audits,
            "can_manage_audits": can_manage_audits,
            "can_view_risks": can_view_risks,
            "can_manage_risks": can_manage_risks,
            "can_view_interested_parties": can_view_interested_parties,
            "can_manage_interested_parties": can_manage_interested_parties,
            "can_view_isms": can_view_isms,
            "can_manage_isms": can_manage_isms,
            "can_view_events": can_view_events,
            "can_create_questions": can_create_questions,
            "can_question_events": can_create_questions,
            "can_delete_questions": can_delete_questions,
            "incident_webhook_enabled": incident_webhook_enabled,
            "can_create_incidents": can_create_incidents,
            "can_delete_incidents": can_delete_incidents,
            "sample_pdf_footer": (settings.sample_pdf_footer or "").strip() or None,
            "event_data_masking": settings.event_data_masking,
        }
    )
    set_session_cookie(resp, sid)
    # Provide a CSRF token for the UI to echo in a header on unsafe methods.
    set_csrf_cookie(resp, generate_csrf_token())
    return resp


@router.get("/v1/auth/oidc/start")
async def oidc_start(request: Request, db: Session = Depends(get_db)) -> Response:
    next_url = request.query_params.get("next")
    url = await build_authorize_redirect(request, db, next_url=next_url, provider_key="oidc")
    return RedirectResponse(url, status_code=302)


@router.get("/v1/auth/oidc/callback")
async def oidc_callback(request: Request, db: Session = Depends(get_db)) -> Response:
    return await _complete_sso_callback(request, db, provider_key="oidc")


@router.get("/v1/auth/sso/{provider_key}/start")
async def sso_start(provider_key: str, request: Request, db: Session = Depends(get_db)) -> Response:
    next_url = request.query_params.get("next")
    url = await build_authorize_redirect(request, db, next_url=next_url, provider_key=provider_key)
    return RedirectResponse(url, status_code=302)


@router.get("/v1/auth/sso/{provider_key}/callback")
async def sso_callback(provider_key: str, request: Request, db: Session = Depends(get_db)) -> Response:
    return await _complete_sso_callback(request, db, provider_key=provider_key)


async def _complete_sso_callback(request: Request, db: Session, *, provider_key: str) -> Response:

    user, next_url, id_token, provider = await handle_callback(request, db, provider_key=provider_key)
    eff = attach_effective_role(db, user)
    permission_codes = get_effective_permission_codes(db, user, use_cache=False)
    authz_version = int(getattr(user, "authz_version", 0) or 0)
    sid = create_session(
        get_valkey(),
        user_id=str(user.id),
        ttl_seconds=int(settings.session_ttl_seconds),
        auth_method=provider.key,
        oidc_id_token=id_token,
        effective_role=eff,
        permission_codes=permission_codes,
        authz_version=authz_version,
    )

    dest = next_url or "/"
    resp = RedirectResponse(dest, status_code=302)
    set_session_cookie(resp, sid)
    set_csrf_cookie(resp, generate_csrf_token())
    return resp


@router.post("/v1/auth/logout")
def logout(request: Request) -> Response:
    sid = request.cookies.get(settings.session_cookie_name) or ""
    if sid:
        delete_session(get_valkey(), sid)
    resp = Response(status_code=204)
    clear_session_cookie(resp)
    clear_csrf_cookie(resp)
    return resp


def _browser_logout_response(request: Request) -> Response:
    sid = (request.cookies.get(settings.session_cookie_name) or "").strip()
    session_row = get_session(get_valkey(), sid) if sid else None
    id_token = str((session_row or {}).get("oidc_id_token") or "").strip()
    provider = get_sso_provider(str((session_row or {}).get("auth_method") or "oidc"))

    if sid:
        delete_session(get_valkey(), sid)

    end_session = (provider.end_session_endpoint if provider else "").strip()
    post_logout = (provider.post_logout_redirect_uri if provider else "").strip()

    if end_session and id_token and post_logout:
        qs = urlencode(
            {
                "id_token_hint": id_token,
                "post_logout_redirect_uri": post_logout,
            },
            safe=":/?&=",
        )
        resp = RedirectResponse(f"{end_session}?{qs}", status_code=302)
    else:
        resp = RedirectResponse(post_logout or "/login.html", status_code=302)

    clear_session_cookie(resp)
    clear_csrf_cookie(resp)
    return resp


@router.get("/v1/auth/logout")
def browser_logout(request: Request) -> Response:
    return _browser_logout_response(request)


@router.get("/v1/auth/oidc/logout")
def oidc_logout(request: Request) -> Response:
    """Clear KEEN's session and start RP-initiated logout when available."""

    return _browser_logout_response(request)


@router.get("/v1/auth/check")
def auth_check(request: Request) -> Response:
    # Used by nginx auth_request to gate access to HTML pages.
    # auth_middleware has already authenticated the request (session cookie or
    # trusted REMOTE_USER header when native OIDC is disabled).
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")

    resp = Response(status_code=204)
    # Optional metadata headers. Nginx *may* read these from the auth_request subrequest
    # response (via $upstream_http_x_keen_user / $upstream_http_x_keen_role) for
    # logging/diagnostics. Admin gating in this codebase uses the /v1/auth/check-admin
    # status code, not these headers.
    resp.headers["X-Keen-User"] = user.username
    resp.headers["X-Keen-Role"] = getattr(user, "effective_role", None) or user.role
    return resp


@router.get("/v1/auth/check-admin")
def auth_check_admin(request: Request, db: Session = Depends(get_db)) -> Response:
    """Used by nginx auth_request to hide the admin UI.

    Returns:
      - 204 if authenticated AND (effective_role==admin OR user has audittrail.read)
      - 401 if not authenticated
      - 403 if authenticated but not allowed
    """

    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")

    eff = getattr(user, "effective_role", None) or user.role
    if (eff or "") != ROLE_ADMIN:
        if not has_permission(db, user, "audittrail.read"):
            raise HTTPException(
                status_code=403, detail="Admin or audit access required"
            )

    resp = Response(status_code=204)
    # Optional metadata headers (see /v1/auth/check).
    resp.headers["X-Keen-User"] = user.username
    resp.headers["X-Keen-Role"] = eff
    return resp
