from __future__ import annotations

from typing import Optional, Tuple

from fastapi import Request
from redis import Redis


def client_ip(request: Request) -> Optional[str]:
    """Best-effort client IP for rate limiting.

    NOTE: these headers must be set by a trusted reverse proxy. If your API is
    directly internet-facing, relying on X-Forwarded-For is unsafe.
    """
    try:
        xff = (request.headers.get("x-forwarded-for") or "").strip()
        if xff:
            return xff.split(",")[0].strip() or None
        xri = (request.headers.get("x-real-ip") or "").strip()
        if xri:
            return xri
        return getattr(getattr(request, "client", None), "host", None)
    except Exception:
        return None


def fixed_window_allow(
    r: Redis, key: str, limit: int, window_seconds: int, fail_closed: bool = False
) -> Tuple[bool, int]:
    """Fixed-window rate limiting using Redis/Valkey.

    Returns (allowed, retry_after_seconds).

    Implementation:
      - INCR a counter
      - Ensure the key has an expiry
      - Read TTL for Retry-After

    Args:
        r: Redis client
        key: Rate limit key
        limit: Maximum requests allowed
        window_seconds: Time window in seconds
        fail_closed: If True, deny requests when Redis is unavailable.
                    If False, allow requests (fail open) when Redis is unavailable.
    """
    if limit <= 0 or window_seconds <= 0:
        return True, 0

    try:
        # Pipeline for minimal round-trips.
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = pipe.execute()

        try:
            count_i = int(count)
        except Exception:
            count_i = limit + 1

        try:
            ttl_i = int(ttl)
        except Exception:
            ttl_i = int(window_seconds)

        # Redis TTL can be -1 (no expiry) or -2 (missing). Ensure the key expires.
        if ttl_i < 0:
            try:
                r.expire(key, int(window_seconds))
            except Exception:
                pass
            ttl_i = int(window_seconds)

        if count_i > int(limit):
            return False, ttl_i
        return True, 0
    except Exception:
        # Redis unavailable
        if fail_closed:
            return False, window_seconds
        return True, 0
