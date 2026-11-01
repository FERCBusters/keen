"""Ed25519 verification for KEEN agent requests, batches and key proofs.

Agents sign each request with RFC 9421 HTTP Message Signatures over a fixed
set of components (the body they cover is the request as encrypted to KEEN;
see app/agents/server_keys.py), sign each batch manifest of evidence hashes,
and prove possession of a new key when enrolling or renewing. KEEN stores only public
keys. Every signed message names its purpose so a signature made for one
purpose is never valid for another.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.agents.ueu.jcs import canonical

OPERATIONS = ("otlp-logs", "heartbeat", "renew")
WINDOW_SECONDS = 300
COMPONENTS = (
    '("@method" "keen-agent-operation" "keen-encryption-key" "content-digest" '
    '"content-type")'
)
_INPUT = re.compile(
    r'keen=\("@method" "keen-agent-operation" "keen-encryption-key" "content-digest"'
    r' "content-type"\)'
    r';created=([0-9]{1,12});keyid="([0-9a-f-]{36}:[0-9a-f]{16})"'
    r';nonce="([A-Za-z0-9_-]{22})";alg="ed25519"'
)
_SIGNATURE = re.compile(r"keen=:([A-Za-z0-9+/]{86}==):")
_DIGEST = re.compile(r"sha-256=:([A-Za-z0-9+/]{43}=):")
_ENCRYPTION_KEY = re.compile(r"[0-9a-f]{16}")
_PUBLIC_KEY = re.compile(r"[A-Za-z0-9_-]{43}")
_B64URL_SIGNATURE = re.compile(r"[A-Za-z0-9_-]{86}")
KEY_ID = re.compile(r"([0-9a-f-]{36}):([0-9a-f]{16})")


class SignatureError(ValueError):
    """A signature, digest or key is missing, malformed or does not verify."""


@dataclass(frozen=True)
class SignedRequest:
    key_id: str
    agent_id: str
    nonce: str
    created: int
    encryption_key: str
    digest: str
    params: str
    signature: bytes


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def public_key(value: str) -> Ed25519PublicKey:
    """Parse an unpadded base64url Ed25519 public key."""
    if not isinstance(value, str) or not _PUBLIC_KEY.fullmatch(value):
        raise SignatureError("invalid public key")
    raw = unb64url(value)
    if len(raw) != 32 or b64url(raw) != value:
        raise SignatureError("invalid public key")
    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise SignatureError("invalid public key") from exc


def key_id(agent_id: str, encoded_public_key: str) -> str:
    """<agent id>:<first 16 hex digits of SHA-256 of the raw public key>."""
    raw = unb64url(encoded_public_key)
    return agent_id + ":" + hashlib.sha256(raw).hexdigest()[:16]


def content_digest(body: bytes) -> str:
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"


def signature_base(
    method, operation, encryption_key, digest, content_type, params
) -> bytes:
    return (
        f'"@method": {method.upper()}\n'
        f'"keen-agent-operation": {operation}\n'
        f'"keen-encryption-key": {encryption_key}\n'
        f'"content-digest": {digest}\n'
        f'"content-type": {content_type}\n'
        f'"@signature-params": {params}'
    ).encode()


def parse_request(headers, operation: str, *, now: float | None = None):
    """Parse the signature headers and check the operation and time window.

    The body is not needed yet: the digest header is covered by the
    signature, and the body is compared with it once it has been read.
    """
    if operation not in OPERATIONS:
        raise SignatureError("unknown operation")
    signature_input = headers.get("signature-input", "")
    signature = headers.get("signature", "")
    digest = headers.get("content-digest", "")
    encryption_key = headers.get("keen-encryption-key", "")
    match = _INPUT.fullmatch(signature_input)
    value = _SIGNATURE.fullmatch(signature)
    if (
        not match
        or not value
        or not _DIGEST.fullmatch(digest)
        or not _ENCRYPTION_KEY.fullmatch(encryption_key)
        or headers.get("keen-agent-operation", "") != operation
    ):
        raise SignatureError("missing or malformed signature")
    created = int(match[1])
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    if abs(now - created) > WINDOW_SECONDS:
        raise SignatureError("signature outside the time window")
    try:
        raw = base64.b64decode(value[1], validate=True)
    except binascii.Error as exc:
        raise SignatureError("missing or malformed signature") from exc
    return SignedRequest(
        key_id=match[2],
        agent_id=match[2].split(":", 1)[0],
        nonce=match[3],
        created=created,
        encryption_key=encryption_key,
        digest=digest,
        params=signature_input[len("keen=") :],
        signature=raw,
    )


def verify_request(
    signed: SignedRequest, encoded_public_key: str, method, operation, content_type
):
    base = signature_base(
        method,
        operation,
        signed.encryption_key,
        signed.digest,
        content_type,
        signed.params,
    )
    try:
        public_key(encoded_public_key).verify(signed.signature, base)
    except InvalidSignature as exc:
        raise SignatureError("signature invalid") from exc


def verify_digest(signed: SignedRequest, body: bytes):
    if content_digest(bytes(body)) != signed.digest:
        raise SignatureError("content digest mismatch")


def verify_detached(encoded_public_key: str, message: bytes, signature: str):
    """Verify an unpadded base64url Ed25519 signature over message bytes."""
    if not isinstance(signature, str) or not _B64URL_SIGNATURE.fullmatch(signature):
        raise SignatureError("malformed signature")
    try:
        public_key(encoded_public_key).verify(unb64url(signature), message)
    except InvalidSignature as exc:
        raise SignatureError("signature invalid") from exc


def verify_proof(encoded_public_key: str, statement: dict, proof: str):
    """Check proof of possession: a signature over a canonical statement."""
    verify_detached(encoded_public_key, canonical(statement), proof)
