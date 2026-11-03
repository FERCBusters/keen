from __future__ import annotations

import hashlib
import ipaddress
import unicodedata
from typing import Tuple

from redis import Redis

from app.security.client_ip import request_ip as client_ip

__all__ = [
    "address_allowance",
    "address_window_allow",
    "client_ip",
    "fixed_window_allow",
    "login_limit_key",
    "login_name",
]

# Unicode Default_Ignorable_Code_Point ranges, plus the object replacement
# character that RFC 4518 also maps to nothing. Directories ignore these when
# they compare names, so they never distinguish one username from another.
_IGNORABLE = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0xFFFC, 0xFFFC),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def _ignorable(character: str) -> bool:
    point = ord(character)
    if unicodedata.category(character) == "Cc" and not character.isspace():
        return True
    return any(low <= point <= high for low, high in _IGNORABLE)


def login_name(username: str) -> str:
    """The form in which sign-in names are compared for rate limiting.

    Directories match names regardless of width, case, invisible characters and
    runs of spaces (Active Directory folds fullwidth forms; RFC 4518 drops
    ignorable characters and collapses spaces), so every such variant of a
    name has one form. An empty form means the username is effectively blank.
    """
    folded = unicodedata.normalize(
        "NFKC", unicodedata.normalize("NFKC", username).casefold()
    )
    visible = "".join(c for c in folded if not _ignorable(c))
    return " ".join(visible.split())


def login_limit_key(username: str) -> str:
    """The Valkey key of the per-username sign-in allowance a username draws on.

    The key holds a digest of the name's compared form, so its size is fixed
    and a password typed into the username field never appears in it.
    """
    digest = hashlib.sha256(login_name(username).encode()).hexdigest()
    return f"keen:rl:login:user:{digest}"


def address_allowance(address: str | None) -> str | None:
    """The part of a client address that a per-address allowance counts.

    One IPv6 subscriber or host normally holds a whole /64 network, so every
    address in it shares one allowance; an IPv4 address has its own.
    """
    if address is None:
        return None
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((ip, 64), strict=False))
    return str(ip)


def address_window_allow(
    r: Redis,
    scope: str,
    address: str | None,
    limit: int,
    window_seconds: int,
    fail_closed: bool = False,
) -> Tuple[bool, int]:
    """A fixed-window allowance for one client address.

    Every per-address limit goes through here, so each counts an IPv6 /64
    network as one address. Clients whose address is unknown share one
    allowance. The contract tests refuse a client address in any other
    allowance key.
    """
    allowance = address_allowance(address) or "unknown"
    return fixed_window_allow(
        r, f"keen:rl:{scope}:ip:{allowance}", limit, window_seconds, fail_closed
    )


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
