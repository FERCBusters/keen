"""Extract trusted request context at the HTTP boundary."""

from fastapi import Request

from app.security.client_ip import request_ip
from app.services.context import ChangeContext


def change_context(request: Request) -> ChangeContext:
    return ChangeContext(
        method=request.method,
        path=request.url.path,
        actor=getattr(request.state, "user", None),
        client_ip=request_ip(request),
        user_agent=request.headers.get("user-agent"),
        referer=request.headers.get("referer"),
    )
