from __future__ import annotations

import time
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.responses import JSONResponse, Response
from fastapi.openapi.utils import get_openapi


from app.api.routes import OPENAPI_TAGS, router as api_router
from app.core.config import settings
from app.db.models import AuditLog, User
from app.db.session import SessionLocal
from app.security.auth import ROLE_ADMIN, create_user, get_current_user_from_request
from app.security.permissions import (
    ensure_session_authorization_current,
    has_permission,
)
from app.security.roles import attach_effective_role
from app.security.csrf import (
    csrf_valid,
    generate_csrf_token,
    get_csrf_token_from_request,
    set_csrf_cookie,
)
from app.security.redaction import redact_query_string
from app.realtime.notifications import (
    start_notification_listener,
    stop_notification_listener,
)

app = FastAPI(
    title="Keen API",
    version="0.1.0",
    description=(
        "Keen backend API. Use /docs (or /v1/docs) for interactive Swagger UI, "
        "/redoc (or /v1/redoc) for ReDoc, and /openapi.json "
        "(or /v1/openapi.json) for the OpenAPI schema."
    ),
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    root_path_in_servers=False,
    openapi_tags=OPENAPI_TAGS,
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

    if settings.cookie_secure:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )

    return response


@app.middleware("http")
async def request_size_limit_middleware(request: Request, call_next):
    """Limit request body size to prevent DoS attacks."""
    if request.method in ("POST", "PUT", "PATCH"):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > 41943040:  # 40MB
                    return JSONResponse(
                        {"detail": "Request body too large"}, status_code=413
                    )
            except ValueError:
                pass

    return await call_next(request)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=OPENAPI_TAGS,
    )
    schema["servers"] = [{"url": "/api"}]  # external nginx prefix
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.on_event("startup")
async def _startup_realtime_notifications() -> None:
    # Best-effort; the app should still start even if Redis is temporarily
    # unavailable.
    try:
        await start_notification_listener()
    except Exception:
        pass


@app.on_event("shutdown")
async def _shutdown_realtime_notifications() -> None:
    try:
        await stop_notification_listener()
    except Exception:
        pass


def _client_ip(request: Request) -> str | None:
    # Prefer proxied headers (nginx sets these)
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        # First address is the original client
        return xff.split(",")[0].strip() or None
    xri = (request.headers.get("x-real-ip") or "").strip()
    if xri:
        return xri
    return getattr(getattr(request, "client", None), "host", None)


@app.on_event("startup")
def bootstrap_initial_admin():
    """Create the first admin user on fresh installs.

    If KEEN_BOOTSTRAP_ADMIN_USERNAME and KEEN_BOOTSTRAP_ADMIN_PASSWORD are
    set and there are no users in the database, create an initial admin.
    """
    uname = (settings.bootstrap_admin_username or "").strip()
    pwd = (settings.bootstrap_admin_password or "").strip()
    if not uname or not pwd:
        return

    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            return
        create_user(db, username=uname, password=pwd, role=ROLE_ADMIN)
        print(f"[keen] Bootstrapped initial admin user: {uname}")
    except Exception as e:  # pragma: no cover
        print(f"[keen] Failed to bootstrap admin user: {e}")
    finally:
        db.close()


# -----------------------------------------------------------------------------
# Authentication middleware
# -----------------------------------------------------------------------------


_AUTH_EXEMPT_PATHS = {
    "/health",
    "/v1/auth/methods",
    "/v1/auth/login",
    "/v1/auth/logout",
    "/v1/auth/oidc/start",
    "/v1/auth/oidc/callback",
    "/v1/auth/oidc/logout",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/v1/openapi.json",
    "/v1/docs",
    "/v1/redoc",
    "/v1/docs/oauth2-redirect",
}
_AUTH_EXEMPT_PREFIXES = ("/v1/webhooks", "/v1/auth/sso/", "/docs", "/redoc")


# CSRF: enforce for browser-session authenticated writes.
_CSRF_EXEMPT_PATHS = {
    "/health",
    "/v1/auth/methods",
    "/v1/auth/login",
    "/v1/auth/logout",
    "/v1/auth/oidc/start",
    "/v1/auth/oidc/callback",
    "/v1/auth/oidc/logout",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/v1/openapi.json",
    "/v1/docs",
    "/v1/redoc",
    "/v1/docs/oauth2-redirect",
}
_CSRF_EXEMPT_PREFIXES = ("/v1/webhooks", "/v1/auth/sso/", "/docs", "/redoc")


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    """Double-submit cookie CSRF protection for the web UI.

    The UI uses a cookie-backed session (HttpOnly) and echoes a CSRF token from
    a non-HttpOnly cookie into a request header for all unsafe methods.
    """
    path = request.url.path or ""

    if path in _CSRF_EXEMPT_PATHS or any(
        path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES
    ):
        return await call_next(request)

    # Only enforce CSRF for authenticated browser sessions.
    user = getattr(request.state, "user", None)
    if user and request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        if not csrf_valid(request):
            return JSONResponse(
                {"detail": "CSRF token missing or invalid"}, status_code=403
            )

    response = await call_next(request)

    # Backwards-compatible: if an existing session predates CSRF, mint a token
    # during the next authenticated request.
    if user and not get_csrf_token_from_request(request):
        try:
            set_csrf_cookie(response, generate_csrf_token())
        except Exception:
            # CSRF cookie minting should never break the main request path.
            pass

    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Authenticate the request and attach request.state.user.

    IMPORTANT: This middleware MUST run *before* csrf_middleware so that
    request.state.user is populated when CSRF enforcement happens.

    Starlette/FastAPI wraps middleware in reverse registration order (the last
    declared middleware runs first). Therefore auth_middleware is declared
    *after* csrf_middleware to ensure it executes earlier on the request path.
    """
    path = request.url.path or ""

    if path in _AUTH_EXEMPT_PATHS or any(
        path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES
    ):
        return await call_next(request)

    # IMPORTANT: do not keep a SQLAlchemy session open for the duration of the
    # request via middleware state. Route handlers create their own DB sessions
    # via dependencies. If we store an ORM instance from this middleware's
    # session on request.state, later attempts to use it with a different
    # session (e.g. db.add(user)) will raise:
    #   InvalidRequestError: Object is already attached to session ...
    #
    # So we:
    #  1) load the user in a short-lived session
    #  2) run access checks
    #  3) expunge (detach) the instance
    #  4) close the session before calling downstream handlers
    db = SessionLocal()
    user = None
    try:
        user = get_current_user_from_request(request, db)
        if not user:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        # Use the server-side session permission snapshot when current. This keeps
        # navbar/page rendering from querying RBAC tables on every navigation while
        # remaining invalidatable by admin RBAC changes.
        ensure_session_authorization_current(db, user)
        effective_role = getattr(user, "effective_role", None) or attach_effective_role(
            db, user
        )

        # Admin-only namespaces
        if path.startswith("/v1/users"):
            if (effective_role or "") != ROLE_ADMIN:
                return JSONResponse({"detail": "Admin role required"}, status_code=403)

        if path.startswith("/v1/admin"):
            # Special-case audit trail: allow users with audittrail.read.
            if path.startswith("/v1/admin/audit") or path.startswith(
                "/v1/admin/entity-changelog"
            ):
                if (effective_role or "") != ROLE_ADMIN and not has_permission(
                    db, user, "audittrail.read"
                ):
                    return JSONResponse(
                        {"detail": "Audit access required"}, status_code=403
                    )
            else:
                if (effective_role or "") != ROLE_ADMIN:
                    return JSONResponse(
                        {"detail": "Admin role required"}, status_code=403
                    )

        # Default rule: only admin can mutate backend state, except for
        # explicitly user/permission-owned workflows checked below.
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            if path in {
                "/v1/me/password",
                "/v1/me/preferences",
                "/v1/auth/logout",
            } or path.startswith("/v1/me/saved-searches"):
                pass  # self-service
            elif (
                request.method.upper() == "POST"
                and path.startswith("/v1/events/")
                and path.endswith("/questions")
            ):
                pass  # event questions (permission-checked in route)
            elif (
                request.method.upper() == "POST"
                and path.startswith("/v1/questions/")
                and path.endswith("/posts")
            ):
                pass  # event query replies (permission-checked in route)
            elif (
                request.method.upper() == "POST"
                and path.startswith("/v1/events/")
                and path.endswith("/questions/mark-seen")
            ):
                pass  # mark questions seen (thread author)
            elif (
                request.method.upper() == "POST"
                and path.startswith("/v1/events/")
                and path.endswith("/incident")
            ):
                if not has_permission(db, user, "incident.create"):
                    return JSONResponse(
                        {"detail": "incident.create permission required"},
                        status_code=403,
                    )
            elif (
                request.method.upper() == "DELETE"
                and path.startswith("/v1/events/")
                and "/incidents/" in path
            ):
                if not has_permission(db, user, "incident.delete"):
                    return JSONResponse(
                        {"detail": "incident.delete permission required"},
                        status_code=403,
                    )
            elif path.startswith("/v1/audits"):
                if not has_permission(db, user, "audits.manage"):
                    return JSONResponse(
                        {"detail": "audits.manage permission required"}, status_code=403
                    )
            elif path.startswith("/v1/risks"):
                if not has_permission(db, user, "risk.manage"):
                    return JSONResponse(
                        {"detail": "risk.manage permission required"}, status_code=403
                    )
            elif path.startswith("/v1/interested-parties"):
                if not (
                    has_permission(db, user, "interested_parties.manage")
                    or has_permission(db, user, "risk.manage")
                ):
                    return JSONResponse(
                        {"detail": "interested_parties.manage permission required"},
                        status_code=403,
                    )
            elif path.startswith("/v1/isms"):
                if not (
                    has_permission(db, user, "isms.manage")
                    or has_permission(db, user, "risk.manage")
                ):
                    return JSONResponse(
                        {"detail": "isms.manage permission required"},
                        status_code=403,
                    )
            elif (effective_role or "") != ROLE_ADMIN:
                return JSONResponse(
                    {"detail": "Read-only users cannot perform write actions"},
                    status_code=403,
                )

        # Detach the instance so it can be safely used with other sessions.
        db.expunge(user)
        request.state.user = user
    finally:
        db.close()

    return await call_next(request)


@app.middleware("http")
async def audit_trail_middleware(request: Request, call_next):
    """Persist an internal audit trail of UI->API actions.

    Records are written after the response completes so we can capture status
    codes and timings.
    """
    path = request.url.path or ""
    # Keep noise down by skipping obvious non-UI endpoints.
    if path in {
        "/health",
        "/v1/admin/questions/summary",  # admin bell badge poll,
        "/v1/evidence/latest",  # homepage ambient evidence ticker,
        "/v1/events/latest",  # backwards-compatible ticker alias,
        "/v1/webhooks",  # webhook ingesters
    }:
        return await call_next(request)

    start = time.perf_counter()
    response = None

    try:
        response = await call_next(request)
        return response
    finally:
        try:
            # Only log API calls used by the UI (UI itself is served by nginx in a separate container)
            if not path.startswith("/v1"):
                return

            status_code = getattr(response, "status_code", 500)
            dur_ms = int((time.perf_counter() - start) * 1000)

            user = getattr(request.state, "user", None)
            username = getattr(user, "username", None) if user else None

            qs = redact_query_string(request.url.query)
            if qs and len(qs) > 4096:
                qs = qs[:4096]

            ua = (request.headers.get("user-agent") or "").strip() or None
            ref = (request.headers.get("referer") or "").strip() or None

            # Truncate potentially long strings for safety.
            if ua and len(ua) > 256:
                ua = ua[:256]
            if ref and len(ref) > 512:
                ref = ref[:512]

            db = SessionLocal()
            try:
                db.add(
                    AuditLog(
                        ts=datetime.utcnow(),
                        username=username,
                        method=request.method,
                        path=path,
                        query_string=qs,
                        status_code=int(status_code or 0),
                        duration_ms=dur_ms,
                        client_ip=_client_ip(request),
                        user_agent=ua,
                        referer=ref,
                    )
                )
                db.commit()
            finally:
                db.close()
        except Exception:
            # Audit logging must never break the main request path.
            pass


@app.get("/health")
def health():
    return {"ok": True, "service": "keen"}


@app.get("/openapi.json", include_in_schema=False)
def openapi_root():
    return app.openapi()


@app.get("/docs", include_in_schema=False)
def docs_root():
    return get_swagger_ui_html(
        openapi_url="./openapi.json",
        title=f"{app.title} - Swagger UI",
    )


@app.get("/docs/oauth2-redirect", include_in_schema=False)
def docs_oauth2_redirect_root():
    return get_swagger_ui_oauth2_redirect_html()


@app.get("/redoc", include_in_schema=False)
def redoc_root():
    return get_redoc_html(
        openapi_url="./openapi.json",
        title=f"{app.title} - ReDoc",
    )


@app.get("/v1/openapi.json", include_in_schema=False)
def openapi_v1():
    return app.openapi()


@app.get("/v1/docs", include_in_schema=False)
def docs_v1():
    return get_swagger_ui_html(
        openapi_url="./openapi.json",
        title=f"{app.title} - Swagger UI",
    )


@app.get("/v1/docs/oauth2-redirect", include_in_schema=False)
def docs_oauth2_redirect_v1():
    return get_swagger_ui_oauth2_redirect_html()


@app.get("/v1/redoc", include_in_schema=False)
def redoc_v1():
    return get_redoc_html(
        openapi_url="./openapi.json",
        title=f"{app.title} - ReDoc",
    )


app.include_router(api_router, prefix="")
