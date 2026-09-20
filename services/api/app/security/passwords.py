from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

# -----------------------------------------------------------------------------
# Password hashing (stdlib-only)
# -----------------------------------------------------------------------------
#
# Format:
#   pbkdf2_sha256$<iterations>$<salt_b64url>$<hash_b64url>
#
# Using PBKDF2-HMAC-SHA256 from Python's stdlib keeps the runtime dependency
# surface small while providing a modern, slow password hash.


_ALGO = "pbkdf2_sha256"
_DEFAULT_ITERS = 260_000  # reasonable baseline for 2026-era CPUs
_SALT_BYTES = 16


@dataclass(frozen=True)
class ParsedHash:
    algo: str
    iterations: int
    salt: bytes
    digest: bytes


def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def _pbkdf2_sha256(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, int(iterations)
    )


def _parse_hash(password_hash: str) -> ParsedHash | None:
    if not password_hash:
        return None
    parts = str(password_hash).split("$")
    if len(parts) != 4:
        return None
    algo, iters_s, salt_s, digest_s = parts
    if algo != _ALGO:
        return None
    try:
        iters = int(iters_s)
        salt = _b64u_decode(salt_s)
        digest = _b64u_decode(digest_s)
    except Exception:
        return None
    if iters <= 0 or not salt or not digest:
        return None
    return ParsedHash(algo=algo, iterations=iters, salt=salt, digest=digest)


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        # Keep validation minimal; UI can do more.
        raise ValueError("Password must be at least 8 characters")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _pbkdf2_sha256(password, salt, _DEFAULT_ITERS)
    return f"{_ALGO}${_DEFAULT_ITERS}${_b64u_encode(salt)}${_b64u_encode(digest)}"


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    parsed = _parse_hash(password_hash)
    if not parsed:
        return False
    try:
        calc = _pbkdf2_sha256(password, parsed.salt, parsed.iterations)
    except Exception:
        return False
    return hmac.compare_digest(calc, parsed.digest)
