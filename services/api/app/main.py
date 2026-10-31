from __future__ import annotations

import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api.errors import domain_error_response, validation_error_response
from app.api.routes import OPENAPI_TAGS
from app.api.routes import router as api_router
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import AuditLog, User
from app.db.session import SessionLocal
from app.realtime.notifications import (
    start_notification_listener,
    stop_notification_listener,
)
from app.security.auth import ROLE_ADMIN, create_user, get_current_user_from_request
from app.security.client_ip import request_ip as _client_ip
from app.security.csrf import (
    csrf_valid,
    generate_csrf_token,
    get_csrf_token_from_request,
    set_csrf_cookie,
)
from app.security.demo import DemoExpiryMiddleware
from app.security.headers import SecurityHeadersMiddleware
from app.security.permissions import (
    ensure_session_authorization_current,
    has_permission,
)
from app.security.redaction import redact_query_string
from app.security.request_limits import RequestBodyLimitMiddleware
from app.security.roles import attach_effective_role
from app.security.route_policy import (
    gated_write_permission,
    is_permission_checked_write,
    is_public_request,
    policy_path,
)
from app.services.errors import DomainError


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _startup_realtime_notifications()
    try:
        bootstrap_initial_admin()
        yield
    finally:
        await _shutdown_realtime_notifications()


app = FastAPI(
    lifespan=lifespan,
    title="Keen API",
    version="1.0.0",
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
    exception_handlers={
        DomainError: domain_error_response,
        RequestValidationError: validation_error_response,
    },
)


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


app.openapi = custom_openapi  # type: ignore[method-assign]  # FastAPI's documented override


async def _startup_realtime_notifications() -> None:
    # Best-effort; the app should still start even if Redis is temporarily
    # unavailable.
    try:
        await start_notification_listener()
    except Exception:
        pass


async def _shutdown_realtime_notifications() -> None:
    try:
        await stop_notification_listener()
    except Exception:
        pass


def bootstrap_initial_admin():
    """Create the first admin user on fresh installs.

    If KEEN_BOOTSTRAP_ADMIN_USERNAME and KEEN_BOOTSTRAP_ADMIN_PASSWORD are
    set and there are no users in the database, create an initial admin.
    """
    if settings.hosted_mode:
        return  # Owner is created explicitly before API startup.
    uname = settings.bootstrap_admin_username.strip()
    pwd = settings.bootstrap_admin_password.strip()
    if not uname and not pwd:
        return  # Explicit bootstrap is optional; no account is created.

    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            return
        if not uname or not pwd:
            raise ValueError(
                "Supply both bootstrap credentials for initial account creation"
            )
        if len(pwd) < 8:
            raise ValueError("Bootstrap password must be at least eight characters")
        create_user(db, username=uname, password=pwd, role=ROLE_ADMIN)
        print(f"[keen] Bootstrapped initial admin user: {uname}")
    except Exception:
        # Do not expose passwords or database diagnostics in startup failures.
        db.rollback()
        raise RuntimeError(
            "Initial administrator creation failed; on an empty installation supply "
            "both KEEN_BOOTSTRAP_ADMIN_USERNAME and KEEN_BOOTSTRAP_ADMIN_PASSWORD "
            "(at least eight characters), and check database availability"
        ) from None
    finally:
        db.close()


# -----------------------------------------------------------------------------
# Authentication middleware
# -----------------------------------------------------------------------------


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    """Double-submit cookie CSRF protection for the web UI.

    The UI uses a cookie-backed session (HttpOnly) and echoes a CSRF token from
    a non-HttpOnly cookie into a request header for all unsafe methods.
    """
    if is_public_request(request):
        return await call_next(request)

    # Only enforce CSRF for authenticated browser sessions.
    user = getattr(request.state, "user", None)
    if user and request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        if not csrf_valid(request):
            return JSONResponse(
                {"detail": "CSRF token missing or invalid"}, status_code=403
            )

    response = await call_next(request)

    # Browsers signed in by a trusted proxy have no Keen sign-in response to
    # carry the CSRF cookie, and a browser may lose the cookie before its
    # session ends; either receives a token with this response.
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
    path = policy_path(request)

    if is_public_request(request):
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
        effective_role = user.effective_role or attach_effective_role(db, user)

        # Admin-only namespaces
        if path.startswith("/v1/users"):
            if effective_role != ROLE_ADMIN:
                return JSONResponse({"detail": "Admin role required"}, status_code=403)

        if path.startswith("/v1/admin"):
            # Special-case audit trail: allow users with audittrail.read.
            if path in {"/v1/admin/audit", "/v1/admin/entity-changelog"}:
                if effective_role != ROLE_ADMIN and not has_permission(
                    db, user, "audittrail.read"
                ):
                    return JSONResponse(
                        {"detail": "Audit access required"}, status_code=403
                    )
            else:
                if effective_role != ROLE_ADMIN:
                    return JSONResponse(
                        {"detail": "Admin role required"}, status_code=403
                    )

        # Default rule: only admin can mutate backend state, except for
        # explicitly user/permission-owned workflows checked below.
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            if is_permission_checked_write(request):
                pass  # authentication and CSRF still apply; adapters enforce permissions
            elif (
                path
                in {
                    "/v1/me/password",
                    "/v1/me/preferences",
                    "/v1/auth/logout",
                }
                or path.startswith("/v1/me/saved-searches")
                or path
                in {
                    "/v1/me/sso-emails/request",
                    "/v1/me/sso-emails/confirm",
                    "/v1/me/sso-emails/remove",
                }
            ):
                pass  # self-service
            elif code := gated_write_permission(request):
                if not has_permission(db, user, code):
                    return JSONResponse(
                        {"detail": f"{code} permission required"}, status_code=403
                    )
            elif path.startswith("/v1/audits"):
                if not (
                    has_permission(db, user, "audits.manage")
                    or has_permission(db, user, "audits.admin")
                ):
                    return JSONResponse(
                        {"detail": "audits.manage or audits.admin permission required"},
                        status_code=403,
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
            elif path.startswith("/v1/quizzes"):
                if not has_permission(db, user, "quiz.manage"):
                    return JSONResponse(
                        {"detail": "quiz.manage permission required"},
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
            elif effective_role != ROLE_ADMIN:
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


# Authorisation codes and state are single-use, but are still credentials
# while a sign-in is in progress; keep them out of the audit trail.
_OAUTH_CALLBACK_PATH = re.compile(r"/v1/auth/(?:oidc|sso/[^/]+)/callback")
_OAUTH_CALLBACK_QUERY_KEYS = frozenset({"code", "state"})


@app.middleware("http")
async def audit_trail_middleware(request: Request, call_next):
    """Persist an internal audit trail of UI->API actions.

    Records are written after the response completes so we can capture status
    codes and timings.
    """
    path = policy_path(request)
    # Keep noise down by skipping obvious non-UI endpoints.
    if not path.startswith("/v1") or path in {
        "/health",
        "/v1/admin/questions/summary",  # admin bell badge poll,
        "/v1/evidence/latest",  # homepage ambient evidence ticker,
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
            status_code = getattr(response, "status_code", 500)
            dur_ms = int((time.perf_counter() - start) * 1000)

            user = getattr(request.state, "user", None)
            username = getattr(user, "username", None) if user else None

            qs = redact_query_string(
                request.url.query,
                _OAUTH_CALLBACK_QUERY_KEYS
                if _OAUTH_CALLBACK_PATH.fullmatch(path)
                else frozenset(),
            )
            if qs and len(qs) > 4096:
                qs = qs[:4096]

            ua = (request.headers.get("user-agent") or "").strip() or None
            from app.security.redaction import redact_str

            ref = redact_str((request.headers.get("referer") or "").strip()) or None

            # Truncate potentially long strings for safety.
            if ua and len(ua) > 256:
                ua = ua[:256]
            if ref and len(ref) > 512:
                ref = ref[:512]

            db = SessionLocal()
            try:
                db.add(
                    AuditLog(
                        ts=utc_now_naive(),
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

# Outermost guard also covers auth-exempt integrations and WebSockets.
app.add_middleware(DemoExpiryMiddleware)


# Enforce byte limits before JSON/form parsing, including chunked requests.
app.add_middleware(RequestBodyLimitMiddleware)

# Response policy also covers rejections from every inner middleware.
app.add_middleware(SecurityHeadersMiddleware)
