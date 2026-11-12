from __future__ import annotations

from functools import partial
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from app.api.context import change_context
from app.api.routing import Router
from app.core.config import settings
from app.core.valkey import get_valkey
from app.db.session import get_db
from app.schemas import LoginPayload
from app.security import sign_in_throttle as throttle
from app.security.auth import (
    ROLE_ADMIN,
    clear_session_cookie,
    require_authenticated,
    set_session_cookie,
)
from app.security.auth import cookie_name as session_cookie_name
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
    linked_account_id,
    set_sso_binding,
    sso_attempt,
    sso_binding_cookie,
)
from app.security.rate_limit import address_window_allow
from app.security.rate_limit import client_ip as _client_ip
from app.security.sessions import get_session, revoke_session
from app.services import login as login_service
from app.services.errors import DomainError

router = Router()


@router.get("/v1/auth/methods")
def auth_methods() -> dict:
    from app.security.demo import demo_metadata

    providers = configured_sso_providers()
    return {
        "demo": demo_metadata(),
        "local_enabled": settings.local_auth_enabled,
        "ldap_enabled": settings.ldap_enabled,
        "remote_user_enabled": settings.trust_remote_user and not settings.oidc_enabled,
        # The hosted demo portal offers a demo once its OIDC sign-in works.
        "oidc_enabled": any(provider.key == "oidc" for provider in providers),
        "sso_enabled": bool(providers),
        "sso_providers": [provider.public_dict() for provider in providers],
    }


@router.post("/v1/auth/login")
def login(
    payload: LoginPayload, request: Request, db: Session = Depends(get_db)
) -> Response:
    if not request_origin_allowed(request, require_origin=settings.hosted_mode):
        raise HTTPException(403, "Authentication request origin is not allowed")
    if not (
        settings.ldap_enabled
        if payload.method == "ldap"
        else settings.local_auth_enabled
    ):
        raise HTTPException(status_code=404, detail="Login method is disabled")

    # Sign-in throttling (app/security/sign_in_throttle.py, shared with
    # OSPMAE): a browser that has proved this account's password is heard
    # whatever an attacker does elsewhere; every other browser draws on the
    # per-address allowance and the account's growing waits. Fails closed
    # while Valkey is unavailable.
    key = throttle.name_key(payload.username)
    try:
        r = get_valkey()
        device = throttle.recognised_device(
            r, request.cookies.get(throttle.device_cookie_name()), key
        )
        if device is not None and not throttle.reserve_device_attempt(r, device):
            # Its allowance is spent, so it is heard as any other browser.
            device = None
        if device is None:
            # An allowance of zero or less is unlimited: the limiter allows it
            # without counting.
            ok, retry = address_window_allow(
                r,
                "login",
                _client_ip(request),
                settings.login_rate_limit_per_ip,
                settings.login_rate_limit_window_seconds,
                fail_closed=True,
            )
            if ok:
                ok, retry = throttle.reserve_unrecognised_attempt(r, key)
            if not ok:
                return _limited(retry)
    except Exception:
        return JSONResponse(
            {"detail": "Service temporarily unavailable. Please try again later."},
            status_code=503,
        )

    context = change_context(request)
    try:
        user = login_service.authenticate_password(
            db,
            context,
            username=payload.username,
            password=payload.password,
            method=payload.method,
            recognised=device is not None,
        )
    except DomainError:
        if device is not None:
            throttle.device_failed(r, device)
        raise
    except HTTPException:
        # The directory could not answer, so no password was judged.
        if device is not None:
            throttle.release_device_attempt(r, device)
        raise

    # The password is proven: the account's count ends, and this browser is
    # recognised for it from now on (its previous token retired).
    throttle.clear_throttle(r, key)
    token = throttle.issue_device(r, user_id=str(user.id), key=key, replaces=device)

    from app.security import mfa

    if user.mfa_enabled or mfa.required(db, user):
        # Committed with the challenge that mfa.start issues.
        login_service.record_sign_in(
            db, context, method=payload.method, outcome="mfa-required", account=user
        )
        account_id = user.id
        try:
            response = mfa.start(db, user, request)
            throttle.set_device_cookie(response, token)
            return response
        except HTTPException:
            # The password was right, so the refusal names the account.
            login_service.record_refused_sign_in(
                db,
                context,
                method=payload.method,
                account_id=account_id,
                outcome="mfa-refused",
            )
            raise
    response = complete_local_login(db, user, request)
    throttle.set_device_cookie(response, token)
    return response


def _limited(retry: int) -> JSONResponse:
    resp = JSONResponse({"detail": throttle.LIMITED}, status_code=429)
    resp.headers["Retry-After"] = str(max(1, int(retry)))
    return resp


def complete_local_login(
    db, user, request, *, mfa_verified=False, verified_version=None
):
    # Freeze the version proven by this ceremony before any commit expires ORM state.
    csrf_token = generate_csrf_token()
    sid, result = login_service.complete_local_login(
        db,
        user,
        existing_sid=request.cookies.get(session_cookie_name()),
        context=change_context(request),
        mfa_verified=mfa_verified,
        verified_version=verified_version,
        csrf_token=csrf_token,
    )
    response = JSONResponse(result)
    set_session_cookie(response, sid)
    set_csrf_cookie(response, csrf_token)
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
    context = change_context(request)
    account_id = None
    csrf_token = generate_csrf_token()
    try:
        user, next_url, id_token, provider = await handle_callback(
            request, db, provider_key=provider_key
        )
        account_id = user.id
        sid = await run_in_threadpool(
            partial(
                login_service.complete_sso_login,
                db,
                user,
                provider=provider,
                id_token=id_token,
                existing_sid=request.cookies.get(session_cookie_name()),
                context=context,
                csrf_token=csrf_token,
            )
        )
    except (HTTPException, DomainError) as error:
        # Audit attempts that consumed a sign-in started in this browser, naming
        # the account only when the provider's identity reached one.
        attempted = sso_attempt(request)
        if attempted is not None:
            await run_in_threadpool(
                partial(
                    login_service.record_refused_sign_in,
                    db,
                    context,
                    method=attempted,
                    account_id=linked_account_id(error) or account_id,
                )
            )
        raise

    dest = next_url or "/"
    resp = RedirectResponse(dest, status_code=302)
    set_session_cookie(resp, sid)
    set_csrf_cookie(resp, csrf_token)
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
    if request.cookies.get(session_cookie_name()):
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
    sid = (request.cookies.get(session_cookie_name()) or "").strip()
    _revoke_or_fail(sid)
    resp = Response(status_code=204)
    from app.security import mfa

    mfa.cancel(db, request, resp)
    clear_session_cookie(resp)
    clear_csrf_cookie(resp)
    return resp


def _browser_logout_response(request: Request, db: Session) -> Response:
    _require_logout_intent(request)
    sid = (request.cookies.get(session_cookie_name()) or "").strip()
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
    user = require_authenticated(request)

    resp = Response(status_code=204)
    # Optional metadata headers. Nginx *may* read these from the auth_request subrequest
    # response (via $upstream_http_x_keen_user / $upstream_http_x_keen_role) for
    # logging/diagnostics. Admin gating in this codebase uses the /v1/auth/check-admin
    # status code, not these headers.
    resp.headers["X-Keen-User"] = user.username
    resp.headers["X-Keen-Role"] = user.effective_role or user.role
    return resp


@router.get("/v1/auth/check-admin")
def auth_check_admin(request: Request) -> Response:
    """Used by nginx auth_request to hide the admin UI.

    Returns:
      - 204 if authenticated AND effective_role==admin
      - 401 if not authenticated
      - 403 if authenticated but not an administrator
    """

    user = require_authenticated(request)

    eff = user.effective_role or user.role
    if eff != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")

    resp = Response(status_code=204)
    # Optional metadata headers (see /v1/auth/check).
    resp.headers["X-Keen-User"] = user.username
    resp.headers["X-Keen-Role"] = eff
    return resp
