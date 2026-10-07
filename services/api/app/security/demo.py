"""Demo lifetime is server-owned and applies to every HTTP/WebSocket entry point."""
import asyncio
from datetime import datetime, timezone
from starlette.responses import JSONResponse
from app.core.config import settings


def demo_expired():
    return settings.demo_mode and datetime.now(timezone.utc) >= settings.demo_expires_at


def demo_metadata():
    return {"enabled": settings.demo_mode,
            "expires_at": settings.demo_expires_at.isoformat() if settings.demo_mode else None,
            "consultation_url": settings.demo_consultation_url if settings.demo_mode else None,
            "expired": bool(demo_expired())}


class DemoExpiryMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not settings.demo_mode or scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        if scope['type'] == 'http':
            if demo_expired() and scope['path'] not in ('/v1/auth/methods', '/health'):
                return await JSONResponse({"detail": "This KEEN demo has ended.",
                    "consultation_url": settings.demo_consultation_url}, status_code=401 if scope["path"] == "/v1/auth/check" else 410)(scope, receive, send)
            return await self.app(scope, receive, send)
        seconds = (settings.demo_expires_at - datetime.now(timezone.utc)).total_seconds()
        if seconds <= 0:
            return await send({"type": "websocket.close", "code": 1008})
        try:
            await asyncio.wait_for(self.app(scope, receive, send), timeout=seconds)
        except asyncio.TimeoutError:
            await send({"type": "websocket.close", "code": 1008})
