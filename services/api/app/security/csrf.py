from __future__ import annotations

import secrets

from fastapi import Request, Response

from app.core.config import settings


def csrf_cookie_name() -> str:
    # Allow override, but keep a stable default.
    return (
        getattr(settings, "csrf_cookie_name", "") or "keen_csrf"
    ).strip() or "keen_csrf"


def csrf_header_name() -> str:
    # Default mirrors common conventions.
    return (
        getattr(settings, "csrf_header_name", "") or "X-CSRF-Token"
    ).strip() or "X-CSRF-Token"


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
    domain = (settings.cookie_domain or "").strip() or None

    # CSRF cookies should be readable by JS so the UI can echo the token
    # in a header.
    samesite = (getattr(settings, "csrf_cookie_samesite", "") or "strict").lower()
    if samesite not in ("lax", "strict", "none"):
        samesite = "strict"

    response.set_cookie(
        key=csrf_cookie_name(),
        value=token,
        max_age=int(settings.session_ttl_seconds),
        httponly=False,
        secure=bool(settings.cookie_secure),
        samesite=samesite,
        domain=domain,
        path="/",
    )


def clear_csrf_cookie(response: Response) -> None:
    domain = (settings.cookie_domain or "").strip() or None
    response.delete_cookie(key=csrf_cookie_name(), domain=domain, path="/")


def csrf_valid(request: Request) -> bool:
    cookie_token = get_csrf_token_from_request(request)
    header_token = get_csrf_token_from_header(request)
    if not cookie_token or not header_token:
        return False
    try:
        return secrets.compare_digest(cookie_token, header_token)
    except Exception:
        return False
