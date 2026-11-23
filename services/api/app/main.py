from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.openapi.utils import get_openapi
from mosp import middleware
from mosp.api.errors import domain_error_response, validation_error_response
from mosp.api.sql_trace import RequestLabelMiddleware
from mosp.realtime.notifications import (
    start_notification_listener,
    stop_notification_listener,
)
from mosp.security.headers import SecurityHeadersMiddleware
from mosp.security.request_limits import RequestBodyLimitMiddleware
from mosp.services.errors import DomainError
from starlette.concurrency import run_in_threadpool

from app.api.mcp import server as mcp_server
from app.api.routes import OPENAPI_TAGS
from app.api.routes import router as api_router
from app.core.config import settings
from app.security.demo import DemoExpiryMiddleware

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _startup_realtime_notifications()
    try:
        await run_in_threadpool(middleware.bootstrap_initial_admin)
        async with mcp_server.lifespan(_app):
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

# Authentication, CSRF and the audit trail (mosp), inside the middleware below.
middleware.install(app)

# Outermost guard also covers auth-exempt integrations and WebSockets.
app.add_middleware(DemoExpiryMiddleware)


# Enforce byte limits before JSON/form parsing, including chunked requests.
app.add_middleware(RequestBodyLimitMiddleware)

# Response policy also covers rejections from every inner middleware.
app.add_middleware(SecurityHeadersMiddleware)

# Outermost when enabled, so statements from every middleware are labelled.
if settings.sql_trace:
    app.add_middleware(RequestLabelMiddleware)
