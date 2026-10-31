"""Demo lifetime is server-owned and applies to every HTTP/WebSocket entry point."""

import asyncio
from datetime import datetime, timezone

from starlette.responses import JSONResponse

from app.core.config import settings


def demo_expired() -> bool:
    if not settings.demo_mode:
        return False
    expiry = settings.demo_expires_at
    # Settings refuse demo mode without an expiry; a missing one counts as ended.
    return expiry is None or datetime.now(timezone.utc) >= expiry


def demo_metadata():
    expiry = settings.demo_expires_at if settings.demo_mode else None
    return {
        "enabled": settings.demo_mode,
        "expires_at": expiry.isoformat() if expiry else None,
        "consultation_url": settings.demo_consultation_url
        if settings.demo_mode
        else None,
        "expired": demo_expired(),
    }


class DemoExpiryMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not settings.demo_mode or scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        if scope["type"] == "http":
            if demo_expired() and scope["path"] not in ("/v1/auth/methods", "/health"):
                return await JSONResponse(
                    {
                        "detail": "This Keen demo has ended.",
                        "consultation_url": settings.demo_consultation_url,
                    },
                    status_code=401 if scope["path"] == "/v1/auth/check" else 410,
                )(scope, receive, send)
            return await self.app(scope, receive, send)
        expiry = settings.demo_expires_at
        seconds = (expiry - datetime.now(timezone.utc)).total_seconds() if expiry else 0
        if seconds <= 0:
            return await send({"type": "websocket.close", "code": 1008})
        try:
            await asyncio.wait_for(self.app(scope, receive, send), timeout=seconds)
        except asyncio.TimeoutError:
            await send({"type": "websocket.close", "code": 1008})
