"""KEEN's own keys for end-to-end encrypted agent traffic.

TLS protects agent traffic only as far as whatever holds a certificate for
KEEN's hostname: a reverse proxy, a load balancer, an inspecting middlebox,
or an impostor that obtained one. keen-agent therefore encrypts every request
to KEEN itself, and trusts a response only when it is encrypted with a key the
agent sent inside that request.

The identity key (Ed25519) is KEEN's long-term name towards its agents. Its
fingerprint ends every enrolment code and bootstrap key, so an agent checks,
before it sends anything, that it is talking to the KEEN that issued its
credential. It signs each encryption key. It does not rotate: replacing it
(scripts/agent_server_key.py) means re-enrolling every agent.

Encryption keys (HPKE: MLKEM768-X25519, HKDF-SHA256, AES-256-GCM) receive
every agent request. A new key is made every ROTATE_AFTER; agents may encrypt
to a key until its not_after, and its private half is erased DESTROY_GRACE
later, so traffic recorded on the wire cannot later be opened with keys taken
from KEEN. The hybrid KEM keeps that true against a future quantum computer.

Private halves are stored encrypted with KEEN_INTEGRATION_SECRET_KEY.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from cryptography.exceptions import InvalidTag
from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives import hpke
from cryptography.hazmat.primitives.asymmetric import mlkem, x25519
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.ueu.jcs import canonical
from app.core.datetime_utils import utc_now_naive
from app.db.models import AgentServerKey
from app.integrations.errors import IntegrationError
from app.integrations.transport import decrypt, encrypt
from app.security.agent_signatures import b64url, unb64url
from app.services.errors import DomainError

SUITE = hpke.Suite(
    hpke.KEM.MLKEM768_X25519, hpke.KDF.HKDF_SHA256, hpke.AEAD.AES_256_GCM
)
KEM, KDF, AEAD = "MLKEM768-X25519", "HKDF-SHA256", "AES-256-GCM"
ENC_SIZE = 1120
PUBLIC_SIZE = 1216
RESPONSE_KEY_SIZE = 32
TAG_SIZE = 16
NONCE_SIZE = 12
# What sealing adds to a request body: the encapsulated key, the response key
# and the AEAD tag.
SEALED_OVERHEAD = ENC_SIZE + RESPONSE_KEY_SIZE + TAG_SIZE

SEALED = "application/vnd.keen.sealed"
SEALED_RESPONSE = "application/vnd.keen.sealed-response"
KEY_HEADER = "keen-encryption-key"
REQUEST_CONTEXT = b"keen-agent-request-v1"
RESPONSE_CONTEXT = b"keen-agent-response-v1"
STATEMENT_CONTEXT = "keen-agent-encryption-key-v1"

ROTATE_AFTER = timedelta(days=30)
VALID_FOR = timedelta(days=37)
DESTROY_GRACE = timedelta(hours=1)

ENCRYPTION_KEY_ID = re.compile(r"[0-9a-f]{16}")
FINGERPRINT = re.compile(r"[A-Za-z0-9_-]{22}")


def fingerprint(raw_public_key: bytes) -> str:
    """The identity key's fingerprint: 128 bits of SHA-256, base64url."""
    return b64url(hashlib.sha256(raw_public_key).digest()[:16])


def stamp(when: datetime) -> str:
    return when.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def secret_missing() -> DomainError:
    return DomainError(
        503, "Keen Agent encryption needs KEEN_INTEGRATION_SECRET_KEY on the API"
    )


def seal_private(raw: bytes) -> str:
    try:
        return encrypt({"key": b64url(raw)})
    except IntegrationError:
        raise secret_missing() from None


def open_private(sealed: str) -> bytes:
    try:
        return unb64url(decrypt(sealed)["key"])
    except IntegrationError:
        raise secret_missing() from None
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise DomainError(
            503,
            "KEEN's agent keys cannot be read with the configured "
            "KEEN_INTEGRATION_SECRET_KEY",
        ) from None


@dataclass(frozen=True)
class Identity:
    fingerprint: str
    public_key: str
    private: Ed25519PrivateKey


def active(db: Session, purpose: str) -> AgentServerKey | None:
    return (
        db.query(AgentServerKey)
        .filter_by(purpose=purpose, state="active")
        .populate_existing()
        .one_or_none()
    )


def insert(db: Session, row: AgentServerKey) -> AgentServerKey:
    """Add a key unless another request made one first; return the active one."""
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:
        pass
    found = active(db, row.purpose)
    if found is None:  # pragma: no cover - the unique index guarantees one
        raise DomainError(503, "KEEN's agent keys are being changed; retry")
    return found


def load_identity(row: AgentServerKey) -> Identity:
    if row.private_key is None:  # pragma: no cover - only active keys are loaded
        raise DomainError(503, "KEEN's identity key has been erased")
    private = Ed25519PrivateKey.from_private_bytes(open_private(row.private_key))
    public = private.public_key().public_bytes_raw()
    if b64url(public) != row.public_key or fingerprint(public) != row.key_id:
        raise DomainError(503, "KEEN's identity key is inconsistent")
    return Identity(row.key_id, row.public_key, private)


def identity(db: Session) -> Identity:
    """Return KEEN's identity key, creating it on first use."""
    row = active(db, "identity")
    if row is None:
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes_raw()
        row = insert(
            db,
            AgentServerKey(
                key_id=fingerprint(public),
                purpose="identity",
                public_key=b64url(public),
                private_key=seal_private(private.private_bytes_raw()),
                state="active",
                created_at=utc_now_naive(),
            ),
        )
    return load_identity(row)


def current_fingerprint(db: Session) -> str | None:
    """The identity fingerprint, if KEEN has one yet; never creates it."""
    row = active(db, "identity")
    return row.key_id if row is not None else None


def new_encryption_key(ident: Identity, now: datetime) -> AgentServerKey:
    pq = mlkem.MLKEM768PrivateKey.generate()
    classical = x25519.X25519PrivateKey.generate()
    public = (
        pq.public_key().public_bytes_raw() + classical.public_key().public_bytes_raw()
    )
    key_id = hashlib.sha256(public).hexdigest()[:16]
    not_after = now + VALID_FOR
    statement = {
        "aead": AEAD,
        "context": STATEMENT_CONTEXT,
        "identity": ident.fingerprint,
        "issued_at": stamp(now),
        "kdf": KDF,
        "kem": KEM,
        "key_id": key_id,
        "not_after": stamp(not_after),
        "public_key": b64url(public),
    }
    return AgentServerKey(
        key_id=key_id,
        purpose="encryption",
        public_key=b64url(public),
        private_key=seal_private(
            pq.private_bytes_raw() + classical.private_bytes_raw()
        ),
        state="active",
        created_at=now,
        statement=statement,
        signature=b64url(ident.private.sign(canonical(statement))),
        not_after=not_after.replace(microsecond=0),
    )


def rotate_if_due(db: Session, ident: Identity, now: datetime) -> AgentServerKey:
    """Return the active encryption key, replacing it when it is due."""
    current = active(db, "encryption")
    if (
        current is not None
        and current.statement is not None
        and current.statement.get("identity") == ident.fingerprint
        and now - current.created_at < ROTATE_AFTER
    ):
        return current
    try:
        with db.begin_nested():
            locked = (
                db.query(AgentServerKey)
                .filter_by(purpose="encryption", state="active")
                .populate_existing()
                .with_for_update()
                .one_or_none()
            )
            if (
                locked is not None
                and locked.statement is not None
                and locked.statement.get("identity") == ident.fingerprint
                and now - locked.created_at < ROTATE_AFTER
            ):
                return locked
            if locked is not None:
                locked.state, locked.retired_at = "retired", now
                db.flush()
            row = new_encryption_key(ident, now)
            db.add(row)
            db.flush()
            return row
    except IntegrityError:
        found = active(db, "encryption")
        if found is None:  # pragma: no cover - the unique index guarantees one
            raise DomainError(503, "KEEN's agent keys are being changed; retry")
        return found


def destroy_expired(db: Session, now: datetime) -> int:
    """Erase private halves agents may no longer use. Returns how many."""
    rows = (
        db.query(AgentServerKey)
        .filter(
            AgentServerKey.purpose == "encryption",
            AgentServerKey.state != "destroyed",
            AgentServerKey.not_after < now - DESTROY_GRACE,
        )
        .all()
    )
    for row in rows:
        row.state, row.private_key, row.destroyed_at = "destroyed", None, now
        row.retired_at = row.retired_at or now
        forget_opened(row.key_id)
    db.flush()
    return len(rows)


def maintain(db: Session, now: datetime | None = None) -> AgentServerKey:
    """Rotate the encryption key when due and erase expired private halves.

    Runs on every server-key request and from the worker; the caller commits.
    """
    now = now or utc_now_naive()
    ident = identity(db)
    current = rotate_if_due(db, ident, now)
    destroy_expired(db, now)
    return current


def document(db: Session) -> dict:
    """What an agent fetches to learn, and verify, where to encrypt."""
    current = maintain(db)
    ident = identity(db)
    return {
        "identity": {
            "algorithm": "ed25519",
            "fingerprint": ident.fingerprint,
            "public_key": ident.public_key,
        },
        "encryption_key": current.statement,
        "signature": current.signature,
    }


def active_key_id(db: Session) -> str | None:
    row = active(db, "encryption")
    return row.key_id if row is not None else None


# Opened private keys, by key ID: (stored ciphertext, usable until, key).
# Entries leave once their key may no longer be used, so a running process
# does not keep keys the database has erased.
_OPENED: dict[str, tuple[str, datetime, hpke.MLKEM768X25519PrivateKey]] = {}
_OPENED_LOCK = threading.Lock()


def load_encryption_private(
    key_id: str, sealed: str, usable_until: datetime, now: datetime
) -> hpke.MLKEM768X25519PrivateKey:
    with _OPENED_LOCK:
        for kid in [k for k, v in _OPENED.items() if v[1] < now]:
            del _OPENED[kid]
        cached = _OPENED.get(key_id)
        if cached is not None and cached[0] == sealed:
            return cached[2]
    raw = open_private(sealed)
    if len(raw) != 96:
        raise DomainError(503, "KEEN's encryption key is inconsistent")
    private = hpke.MLKEM768X25519PrivateKey(
        mlkem.MLKEM768PrivateKey.from_seed_bytes(raw[:64]),
        x25519.X25519PrivateKey.from_private_bytes(raw[64:]),
    )
    with _OPENED_LOCK:
        _OPENED[key_id] = (sealed, usable_until, private)
    return private


def forget_opened(key_id: str | None = None) -> None:
    """Drop opened keys from this process (one, or all)."""
    with _OPENED_LOCK:
        if key_id is None:
            _OPENED.clear()
        else:
            _OPENED.pop(key_id, None)


def unknown_key() -> DomainError:
    # 421: this KEEN cannot open a request encrypted to that key. The agent
    # fetches the server-key document again and re-encrypts.
    return DomainError(
        421, "Unknown or expired encryption key; fetch /v1/agents/server-key"
    )


def encryption_private(db: Session, key_id: str, now: datetime | None = None):
    now = now or utc_now_naive()
    row = db.get(AgentServerKey, key_id, populate_existing=True)
    if (
        row is None
        or row.purpose != "encryption"
        or row.private_key is None
        or row.not_after is None
        or row.not_after + DESTROY_GRACE < now
    ):
        forget_opened(key_id)
        raise unknown_key()
    return load_encryption_private(
        key_id, row.private_key, row.not_after + DESTROY_GRACE, now
    )


def request_info(operation: str, key_id: str, agent_key_id: str) -> bytes:
    """HPKE info: binds a sealed body to its operation, KEEN key and sender."""
    return b"\x00".join(
        (
            REQUEST_CONTEXT,
            operation.encode(),
            key_id.encode(),
            agent_key_id.encode(),
        )
    )


@dataclass(frozen=True)
class Opened:
    """A decrypted agent request and the key its response is sealed with."""

    body: bytes
    response_key: bytes
    operation: str
    key_id: str


def sealed_content_type(headers) -> bool:
    return headers.get("content-type", "").split(";")[0].strip() == SEALED


def open_request(
    db: Session, headers, body: bytes, operation: str, agent_key_id: str = ""
) -> Opened:
    """Decrypt a sealed agent request. KEEN accepts no plaintext from keen-agent."""
    if not sealed_content_type(headers):
        raise DomainError(
            415,
            "keen-agent requests must be encrypted to KEEN; upgrade keen-agent "
            "to 0.3 or later",
        )
    if headers.get("content-encoding", "identity") != "identity":
        raise DomainError(415, "Sealed requests are not compressed")
    key_id = headers.get(KEY_HEADER, "")
    if not ENCRYPTION_KEY_ID.fullmatch(key_id):
        raise DomainError(400, "Missing or malformed Keen-Encryption-Key")
    private = encryption_private(db, key_id)
    if len(body) < ENC_SIZE + TAG_SIZE + RESPONSE_KEY_SIZE:
        raise DomainError(400, "Sealed request could not be opened")
    try:
        plain = SUITE.decrypt(
            bytes(body), private, request_info(operation, key_id, agent_key_id)
        )
    except InvalidTag:
        raise DomainError(400, "Sealed request could not be opened") from None
    return Opened(
        body=plain[RESPONSE_KEY_SIZE:],
        response_key=plain[:RESPONSE_KEY_SIZE],
        operation=operation,
        key_id=key_id,
    )


def response_aad(operation: str, status: int) -> bytes:
    return b"\x00".join((RESPONSE_CONTEXT, operation.encode(), str(status).encode()))


def seal_response(opened: Opened, status: int, payload: bytes) -> bytes:
    """Encrypt a response with the key the agent sent in its request.

    Only a holder of KEEN's encryption key could have read that key, so a
    response that decrypts is KEEN's, and answers that one request.
    """
    nonce = os.urandom(NONCE_SIZE)
    return nonce + AESGCM(opened.response_key).encrypt(
        nonce, payload, response_aad(opened.operation, status)
    )


def reset_identity(db: Session, now: datetime | None = None) -> Identity:
    """Replace KEEN's identity key and erase every agent key it vouched for.

    Every agent must then enrol again with a new code or bootstrap key; the
    caller commits.
    """
    now = now or utc_now_naive()
    for row in (
        db.query(AgentServerKey)
        .filter(AgentServerKey.state != "destroyed")
        .populate_existing()
        .with_for_update()
        .all()
    ):
        row.state, row.private_key, row.destroyed_at = "destroyed", None, now
        row.retired_at = row.retired_at or now
    db.flush()
    forget_opened()
    ident = identity(db)
    rotate_if_due(db, ident, now)
    return ident


def scheduled_maintenance(db: Session) -> dict:
    """The worker's hourly pass. Nothing happens until KEEN has an identity
    key, which it makes when it first issues an agent credential."""
    if current_fingerprint(db) is None:
        return {"identity": False}
    try:
        current = maintain(db)
    except DomainError as error:
        db.rollback()
        return {"identity": True, "error": error.detail}
    db.commit()
    return {"identity": True, "encryption_key": current.key_id}
