"""A test producer that speaks KEEN's agent protocol the way keen-agent does.

It holds an Ed25519 key, pins KEEN's identity key from its credential,
encrypts every request to KEEN's current encryption key (HPKE) and opens only
responses sealed with the key it sent, enrols, builds UEU envelopes in KEEN's
profile, chains and signs them and signs every request (RFC 9421). The golden
vectors in fixtures/ueu-vectors.json tie this helper, KEEN's verifier and the
Go agent to the same bytes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timezone

from app.agents.ueu.jcs import canonical
from cryptography.hazmat.primitives import hpke
from cryptography.hazmat.primitives.asymmetric import mlkem, x25519
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TIME = "%Y-%m-%dT%H:%M:%S.%f"
COMPONENTS = (
    '("@method" "keen-agent-operation" "keen-encryption-key" "content-digest" '
    '"content-type")'
)
SEALED = "application/vnd.keen.sealed"
SEALED_RESPONSE = "application/vnd.keen.sealed-response"
SUITE = hpke.Suite(
    hpke.KEM.MLKEM768_X25519, hpke.KDF.HKDF_SHA256, hpke.AEAD.AES_256_GCM
)


class FakeValkey:
    """Enough of Valkey for request nonces and fixed-window counters."""

    def __init__(self):
        self.keys = set()
        self.counts = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True

    def pipeline(self):
        valkey, results = self, []

        class Pipe:
            def incr(self, key):
                valkey.counts[key] = valkey.counts.get(key, 0) + 1
                results.append(valkey.counts[key])

            def ttl(self, key):
                results.append(60)

            def execute(self):
                return list(results)

        return Pipe()

    def expire(self, key, seconds):
        return True


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_server_key(doc, pinned):
    """Check a server-key document the way keen-agent does."""
    raw = unb64url(doc["identity"]["public_key"])
    assert b64url(hashlib.sha256(raw).digest()[:16]) == pinned
    assert doc["identity"]["fingerprint"] == pinned
    statement = doc["encryption_key"]
    Ed25519PublicKey.from_public_bytes(raw).verify(
        unb64url(doc["signature"]), canonical(statement)
    )
    assert statement["context"] == "keen-agent-encryption-key-v1"
    assert statement["identity"] == pinned
    assert (statement["kem"], statement["kdf"], statement["aead"]) == (
        "MLKEM768-X25519",
        "HKDF-SHA256",
        "AES-256-GCM",
    )
    return statement


def encryption_public(statement):
    raw = unb64url(statement["public_key"])
    return hpke.MLKEM768X25519PublicKey(
        mlkem.MLKEM768PublicKey.from_public_bytes(raw[:1184]),
        x25519.X25519PublicKey.from_public_bytes(raw[1184:]),
    )


def request_info(operation, key_id, agent_key_id=""):
    return b"\x00".join(
        (
            b"keen-agent-request-v1",
            operation.encode(),
            key_id.encode(),
            agent_key_id.encode(),
        )
    )


def response_aad(operation, status):
    return b"\x00".join(
        (b"keen-agent-response-v1", operation.encode(), str(status).encode())
    )


def stamp(when: datetime) -> str:
    when = when.astimezone(timezone.utc)
    return when.strftime(TIME)[:-3] + "Z"


def uuid7(when: datetime | None = None) -> str:
    ms = int((when or datetime.now(timezone.utc)).timestamp() * 1000)
    raw = bytearray(ms.to_bytes(6, "big") + secrets.token_bytes(10))
    raw[6] = 0x70 | (raw[6] & 0x0F)
    raw[8] = 0x80 | (raw[8] & 0x3F)
    return str(uuid.UUID(bytes=bytes(raw)))


def public(key) -> str:
    return b64url(key.public_key().public_bytes_raw())


def key_id(agent_id, key) -> str:
    raw = key.public_key().public_bytes_raw()
    return agent_id + ":" + hashlib.sha256(raw).hexdigest()[:16]


def proof(key, statement) -> str:
    return b64url(key.sign(canonical(statement)))


class SimAgent:
    def __init__(self, client, *, platform="linux"):
        self.client = client
        self.key = Ed25519PrivateKey.generate()
        self.pairwise = secrets.token_bytes(32)
        self.platform = platform
        self.agent_id = None
        self.key_id = None
        self.grant = None
        self.sequence = 0
        self.last_hash = None
        self.server = None
        self.pinned = None

    # -- KEEN's keys ---------------------------------------------------------
    def fetch_server_key(self, credential=None, pinned=None):
        pinned = pinned or credential.rsplit(".", 1)[1]
        r = self.client.get("/v1/agents/server-key")
        assert r.status_code == 200, r.text
        self.pinned = pinned
        self.server = verify_server_key(r.json(), pinned)
        return r.json()

    def encrypt(self, body, operation, agent_key_id="", *, key_id=None):
        """Encrypt body to KEEN; return the wire bytes and the response key."""
        response_key = secrets.token_bytes(32)
        key_id = key_id or self.server["key_id"]
        wire = SUITE.encrypt(
            response_key + body,
            encryption_public(self.server),
            request_info(operation, key_id, agent_key_id),
        )
        return wire, response_key

    def open_response(self, r, response_key, operation):
        """Replace a sealed success body with its plaintext, as the agent sees it."""
        if 200 <= r.status_code < 300:
            assert r.headers["content-type"] == SEALED_RESPONSE, r.text
            raw = r.content
            r.sealed = raw
            r._content = AESGCM(response_key).decrypt(
                raw[:12], raw[12:], response_aad(operation, r.status_code)
            )
        return r

    # -- enrolment -----------------------------------------------------
    def enrollment(self, credential, *, key=None, created=None, proof_nonce=None):
        """An enrolment body with a fresh proof, as the agent signs per attempt."""
        key = key or self.key
        created = (
            int(datetime.now(timezone.utc).timestamp()) if created is None else created
        )
        proof_nonce = proof_nonce or secrets.token_urlsafe(32)
        return {
            "credential": credential,
            "public_key": public(key),
            "created": created,
            "proof_nonce": proof_nonce,
            "proof": proof(
                key,
                {
                    "context": "keen-agent-enroll-v2",
                    "created": created,
                    "credential_sha256": hashlib.sha256(
                        credential.encode()
                    ).hexdigest(),
                    "proof_nonce": proof_nonce,
                    "public_key": public(key),
                },
            ),
        }

    def post_enrollment(self, credential, body, key=None, *, raw=None):
        if self.server is None:
            self.fetch_server_key(credential)
        plain = raw if raw is not None else json.dumps(body).encode()
        wire, response_key = self.encrypt(plain, "enroll")
        r = self.client.post(
            "/v1/agents/enroll", content=wire, headers=self.enrollment_headers()
        )
        self.open_response(r, response_key, "enroll")
        if r.status_code == 201:
            self.accept(r.json(), key or self.key)
        return r

    def enrollment_headers(self):
        return {
            "Content-Type": SEALED,
            "Keen-Encryption-Key": self.server["key_id"],
            "Keen-Agent-Operation": "enroll",
        }

    def enroll(self, credential, *, name=None, nonce=None, key=None):
        key = key or self.key
        body = self.enrollment(credential, key=key)
        if name is not None:
            body["name"], body["nonce"] = name, nonce or secrets.token_urlsafe(32)
        return self.post_enrollment(credential, body, key)

    def accept(self, grant, key):
        self.grant = grant
        self.key = key
        self.agent_id = grant["agent"]["id"]
        assert grant["key_id"] == key_id(self.agent_id, key)
        self.key_id = grant["key_id"]

    def renew(self, *, nonce=None, new=None):
        new = new or Ed25519PrivateKey.generate()
        nonce = nonce or secrets.token_urlsafe(32)
        body = {
            "public_key": public(new),
            "nonce": nonce,
            "proof": proof(
                new,
                {
                    "context": "keen-agent-renew-v1",
                    "agent_id": self.agent_id,
                    "previous_key_id": self.key_id,
                    "public_key": public(new),
                },
            ),
        }
        r = self.post("/v1/agents/renew", json.dumps(body).encode(), "renew")
        return r, new, nonce

    # -- requests --------------------------------------------------------
    def signed_headers(
        self,
        body,
        operation,
        *,
        created=None,
        nonce=None,
        key=None,
        key_id=None,
        content_type=SEALED,
    ):
        key = key or self.key
        key_id = key_id or self.server["key_id"]
        digest = "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode()
        digest += ":"
        created = (
            int(datetime.now(timezone.utc).timestamp()) if created is None else created
        )
        nonce = nonce or b64url(secrets.token_bytes(16))
        params = (
            f'{COMPONENTS};created={created};keyid="{self.key_id}"'
            f';nonce="{nonce}";alg="ed25519"'
        )
        base = (
            f'"@method": POST\n"keen-agent-operation": {operation}\n'
            f'"keen-encryption-key": {key_id}\n'
            f'"content-digest": {digest}\n"content-type": {content_type}\n'
            f'"@signature-params": {params}'
        ).encode()
        return {
            "Content-Type": content_type,
            "Keen-Encryption-Key": key_id,
            "Content-Digest": digest,
            "Keen-Agent-Operation": operation,
            "Signature-Input": "keen=" + params,
            "Signature": "keen=:" + base64.b64encode(key.sign(base)).decode() + ":",
        }

    def post(self, path, body, operation, *, key_id=None, **kw):
        """Seal body to KEEN, sign the sealed bytes, and open the response."""
        wire, response_key = self.encrypt(body, operation, self.key_id, key_id=key_id)
        r = self.post_raw(path, wire, operation, key_id=key_id, **kw)
        return self.open_response(r, response_key, operation)

    def post_raw(self, path, wire, operation, **kw):
        """Sign and send bytes exactly as given."""
        return self.client.post(
            path, content=wire, headers=self.signed_headers(wire, operation, **kw)
        )

    def heartbeat(self, health=None):
        body = json.dumps(health or {"version": "0.2.0"}).encode()
        return self.post("/v1/agents/heartbeat", body, "heartbeat")

    # -- evidence --------------------------------------------------------
    def pseudonym(self, kind, value):
        subject = self.grant["subject"]
        key = self.pairwise
        if subject["type"] == "public":
            key = base64.urlsafe_b64decode(
                subject["key"] + "=" * (-len(subject["key"]) % 4)
            )
        mac = hmac.new(
            key,
            b"keen-ueu-subject-v1\x00" + kind.encode() + b"\x00" + value.encode(),
            hashlib.sha256,
        )
        return {
            "identifier_type": subject["type"],
            "identifier": "hmac-sha256:" + mac.hexdigest(),
        }

    def base(self, event_type, object_type, component, action, outcome, reason, when):
        return {
            "ueu_spec_version": "0.1-keen",
            "evidence_id": uuid7(when),
            "event_type": event_type,
            "catalogue": {"id": "keen", "version": "2026.10"},
            "event_time": stamp(when),
            "source": {
                "application_id": "keen-agent",
                "application_version": "0.2.0",
                "component": component,
                "instance_id": self.agent_id,
            },
            "asset": {
                "asset_type": self.grant["agent"]["agent_type"],
                "platform": self.platform,
                "identifier": self.agent_id,
            },
            "event": {
                "action": action,
                "object_type": object_type,
                "outcome": outcome,
                "reason_code": reason,
            },
            "privacy": {
                "profile": "keen-default-1",
                "personal_data_included": False,
                "transformations": [],
            },
            "mappings": [],
        }

    def observation(
        self,
        check,
        provider,
        facts,
        *,
        scope="host",
        reason="observed",
        when=None,
        subject=None,
    ):
        when = when or datetime.now(timezone.utc)
        env = self.base(
            f"endpoint.{check}.observed",
            check,
            provider,
            "observe",
            "observed",
            reason,
            when,
        )
        env["observation"] = {
            "method": "native_query",
            "scope": scope,
            "valid_for_seconds": 86400,
            "facts": facts,
        }
        env["evidence"] = {"payload_included": False}
        if subject:
            env["subject"] = self.pseudonym(*subject)
            env["privacy"]["transformations"].append("subject-pseudonymised")
        return env

    def event(
        self,
        event_type,
        object_type,
        attributes,
        *,
        component="syslog",
        action="observe",
        outcome="success",
        reason="recognised",
        payload=None,
        mode="events",
        when=None,
        subject=None,
    ):
        when = when or datetime.now(timezone.utc)
        env = self.base(
            event_type, object_type, component, action, outcome, reason, when
        )
        env["attributes"] = attributes
        evidence = {"payload_included": False, "collection_mode": mode}
        if payload is not None:
            evidence["payload_hash"] = (
                "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
            )
            if mode != "events" or event_type == "log.line.collected":
                evidence["payload_included"] = True
                evidence["payload"] = payload
                env["privacy"]["personal_data_included"] = True
        env["evidence"] = evidence
        if subject:
            env["subject"] = self.pseudonym(*subject)
            env["privacy"]["transformations"].append("subject-pseudonymised")
        return env

    def seal(self, env, *, stream=None):
        self.sequence += 1
        env["evidence_time"] = stamp(datetime.now(timezone.utc))
        env["integrity"] = {
            "canonicalisation": "RFC8785",
            "hash_algorithm": "SHA-256",
            "stream": stream or self.agent_id,
            "sequence": self.sequence,
            "previous_evidence_hash": self.last_hash,
        }
        data = canonical(env)
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        self.last_hash = digest
        return {"env": env, "bytes": data, "hash": digest}

    def manifest(self, sealed, key=None, key_ident=None):
        return canonical(
            {
                "context": "keen-ueu-batch-v1",
                "evidence": [
                    {"evidence_hash": s["hash"], "evidence_id": s["env"]["evidence_id"]}
                    for s in sealed
                ],
                "issued_at": stamp(datetime.now(timezone.utc)),
                "key_id": key_ident or self.key_id,
                "producer": self.agent_id,
            }
        )

    def otlp(self, sealed, *, sign=True, key=None, key_ident=None, manifest=None):
        def attr(k, v):
            return {"key": k, "value": {"stringValue": v}}

        resource = [
            attr("service.name", "keen-agent"),
            attr("service.version", "0.2.0"),
            attr("ueu.producer.instance_id", self.agent_id),
        ]
        if sign:
            manifest = manifest or self.manifest(sealed, key, key_ident)
            signature = b64url((key or self.key).sign(manifest))
            resource += [
                attr("ueu.batch.manifest", manifest.decode()),
                attr("ueu.batch.signature", signature),
                attr("ueu.batch.key_id", key_ident or self.key_id),
            ]
        records = [
            {
                "timeUnixNano": str(
                    int(
                        datetime.strptime(s["env"]["event_time"], TIME[:-3] + ".%fZ")
                        .replace(tzinfo=timezone.utc)
                        .timestamp()
                        * 10**9
                    )
                ),
                "severityNumber": 9,
                "body": {"stringValue": s["bytes"].decode()},
                "attributes": [
                    attr("event.name", s["env"]["event_type"]),
                    attr("ueu.spec_version", "0.1-keen"),
                    attr("ueu.evidence_id", s["env"]["evidence_id"]),
                ],
            }
            for s in sealed
        ]
        return json.dumps(
            {
                "resourceLogs": [
                    {
                        "resource": {"attributes": resource},
                        "scopeLogs": [
                            {"scope": {"name": "keen-agent"}, "logRecords": records}
                        ],
                    }
                ]
            }
        ).encode()

    def send(self, *envelopes, **kw):
        sealed = [self.seal(e) for e in envelopes]
        return self.post("/v1/otlp/logs", self.otlp(sealed, **kw), "otlp-logs"), sealed
