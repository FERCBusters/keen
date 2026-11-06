"""Bound request bodies and screen request targets before handlers see them."""

import re
import tempfile

import anyio
from starlette.responses import JSONResponse

from app.security.regex import scan_pattern

MAX_BODY = 40 * 1024 * 1024
# A decoded JSON document costs many times its size in memory and processor
# time, so the application parses at most this much JSON from one request.
# Administration routes, which hold configuration and control imports, may
# send a whole request body of JSON.
MAX_JSON_BODY = 2 * 1024 * 1024
# Checking a document against a route's model also costs time for each value,
# and that check runs on the event loop. Outside administration, no form sends
# more than a few thousand values, so a request holds at most this many.
MAX_JSON_VALUES = 20_000
# ISMS document files and audit final reports accept a single file up to this
# size; their operations check the file itself against the same figure.
MAX_FILE_UPLOAD = 100 * 1024 * 1024
# A signed effectiveness-metric submission.
MAX_METRIC_SUBMISSION = 256 * 1024
# Room for the multipart boundary and part headers around that one file.
_MULTIPART_ROOM = 64 * 1024
_SEALED_OVERHEAD = 1120 + 32 + 16
_FILE_UPLOADS = scan_pattern(r"/v1/(?:isms/documents/[^/]+/file|audits/[^/]+/report)")


def body_limit(path):
    if path in {"/v1/auth/login", "/v1/agents/enroll", "/v1/agents/renew"}:
        return 16 * 1024
    # keen-agent requests are encrypted to KEEN, which adds a fixed overhead
    # (app.agents.server_keys.SEALED_OVERHEAD) to the plaintext limits.
    if path == "/v1/agents/heartbeat":
        return 32 * 1024 + _SEALED_OVERHEAD
    if path.startswith("/v1/auth/mfa/"):
        return 256 * 1024
    if path == "/v1/otlp/logs":
        return 1024 * 1024 + _SEALED_OVERHEAD
    # Signed metric submissions are small; the body is read before its signature
    # can be checked, so an unsigned sender holds no more than this in memory.
    if path.startswith("/v1/webhooks/isms/effectiveness-metrics/"):
        return MAX_METRIC_SUBMISSION
    if path.startswith("/v1/webhooks/"):
        return 10 * 1024 * 1024
    if _FILE_UPLOADS.fullmatch(path):
        return MAX_FILE_UPLOAD + _MULTIPART_ROOM
    # MCP clients' OAuth requests are small forms and JSON documents, which the
    # MCP SDK parses on the event loop; MCP calls carry short tool arguments.
    if path.startswith("/v1/oauth/"):
        return 16 * 1024
    if path == "/v1/mcp":
        return 256 * 1024
    return MAX_BODY


def json_body_limit(path):
    """The most JSON the application parses from one request to this path.

    The check applies where a body is parsed as JSON, whatever its declared
    content type; webhooks and agents read their bodies under their own limits.
    """
    if path.startswith("/v1/admin/"):
        return MAX_BODY
    return MAX_JSON_BODY


def json_value_limit(path):
    """The most values a parsed JSON request to this path may hold, if bounded."""
    if path.startswith("/v1/admin/"):
        return None
    return MAX_JSON_VALUES


def target_has_nul(scope) -> bool:
    """PostgreSQL text cannot hold NUL, so no path or query value may carry one."""
    query = scope.get("query_string") or b""
    return "\x00" in scope.get("path", "") or b"%00" in query or b"\x00" in query


# The routes whose last parameter may itself contain "/" (a ``:path``
# parameter), and so may arrive with an encoded slash. Every such route is
# listed here; tests/test_request_targets.py fails when another appears.
ENCODED_SLASH_ROUTES = scan_pattern(r"/v1/admin/frameworks/[^/]+/nodes/[^/]+/.+")
_ENCODED_SEPARATOR = scan_pattern(rb"%(?:2f|5c)", re.IGNORECASE)


def target_traverses(scope) -> bool:
    """Whether a request target could name another route than it appears to.

    A path parameter that carries an encoded "/" or "../" decodes into extra
    segments. Browser pages build API paths from record identifiers and query
    values, so such a target would let a crafted link send the page's own
    authenticated request to a different route. No API path has a "." or ".."
    segment, and only routes with a ``:path`` parameter take an encoded slash.
    """
    path = scope.get("path", "")
    if any(segment in {".", ".."} for segment in path.split("/")):
        return True
    raw = scope.get("raw_path") or path.encode()
    return bool(_ENCODED_SEPARATOR.search(raw)) and not ENCODED_SLASH_ROUTES.fullmatch(
        path
    )


class RequestBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = body_limit(scope["path"])

        async def reject(status, message):
            await JSONResponse(
                {"detail": message},
                status_code=status,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)

        if target_has_nul(scope) or target_traverses(scope):
            return await reject(400, "Invalid request target")

        for key, value in scope.get("headers", []):
            if key.lower() == b"content-length":
                if not value.isdigit():
                    return await reject(400, "Invalid Content-Length")
                if len(value) > 10 or int(value) > limit:
                    return await reject(413, "Request body too large")

        # Spool larger uploads to disk. Do not dispatch a partial request: a
        # handler must never commit data before an oversized tail is rejected.
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as body:
            total = 0
            try:
                with anyio.fail_after(30):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        total += len(chunk)
                        if total > limit:
                            return await reject(413, "Request body too large")
                        if chunk:
                            await anyio.to_thread.run_sync(body.write, chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await reject(408, "Request body timeout")
            body.seek(0)
            remaining = total
            complete = False

            async def replay():
                nonlocal remaining, complete
                if complete:
                    return await receive()
                chunk = await anyio.to_thread.run_sync(body.read, 64 * 1024)
                remaining -= len(chunk)
                complete = remaining == 0
                return {
                    "type": "http.request",
                    "body": chunk,
                    "more_body": not complete,
                }

            await self.app(scope, replay, send)
