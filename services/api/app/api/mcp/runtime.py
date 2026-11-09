"""What the application's lifespan built for MCP; empty while MCP is off."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# The Keen application, which tools ask in process.
app: ASGIApp | None = None
# FastMCP's Streamable HTTP application, serving /v1/mcp.
mcp_app: ASGIApp | None = None
# The MCP SDK's OAuth handlers, by endpoint.
handlers: dict[str, Callable[[Request], Awaitable[Response]]] = {}


def clear() -> None:
    global app, mcp_app
    app = mcp_app = None
    handlers.clear()


def install(keen_app: Any, server_app: ASGIApp, built: dict) -> None:
    global app, mcp_app
    app, mcp_app = keen_app, server_app
    handlers.clear()
    handlers.update(built)
