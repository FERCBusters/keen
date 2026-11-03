"""Signed webhook submissions: an HMAC over a timestamp and the exact body.

A sender signs ``<timestamp>.<body>`` with HMAC-SHA256 under its own secret and
sends the timestamp and signature as headers. The secret itself never travels,
so a captured request yields no credential. A signature is accepted within five
minutes of its timestamp and only once, so a captured request cannot be
replayed either.
"""

import hashlib
import hmac
import re
import secrets
import time

TIMESTAMP_HEADER = "X-Keen-Timestamp"
SIGNATURE_HEADER = "X-Keen-Signature"
TOLERANCE_SECONDS = 300
_TIMESTAMP = re.compile(r"[0-9]{1,12}")
_SIGNATURE = re.compile(r"v1=[0-9a-f]{64}")
# Signed with when there is no secret, so a refusal costs what a check does.
_ABSENT = secrets.token_urlsafe(32)


class SignatureRefused(Exception):
    """Signature headers that cannot be accepted; the message is safe to show."""


def new_secret() -> str:
    return "keen_ms_" + secrets.token_urlsafe(32)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Return the ``v1=<hex>`` signature header value for a submission."""
    message = timestamp.encode() + b"." + body
    return "v1=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def presented(
    timestamps: list[str], signatures: list[str], now: float | None = None
) -> tuple[str, str]:
    """Return a request's one timestamp and signature, checked for form and age."""
    if len(timestamps) != 1 or len(signatures) != 1:
        raise SignatureRefused(
            f"Sign the submission: send one {TIMESTAMP_HEADER} header and one "
            f"{SIGNATURE_HEADER} header"
        )
    timestamp, signature = timestamps[0].strip(), signatures[0].strip().lower()
    if not _TIMESTAMP.fullmatch(timestamp):
        raise SignatureRefused(
            f"{TIMESTAMP_HEADER} must be the current Unix time in whole seconds"
        )
    if abs((time.time() if now is None else now) - int(timestamp)) > TOLERANCE_SECONDS:
        raise SignatureRefused(
            f"{TIMESTAMP_HEADER} is more than five minutes from Keen's clock; sign "
            "each submission as it is sent and keep the sender's clock synchronised"
        )
    if not _SIGNATURE.fullmatch(signature):
        raise SignatureRefused(
            f"{SIGNATURE_HEADER} must be v1= followed by the hexadecimal "
            "HMAC-SHA256 of the timestamp, a full stop and the body"
        )
    return timestamp, signature


def matches(secret: str | None, timestamp: str, body: bytes, signature: str) -> bool:
    expected = sign(secret or _ABSENT, timestamp, body)
    return hmac.compare_digest(expected, signature) and secret is not None


def _claim_key(scope: str, signature: str) -> str:
    return f"keen:webhook:signed:{scope}:{signature}"


def claim(redis, scope: str, signature: str) -> bool:
    """Claim a signature once while its timestamp is acceptable; False if claimed."""
    claimed = redis.set(
        _claim_key(scope, signature), "1", nx=True, ex=2 * TOLERANCE_SECONDS + 60
    )
    # redis-py returns None when SET NX loses to an existing key.
    return bool(claimed)


def release(redis, scope: str, signature: str) -> None:
    """Let the sender retry a submission this request failed to record."""
    try:
        redis.delete(_claim_key(scope, signature))
    except Exception:
        # Without the store the claim lapses with the signature's window.
        pass
