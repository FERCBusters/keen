"""Signatures for outbound webhooks, in Keen's signed-submission scheme.

Each delivery carries ``X-Keen-Timestamp`` (Unix seconds) and
``X-Keen-Signature: v1=<hex>``, the HMAC-SHA256 of ``<timestamp>.<body>``
under the shared secret, so a receiver can refuse stale and repeated
deliveries as well as altered ones.
"""

import time

from app.security.signed_webhooks import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign


def signature_headers(
    secret: str, body: bytes, now: float | None = None
) -> dict[str, str]:
    """Return the timestamp and signature headers for a delivery of ``body``."""
    timestamp = str(int(time.time() if now is None else now))
    return {
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: sign(secret, timestamp, body),
    }
