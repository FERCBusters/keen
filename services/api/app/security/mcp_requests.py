"""The account an MCP tool's in-process request acts as (myf's
``hooks.inner_request_user``)."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from myf.core.valkey import get_valkey
from myf.host import hooks
from myf.security.auth import ROLE_ADMIN
from myf.security.rate_limit import fixed_window_allow

from app.core.config import settings
from app.db.models import User
from app.security.mcp_tokens import ADMIN_SCOPES, INNER_TOKEN
from app.security.route_policy import NOT_A_TOOL_ROUTE, mcp_tool_route_scope
from app.services.mcp_oauth import scoped_user


def inner_request_user(db, request: Request):
    """An MCP tool's request carries its connection's token in the ASGI scope,
    which only app.api.mcp tools set; every other request is ordinary."""
    token = request.scope.get(INNER_TOKEN)
    if token is None:
        return hooks.NOT_INNER
    return mcp_user(db, request, token)


# Reads and changes one MCP connection may make each minute through the tools.
_MCP_READS_PER_MINUTE = 600
_MCP_WRITES_PER_MINUTE = 120


def mcp_user(db, request: Request, token: str) -> User | JSONResponse | None:
    """The account an MCP tool's in-process request acts as, narrowed to its
    connection's scopes; a refusal; or None when the token is not current.

    Only app.api.mcp tools set the token in the ASGI scope; a client cannot.
    A request reaches only a route the tools use (INNER_ROUTES); a change needs
    KEEN_MCP_WRITE_ENABLED and the route's change scope, and only the
    mapping-rule routes act with the administrator role, for an administrator
    who granted that scope.
    """
    reading = request.method.upper() in {"GET", "HEAD"}
    needed = mcp_tool_route_scope(request)
    # Every change route names its change scope; one that named none would
    # be refused here rather than left to the route.
    if needed == NOT_A_TOOL_ROUTE or (needed is None and not reading):
        return JSONResponse(
            {"detail": "MCP tools do not use this route"}, status_code=403
        )
    if not reading and not settings.mcp_write_enabled:
        return JSONResponse(
            {"detail": "This Keen accepts no changes from AI apps"}, status_code=403
        )
    found = scoped_user(db, token)
    if found is None:
        return None
    user, record = found
    if needed is not None and needed not in (user.effective_permission_codes or ()):
        return JSONResponse(
            {"detail": f"This connection was not granted {needed}"}, status_code=403
        )
    if needed in ADMIN_SCOPES:
        # granted_scopes kept this scope only for a current administrator.
        user.effective_role = ROLE_ADMIN
    # One allowance for the connection, however often its token is refreshed.
    allowed, retry_after = fixed_window_allow(
        get_valkey(),
        f"keen:rl:mcp-{'reads' if reading else 'writes'}:{record.grant_id}",
        _MCP_READS_PER_MINUTE if reading else _MCP_WRITES_PER_MINUTE,
        60,
        fail_closed=True,
    )
    if not allowed:
        return JSONResponse(
            {"detail": "Too many requests from this connection"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    request.state.inner_client = record.client_id
    return user
