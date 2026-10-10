"""Bound request bodies before parsers or handlers consume untrusted input."""

import tempfile

import anyio
from starlette.responses import JSONResponse

MAX_BODY = 40 * 1024 * 1024


def body_limit(path):
    if path in {"/v1/auth/login", "/v1/agents/enroll", "/v1/agents/renew"}:
        return 16 * 1024
    if path == "/v1/agents/heartbeat":
        return 32 * 1024
    if path.startswith("/v1/auth/mfa/"):
        return 256 * 1024
    if path == "/v1/otlp/logs":
        return 1024 * 1024
    if path.startswith("/v1/webhooks/"):
        return 10 * 1024 * 1024
    return MAX_BODY


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
