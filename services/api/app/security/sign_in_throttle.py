"""Password sign-in throttling that an attacker cannot turn against the owner.

The policy, shared with OSPMAE, follows NIST SP 800-63B-4 section 3.2.2 and
the OWASP device-cookie protocol:

* A browser that has proved an account's password holds a device cookie for
  that account. Its attempts skip the per-address allowance and the account's
  throttle, and draw on their own allowance: five wrong passwords in a row
  revoke the cookie, after which the browser counts as unrecognised.
* Attempts from any other browser draw on the account's throttle. The first
  five consecutive failures are free; each later attempt opens a wait, 30
  seconds doubling to an hour, before the next one is heard, and after 100
  consecutive failures unrecognised browsers are refused until a correct
  password from a recognised browser or an administrator's password reset.
  A refused attempt never reaches password verification. Failures are
  forgotten 30 days after the last one.
* A correct password resets the account's count, and is answered with a fresh
  device cookie for the browser that sent it.

Every operation is a single Valkey command or a pipeline of them, so
concurrent attempts cannot share one opening; callers fail closed when Valkey
is unavailable. The throttle is keyed by a digest of the compared username
whether or not an account has it, so its answers reveal nothing about which
accounts exist.
"""

from __future__ import annotations

import hashlib
import secrets
import time

from fastapi import Response
from redis import Redis

from app.core.config import settings
from app.security.rate_limit import login_name

FREE_FAILURES = 5
FAILURE_CEILING = 100
FIRST_WAIT_SECONDS = 30
MAX_WAIT_SECONDS = 3600
FORGET_FAILURES_AFTER_SECONDS = 30 * 24 * 3600

DEVICE_FAILURES = 5
DEVICE_LIFETIME_SECONDS = 90 * 24 * 3600
DEVICES_PER_ACCOUNT = 20
_DEVICE_TOKEN_MAX_LENGTH = 128

LIMITED = "Too many login attempts. Please try again later."

_PREFIX = "keen:login"


def name_key(username: str) -> str:
    """A fixed-size digest of the compared name: Valkey never holds a password
    typed into the username field."""

    return hashlib.sha256(login_name(username).encode()).hexdigest()


def wait_seconds(failures: int) -> int:
    """The wait opened by an attempt made after ``failures`` failures."""

    if failures < FREE_FAILURES:
        return 0
    step = failures - FREE_FAILURES
    if step >= 7:  # 30 * 2**7 already passes the hour
        return MAX_WAIT_SECONDS
    return min(FIRST_WAIT_SECONDS * 2**step, MAX_WAIT_SECONDS)


def device_cookie_name() -> str:
    """A Secure cookie without a Domain takes the ``__Host-`` prefix, which a
    browser accepts only from this host over HTTPS, so a sibling site can
    neither plant nor shadow it."""

    name = "keen_device"
    if settings.cookie_secure and not settings.cookie_domain.strip():
        return "__Host-" + name
    return name


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _failures_key(key: str) -> str:
    return f"{_PREFIX}:failures:{key}"


def _gate_key(key: str) -> str:
    return f"{_PREFIX}:gate:{key}"


def _device_key(token_hash: str) -> str:
    return f"{_PREFIX}:device:{token_hash}"


def _account_devices_key(user_id: str) -> str:
    return f"{_PREFIX}:devices:{user_id}"


def recognised_device(r: Redis, token: str | None, key: str) -> str | None:
    """The device a presented cookie holds for this username, by digest."""

    raw = (token or "").strip()
    if not raw or len(raw) > _DEVICE_TOKEN_MAX_LENGTH:
        return None
    digest = _token_hash(raw)
    return digest if r.hget(_device_key(digest), "name") == key else None


def reserve_unrecognised_attempt(r: Redis, key: str) -> tuple[bool, int]:
    """Admit one attempt from an unrecognised browser, or say how long to wait.

    The attempt is counted as a failure before the password is checked; a
    refused attempt takes its count back, and a correct password clears it.
    """

    count_key = _failures_key(key)
    attempt = int(r.incr(count_key))
    if attempt > FAILURE_CEILING:
        r.decr(count_key)
        return False, MAX_WAIT_SECONDS
    previous = attempt - 1
    wait = wait_seconds(previous)
    if wait and not r.set(_gate_key(key), "1", nx=True, ex=wait):
        r.decr(count_key)
        return False, max(1, int(r.ttl(_gate_key(key))))
    r.expire(count_key, FORGET_FAILURES_AFTER_SECONDS)
    return True, 0


def clear_throttle(r: Redis, key: str) -> None:
    """A correct password, or an administrator's reset, ends the count."""

    r.delete(_failures_key(key), _gate_key(key))


def device_failed(r: Redis, device: str) -> None:
    """Count a wrong password from a recognised browser; the fifth in a row
    revokes its recognition."""

    if int(r.hincrby(_device_key(device), "failures", 1)) >= DEVICE_FAILURES:
        revoke_device(r, device)


def revoke_device(r: Redis, device: str) -> None:
    owner = r.hget(_device_key(device), "user")
    r.delete(_device_key(device))
    # The client decodes responses, so stored values come back as text.
    if isinstance(owner, str) and owner:
        r.zrem(_account_devices_key(owner), device)


def revoke_account_devices(r: Redis, user_id: str, *, keep: str | None = None) -> None:
    """End every recognised browser of an account except ``keep``."""

    for device in r.zrange(_account_devices_key(user_id), 0, -1):
        if isinstance(device, str) and device != keep:
            revoke_device(r, device)


def issue_device(
    r: Redis, *, user_id: str, key: str, replaces: str | None = None
) -> str:
    """Record a recognised browser for this account and return its token.

    The browser's previous token is retired, and an account keeps at most
    its newest DEVICES_PER_ACCOUNT browsers.
    """

    if replaces:
        revoke_device(r, replaces)
    token = secrets.token_urlsafe(32)
    digest = _token_hash(token)
    account = _account_devices_key(user_id)
    pipe = r.pipeline()
    pipe.hset(
        _device_key(digest), mapping={"name": key, "user": user_id, "failures": 0}
    )
    pipe.expire(_device_key(digest), DEVICE_LIFETIME_SECONDS)
    pipe.zadd(account, {digest: time.time()})
    pipe.expire(account, DEVICE_LIFETIME_SECONDS)
    pipe.execute()
    for surplus in r.zrevrange(account, DEVICES_PER_ACCOUNT, -1):
        revoke_device(r, str(surplus))
    return token


def set_device_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=device_cookie_name(),
        value=token,
        max_age=DEVICE_LIFETIME_SECONDS,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        domain=settings.cookie_domain.strip() or None,
        path="/",
    )
