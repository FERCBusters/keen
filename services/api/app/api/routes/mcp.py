"""MCP and its OAuth endpoints, and each person's consent and connections.

The OAuth endpoints are the MCP SDK's handlers (app.api.mcp.server builds them
while MCP is enabled); they authenticate their own clients, codes and tokens,
so security/route_policy.py lists them as public. The MCP endpoint is
FastMCP's, which admits only a current access token. Each answers 404 while
MCP is off.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData
from starlette.responses import Response

from app.api.mcp import runtime
from app.api.routing import Router
from app.core.config import settings
from app.core.valkey import get_valkey
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.security.client_ip import request_ip
from app.security.rate_limit import address_window_allow
from app.services import mcp_oauth as service
from app.services.mcp_oauth import ConsentAnswer, ConsentLookup

router = Router()

# Per client address: (requests, window seconds). Clients behind one address,
# such as a hosted assistant's, share an allowance.
_ALLOWANCES = {
    "register": (30, 3600),
    "authorize": (120, 600),
    "token": (300, 60),
    "revoke": (60, 60),
}


def _allowed(request: Request, endpoint: str) -> tuple[bool, int]:
    limit, window = _ALLOWANCES[endpoint]
    return address_window_allow(
        get_valkey(),
        f"mcp-{endpoint}",
        request_ip(request),
        limit,
        window,
        fail_closed=True,
    )


async def _oauth(request: Request, endpoint: str) -> Response:
    handler = runtime.handlers.get(endpoint)
    if handler is None:
        raise HTTPException(404, "Not Found")
    if endpoint in _ALLOWANCES:
        allowed, retry_after = await run_in_threadpool(_allowed, request, endpoint)
        if not allowed:
            return JSONResponse(
                {
                    "error": "temporarily_unavailable",
                    "error_description": "Too many requests; retry later",
                },
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
    return await handler(request)


@router.get(
    "/.well-known/oauth-protected-resource/api/v1/mcp",
    summary="Protected resource metadata for the MCP endpoint (RFC 9728)",
)
async def protected_resource_metadata(request: Request):
    return await _oauth(request, "resource")


@router.get(
    "/.well-known/oauth-authorization-server/api/v1/oauth",
    summary="Authorization server metadata (RFC 8414)",
)
async def authorization_server_metadata(request: Request):
    return await _oauth(request, "metadata")


@router.get("/v1/oauth/authorize", summary="OAuth authorization endpoint")
async def authorize(request: Request):
    return await _oauth(request, "authorize")


@router.post("/v1/oauth/token", summary="OAuth token endpoint")
async def token(request: Request):
    return await _oauth(request, "token")


@router.post("/v1/oauth/register", summary="OAuth dynamic client registration")
async def register(request: Request):
    return await _oauth(request, "register")


@router.post("/v1/oauth/revoke", summary="OAuth token revocation")
async def revoke(request: Request):
    # The SDK's revocation form requires a client_secret field, which a public
    # client (RFC 7009 section 2.1) leaves out; it is read here as empty.
    # parse_form keeps the parsed form on the request for every later reader.
    form = await request.form()
    if "client_secret" not in form:
        request._form = FormData([*form.multi_items(), ("client_secret", "")])
    return await _oauth(request, "revoke")


class _Delegated(Response):
    """Hand the request, body unread, to another ASGI application."""

    def __init__(self, app) -> None:
        self.app = app
        self.background = None

    async def __call__(self, scope, receive, send) -> None:
        await self.app(scope, receive, send)


def _mcp_endpoint() -> Response:
    if runtime.mcp_app is None:
        raise HTTPException(404, "Not Found")
    return _Delegated(runtime.mcp_app)


# Streamable HTTP uses POST for client messages, GET for the server-to-client
# event stream and DELETE to end a session. Each must reach FastMCP's app: it
# checks the bearer on the methods it serves and refuses the others with 405,
# which an MCP client reads as "no event stream". Keen's browser middleware
# answers 401, which the client instead reads as an expired token and refreshes
# on every connection until a rotated refresh token is reused.
@router.post("/v1/mcp", summary="MCP Streamable HTTP endpoint (OAuth bearer token)")
async def mcp_endpoint():
    return _mcp_endpoint()


@router.get("/v1/mcp", summary="MCP Streamable HTTP event stream (OAuth bearer token)")
async def mcp_event_stream():
    return _mcp_endpoint()


@router.delete(
    "/v1/mcp", summary="MCP Streamable HTTP session end (OAuth bearer token)"
)
async def mcp_session_end():
    return _mcp_endpoint()


def _enabled() -> None:
    if not settings.mcp_enabled:
        raise HTTPException(404, "AI agent access is not enabled")


@router.post("/v1/me/mcp/consent/view")
def view_consent(
    payload: ConsentLookup,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    _enabled()
    return service.consent_view(db, secret=payload.request, user=user)


@router.post("/v1/me/mcp/consent")
def answer_consent(
    payload: ConsentAnswer,
    request: Request,
    user=Depends(require_authenticated),
    db: Session = Depends(get_db),
):
    _enabled()
    return service.answer_consent(
        db,
        secret=payload.request,
        user=user,
        approve=payload.approve,
        scopes=payload.scopes,
        ip=request_ip(request),
    )


@router.get("/v1/me/mcp")
def my_connections(user=Depends(require_authenticated), db: Session = Depends(get_db)):
    return service.grants_for(db, user=user)


@router.delete("/v1/me/mcp/grants/{grant_id}")
def revoke_connection(
    grant_id: str, user=Depends(require_authenticated), db: Session = Depends(get_db)
):
    return service.revoke_own_grant(db, user=user, grant_id=grant_id)
