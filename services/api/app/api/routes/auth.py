from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from app.api.routing import Router
from app.core.config import settings
from app.core.valkey import get_valkey
from app.db.models import User
from app.db.session import get_db
from app.schemas import LoginPayload
from app.security.auth import (
    ROLE_ADMIN,
    authenticate_user,
    clear_session_cookie,
    set_session_cookie,
)
from app.security.csrf import (
    clear_csrf_cookie,
    csrf_valid,
    generate_csrf_token,
    request_origin_allowed,
    set_csrf_cookie,
)
from app.security.oidc import (
    build_authorize_redirect,
    configured_sso_providers,
    get_sso_provider,
    handle_callback,
    set_sso_binding,
    sso_binding_cookie,
)
from app.security.permissions import has_permission
from app.security.rate_limit import client_ip as _client_ip
from app.security.rate_limit import fixed_window_allow
from app.security.sessions import get_session, revoke_session
from app.services import login as login_service

router = Router()


@router.get("/v1/auth/methods")
def auth_methods() -> dict:
    from app.security.demo import demo_metadata

    providers = configured_sso_providers()
    return {
        "demo": demo_metadata(),
        "local_enabled": bool(getattr(settings, "local_auth_enabled", True)),
        "ldap_enabled": settings.ldap_enabled,
        "remote_user_enabled": bool(
            getattr(settings, "trust_remote_user", False)
            and not getattr(settings, "oidc_enabled", False)
        ),
        # The hosted demo portal offers a demo once its OIDC sign-in works.
        "oidc_enabled": any(provider.key == "oidc" for provider in providers),
        "sso_enabled": bool(providers),
        "sso_providers": [provider.public_dict() for provider in providers],
    }


@router.post("/v1/auth/login")
def login(
    payload: LoginPayload, request: Request, db: Session = Depends(get_db)
) -> Response:
    from app.security.csrf import request_origin_allowed

    if not request_origin_allowed(request, require_origin=settings.hosted_mode):
        raise HTTPException(403, "Authentication request origin is not allowed")
    if not (
        settings.ldap_enabled
        if payload.method == "ldap"
        else settings.local_auth_enabled
    ):
        raise HTTPException(status_code=404, detail="Login method is disabled")

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

    if payload.method == "ldap":
        from app.security.ldap import authenticate_ldap

        user = authenticate_ldap(db, payload.username, payload.password)
    else:
        user = authenticate_user(db, payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    from app.security import mfa

    if user.mfa_enabled or mfa.required(db, user):
        return mfa.start(db, user, request)
    return complete_local_login(db, user, request)


def complete_local_login(
    db, user, request, *, mfa_verified=False, verified_version=None
):
    # Freeze the version proven by this ceremony before any commit expires ORM state.
    sid, result = login_service.complete_local_login(
        db,
        user,
        existing_sid=request.cookies.get(settings.session_cookie_name),
        client_ip=_client_ip(request),
        mfa_verified=mfa_verified,
        verified_version=verified_version,
    )
    response = JSONResponse(result)
    set_session_cookie(response, sid)
    set_csrf_cookie(response, generate_csrf_token())
    return response


@router.get("/v1/auth/sso/{provider_key}/start")
async def sso_start(
    provider_key: str, request: Request, db: Session = Depends(get_db)
) -> Response:
    next_url = request.query_params.get("next")
    url = await build_authorize_redirect(
        request, db, next_url=next_url, provider_key=provider_key
    )
    return set_sso_binding(RedirectResponse(url, status_code=302), request)


@router.get("/v1/auth/sso/{provider_key}/callback")
async def sso_callback(
    provider_key: str, request: Request, db: Session = Depends(get_db)
) -> Response:
    return await _complete_sso_callback(request, db, provider_key=provider_key)


# The hosted demo portal sends visitors to this start address and registers
# this callback with its identity provider. Both serve the OIDC provider.
@router.get("/v1/auth/oidc/start")
async def oidc_start(request: Request, db: Session = Depends(get_db)) -> Response:
    return await sso_start("oidc", request, db)


@router.get("/v1/auth/oidc/callback")
async def oidc_callback(request: Request, db: Session = Depends(get_db)) -> Response:
    return await _complete_sso_callback(request, db, provider_key="oidc")


async def _complete_sso_callback(
    request: Request, db: Session, *, provider_key: str
) -> Response:

    user, next_url, id_token, provider = await handle_callback(
        request, db, provider_key=provider_key
    )
    sid = login_service.complete_sso_login(
        db,
        user,
        provider=provider,
        id_token=id_token,
        existing_sid=request.cookies.get(settings.session_cookie_name),
        client_ip=_client_ip(request),
    )

    dest = next_url or "/"
    resp = RedirectResponse(dest, status_code=302)
    set_session_cookie(resp, sid)
    set_csrf_cookie(resp, generate_csrf_token())
    resp.delete_cookie(
        sso_binding_cookie(),
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _require_logout_intent(request):
    if request.cookies.get(settings.session_cookie_name):
        allowed = csrf_valid(request)
    else:
        # Pending MFA has a separate credential and may not have a CSRF cookie.
        allowed = request_origin_allowed(request, require_origin=True)
    if not allowed:
        raise HTTPException(403, "CSRF token missing or invalid")


def _revoke_or_fail(sid: str) -> None:
    """Revoke the server-side session before reporting sign-out.

    The browser keeps its cookies when revocation fails so the user can retry;
    clearing them would hide a session that remains valid on the server.
    """
    if not sid:
        return
    try:
        revoke_session(get_valkey(), sid)
    except Exception:
        raise HTTPException(
            503,
            "Sign-out could not be completed; try again",
            headers={"Retry-After": "5", "Cache-Control": "no-store"},
        ) from None


@router.post("/v1/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)) -> Response:
    _require_logout_intent(request)
    sid = (request.cookies.get(settings.session_cookie_name) or "").strip()
    _revoke_or_fail(sid)
    resp = Response(status_code=204)
    from app.security import mfa

    mfa.cancel(db, request, resp)
    clear_session_cookie(resp)
    clear_csrf_cookie(resp)
    return resp


def _browser_logout_response(request: Request, db: Session) -> Response:
    _require_logout_intent(request)
    sid = (request.cookies.get(settings.session_cookie_name) or "").strip()
    try:
        session_row = get_session(get_valkey(), sid) if sid else None
    except Exception:
        raise HTTPException(
            503,
            "Sign-out could not be completed; try again",
            headers={"Retry-After": "5", "Cache-Control": "no-store"},
        ) from None
    id_token = str((session_row or {}).get("oidc_id_token") or "").strip()
    provider = get_sso_provider(str((session_row or {}).get("auth_method") or "oidc"))

    _revoke_or_fail(sid)

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
        resp = JSONResponse({"logout_url": f"{end_session}?{qs}"})
    else:
        resp = JSONResponse({"logout_url": post_logout or "/login.html"})

    from app.security import mfa

    mfa.cancel(db, request, resp)
    clear_session_cookie(resp)
    clear_csrf_cookie(resp)
    return resp


@router.get("/v1/auth/logout")
def browser_logout(request: Request, db: Session = Depends(get_db)) -> Response:
    return RedirectResponse("/account.html", status_code=303)


@router.get("/v1/auth/oidc/logout")
def oidc_logout(request: Request, db: Session = Depends(get_db)) -> Response:
    return RedirectResponse("/account.html", status_code=303)


@router.post("/v1/auth/oidc/logout")
def oidc_logout_post(request: Request, db: Session = Depends(get_db)) -> Response:
    """Revoke the local session and return the provider logout destination."""
    return _browser_logout_response(request, db)


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
