"""The digest and unpadded base64url encoding Keen's credentials share."""

from __future__ import annotations

import base64
import hashlib


def digest(value: str) -> str:
    """The SHA-256 of a secret, as Keen stores and compares it."""
    return hashlib.sha256(value.encode()).hexdigest()


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
