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


def mark_machine_credential(request: Request, credential: str) -> None:
    """Name the machine credential that verified this request.

    The audit trail records requests without a signed-in account at a bounded
    rate for each client address; a request whose agent key, webhook secret,
    measure secret or enrolment credential verified is recorded at any rate,
    since that credential's own allowances bound it.
    """
    request.state.machine_credential = credential


def machine_credential(request: Request) -> str | None:
    """The machine credential that verified this request, if any."""
    return getattr(request.state, "machine_credential", None)
