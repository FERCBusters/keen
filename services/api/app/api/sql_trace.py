"""Label each HTTP request's SQL statements while KEEN_SQL_TRACE is on."""

from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

from app.db import sql_trace

_IDENTIFIER = re.compile(r"[0-9a-fA-F]{8}-?(?:[0-9a-fA-F]{4}-?){3}[0-9a-fA-F]{12}|\d+")


def route_label(path: str) -> str:
    """A request path with each numeric or UUID segment replaced by ``{id}``."""
    segments = ("{id}" if _IDENTIFIER.fullmatch(s) else s for s in path.split("/"))
    return sql_trace.clean("/".join(segments), 160)


def page_label(referer: str) -> str:
    """The path of the page that sent a request, from its Referer header."""
    try:
        path = urlsplit(referer).path
    except ValueError:
        path = ""
    return sql_trace.clean(path, 80)


class RequestLabelMiddleware:
    """Label the statements of each HTTP request and report how long it took."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        ident = sql_trace.new_id()
        method = sql_trace.clean(scope.get("method") or "", 10)
        route = route_label(scope.get("path") or "")
        page = page_label(headers.get(b"referer", b"").decode("latin-1"))
        record: dict[str, object] = {
            "kind": "request",
            "id": ident,
            "method": method,
            "route": route,
            "page": page,
            "status": 500,
        }

        async def send_with_status(message):
            if message["type"] == "http.response.start":
                record["status"] = message["status"]
            await send(message)

        token = sql_trace.begin("req", ident, f"{method} {route} page={page}")
        started = time.perf_counter()
        try:
            await self.app(scope, receive, send_with_status)
        finally:
            record["ms"] = round((time.perf_counter() - started) * 1000, 1)
            sql_trace.end(token, record)
