from __future__ import annotations

import secrets
from typing import Literal

from fastapi import Request, Response

from app.core.config import settings

SameSite = Literal["lax", "strict", "none"]
_SAMESITE: dict[str, SameSite] = {"lax": "lax", "strict": "strict", "none": "none"}


def samesite_policy(value: str, default: SameSite) -> SameSite:
    """A cookie's SameSite setting, falling back to the default when unrecognised."""
    return _SAMESITE.get((value or "").lower(), default)


def csrf_cookie_name() -> str:
    # Allow override, but keep a stable default.
    return settings.csrf_cookie_name.strip() or "keen_csrf"


def csrf_header_name() -> str:
    # Default mirrors common conventions.
    return settings.csrf_header_name.strip() or "X-CSRF-Token"


def generate_csrf_token() -> str:
    # URL-safe, high entropy.
    return secrets.token_urlsafe(32)


def get_csrf_token_from_request(request: Request) -> str | None:
    raw = request.cookies.get(csrf_cookie_name())
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def get_csrf_token_from_header(request: Request) -> str | None:
    # Be tolerant of a couple of common variants.
    for h in (csrf_header_name(), "X-CSRFToken", "X-XSRF-Token"):
        raw = request.headers.get(h)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def set_csrf_cookie(response: Response, token: str) -> None:
    # Keep cookie settings aligned with the main session cookie.
    domain = settings.cookie_domain.strip() or None

    # CSRF cookies should be readable by JS so the UI can echo the token
    # in a header.
    response.set_cookie(
        key=csrf_cookie_name(),
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=samesite_policy(settings.csrf_cookie_samesite, "strict"),
        domain=domain,
        path="/",
    )


def clear_csrf_cookie(response: Response) -> None:
    domain = settings.cookie_domain.strip() or None
    response.delete_cookie(key=csrf_cookie_name(), domain=domain, path="/")


def request_origin_allowed(request, *, require_origin=False) -> bool:
    """Validate browser origins for both HTTP and WebSocket entry points.

    KEEN_PUBLIC_BASE_URL is the one accepted origin, so the request's own Host
    and forwarded headers have no say. Non-browser HTTP clients may omit Origin
    and still need their CSRF token.
    """
    from urllib.parse import urlsplit

    supplied = request.headers.get("origin")
    if not supplied:
        return (
            not require_origin and request.headers.get("sec-fetch-site") != "cross-site"
        )

    def origin(value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "\\" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError("Invalid origin")
        return (
            parsed.scheme,
            parsed.hostname.lower(),
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )

    try:
        return origin(supplied) == origin(settings.public_base_url)
    except ValueError:
        return False


def csrf_valid(request: Request) -> bool:
    if not request_origin_allowed(request, require_origin=settings.hosted_mode):
        return False
    cookie_token = get_csrf_token_from_request(request)
    header_token = get_csrf_token_from_header(request)
    if not cookie_token or not header_token:
        return False
    try:
        return secrets.compare_digest(cookie_token, header_token)
    except Exception:
        return False
