"""Signatures for outbound webhook bodies."""

import hashlib
import hmac


def sign_body(secret: str, body: bytes) -> str:
    """Return the ``sha256=<hex>`` HMAC header value for a request body."""
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"
