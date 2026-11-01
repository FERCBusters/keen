"""Keen Agent's sealed enrolment, for tests that enrol agents against a live
Keen. Mirrors keen-agent: the credential's last segment pins Keen's identity
key; the request, credential included, is HPKE-sealed to Keen's signed
encryption key; the grant must open with the request's one-time key."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time

from cryptography.hazmat.primitives import hpke
from cryptography.hazmat.primitives.asymmetric import mlkem, x25519
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SEALED = "application/vnd.keen.sealed"
SUITE = hpke.Suite(
    hpke.KEM.MLKEM768_X25519, hpke.KDF.HKDF_SHA256, hpke.AEAD.AES_256_GCM
)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def canonical(value) -> bytes:
    # RFC 8785 for the flat ASCII string and integer objects used here.
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def server_key(client, credential: str) -> dict:
    """Fetch and verify Keen's encryption key against the credential's pin."""
    pinned = credential.rsplit(".", 1)[1]
    response = client.get("/api/v1/agents/server-key")
    assert response.status_code == 200, response.status_code
    doc = response.json()
    raw = unb64url(doc["identity"]["public_key"])
    assert b64url(hashlib.sha256(raw).digest()[:16]) == pinned, "identity mismatch"
    statement = doc["encryption_key"]
    Ed25519PublicKey.from_public_bytes(raw).verify(
        unb64url(doc["signature"]), canonical(statement)
    )
    assert statement["identity"] == pinned
    return statement


def enroll(client, statement: dict, credential: str, **profile) -> int:
    """Send one sealed enrolment; return its status. profile: name, nonce."""
    key = Ed25519PrivateKey.generate()
    public_key = b64url(key.public_key().public_bytes_raw())
    created, proof_nonce = int(time.time()), secrets.token_urlsafe(32)
    proof = key.sign(
        canonical(
            {
                "context": "keen-agent-enroll-v2",
                "created": created,
                "credential_sha256": hashlib.sha256(credential.encode()).hexdigest(),
                "proof_nonce": proof_nonce,
                "public_key": public_key,
            }
        )
    )
    body = {
        "credential": credential,
        "public_key": public_key,
        "created": created,
        "proof_nonce": proof_nonce,
        "proof": b64url(proof),
        **profile,
    }
    raw = unb64url(statement["public_key"])
    recipient = hpke.MLKEM768X25519PublicKey(
        mlkem.MLKEM768PublicKey.from_public_bytes(raw[:1184]),
        x25519.X25519PublicKey.from_public_bytes(raw[1184:]),
    )
    response_key = secrets.token_bytes(32)
    info = b"\x00".join(
        (b"keen-agent-request-v1", b"enroll", statement["key_id"].encode(), b"")
    )
    response = client.post(
        "/api/v1/agents/enroll",
        content=SUITE.encrypt(
            response_key + json.dumps(body).encode(), recipient, info
        ),
        headers={
            "Content-Type": SEALED,
            "Keen-Encryption-Key": statement["key_id"],
            "Keen-Agent-Operation": "enroll",
        },
    )
    if response.status_code == 201:
        sealed = response.content
        AESGCM(response_key).decrypt(
            sealed[:12], sealed[12:], b"keen-agent-response-v1\x00enroll\x00201"
        )
    return response.status_code
