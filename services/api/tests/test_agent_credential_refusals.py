"""Refusals at the agent credential, signature and payload boundaries.

Bearer-token collectors and signed keen-agent identities reach KEEN through the
OTLP, heartbeat and enrolment routes. Each test sends what a forger or a broken
collector would and checks the refusal precisely: its status and message, that
no evidence, health report, key or nonce was stored, and that the identity
gained nothing.
"""

import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest
from app.agents import credentials
from app.agents.otlp import decode_batch
from app.db.models import AgentKey, Event, KeenAgent
from app.security import agent_signatures as signatures
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from tests.test_keen_agent import logs, record
from tests.test_keen_agent import setup as setup  # noqa: F401 - fixture
from tests.test_ueu_evidence import enrolled
from tests.test_ueu_evidence import keen as keen  # noqa: F401 - fixture
from tests.ueu_agent import SimAgent, b64url, proof, public

REFUSED = "Invalid agent credential"
INVALID_OTLP = "Invalid OTLP JSON logs; maximum 64 records and bounded attributes"
B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _pg_error(code):
    class DriverError(Exception):
        pgcode = code

    return OperationalError("SELECT 1", {}, DriverError("synthetic driver error"))


def _non_canonical(encoded_key):
    """The same 32 key bytes, spelt with a non-zero padding bit."""
    last = B64URL.index(encoded_key[-1])
    assert last & 3 == 0
    return encoded_key[:-1] + B64URL[last + 1]


def _attr(key, value):
    return {"key": key, "value": {"stringValue": value}}


def _ueu_record(body="{}", evidence_id="ev-1"):
    attributes = [_attr("ueu.spec_version", "0.1-keen")]
    if evidence_id is not None:
        attributes.append(_attr("ueu.evidence_id", evidence_id))
    return {"body": body, "attributes": attributes}


def _document(records, resource=()):
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": list(resource)},
                "scopeLogs": [{"scope": {"name": "test"}, "logRecords": records}],
            }
        ]
    }


# -- row locks --------------------------------------------------------------


def test_only_a_busy_row_lock_becomes_a_retryable_refusal(contract_db):
    deadlock = _pg_error("40P01")
    with pytest.raises(OperationalError) as raised:
        with credentials.bounded_lock_wait(contract_db):
            raise deadlock
    # Any other database failure propagates unchanged.
    assert raised.value is deadlock

    with pytest.raises(HTTPException) as busy:
        with credentials.bounded_lock_wait(contract_db):
            raise _pg_error(credentials.LOCK_NOT_AVAILABLE)
    assert busy.value.status_code == 429
    assert busy.value.detail == "The agent's identity is busy; retry shortly"
    assert busy.value.headers == {"Retry-After": "5"}


# -- signed requests --------------------------------------------------------


def test_a_request_signed_by_an_unregistered_key_is_refused(keen):  # noqa: F811
    agent, _ = enrolled(keen)
    nonces = credentials.get_valkey()
    before = set(nonces.keys)
    # The key id names the agent but no key KEEN holds for it.
    agent.key_id = agent.agent_id + ":" + "0" * 16
    agent.key = Ed25519PrivateKey.generate()

    refused = agent.heartbeat()

    assert (refused.status_code, refused.json()) == (401, {"detail": REFUSED})
    assert nonces.keys == before
    with keen.db() as db:
        assert db.get(KeenAgent, agent.agent_id).last_seen is None


def test_a_token_identity_never_authenticates_by_signature(setup):  # noqa: F811
    client, factory, _, info, _, _ = setup
    agent_id = info["agent"]["id"]
    signer = SimAgent(client)
    signer.server = {"key_id": "0123456789abcdef"}
    signer.key_id = signatures.key_id(agent_id, public(signer.key))
    # Even a key row for the token identity, as a hand-edited database might
    # hold, gives it no signature authority.
    with factory() as db:
        db.add(
            AgentKey(
                key_id=signer.key_id,
                agent_id=agent_id,
                public_key=public(signer.key),
                state="active",
                origin="code",
                credential_hash="0" * 64,
            )
        )
        db.commit()
    nonces = credentials.get_valkey()
    body = json.dumps({"version": "0.2.0"}).encode()

    refused = client.post(
        "/v1/agents/heartbeat",
        content=body,
        headers=signer.signed_headers(body, "heartbeat"),
    )

    assert (refused.status_code, refused.json()) == (401, {"detail": REFUSED})
    assert nonces.keys == set()
    with factory() as db:
        row = db.get(KeenAgent, agent_id)
        assert row.last_seen is None and row.health == {}


def test_a_signed_identity_whose_credential_type_changed_is_refused_on_reauthentication(
    keen,  # noqa: F811
):
    agent, _ = enrolled(keen)
    with keen.db() as db:
        row = db.get(KeenAgent, agent.agent_id)
        auth = credentials.Authenticated(
            row,
            signatures.SignedRequest(
                key_id=agent.key_id,
                agent_id=agent.agent_id,
                nonce="n" * 22,
                created=0,
                encryption_key="0" * 16,
                digest="",
                params="",
                signature=b"",
            ),
        )
        # The write path checks the identity again under its row lock.
        assert credentials.reauthenticate(db, auth)[1].key_id == agent.key_id
        row.credential_type = "token"
        db.flush()

        with pytest.raises(HTTPException) as refused:
            credentials.reauthenticate(db, auth)
        assert (refused.value.status_code, refused.value.detail) == (401, REFUSED)


def test_a_bearer_request_has_no_signed_digest_to_compare():
    token_agent = KeenAgent(id="a" * 36, credential_type="token")
    authenticated = credentials.Authenticated(token_agent)

    assert authenticated.key_id is None
    assert authenticated.check_body(b"any body") is None


def test_an_enrolment_key_spelt_another_way_is_refused(keen):  # noqa: F811
    from tests.test_ueu_evidence import register

    info = register(keen)
    agent = SimAgent(keen.client)
    body = agent.enrollment(info["enrollment_code"])
    spelling = _non_canonical(body["public_key"])
    body["public_key"] = spelling
    statement = {
        "context": "keen-agent-enroll-v2",
        "created": body["created"],
        "credential_sha256": hashlib.sha256(
            info["enrollment_code"].encode()
        ).hexdigest(),
        "proof_nonce": body["proof_nonce"],
        "public_key": spelling,
    }
    # The proof is genuine for those key bytes; only the spelling differs.
    body["proof"] = proof(agent.key, statement)

    refused = agent.post_enrollment(info["enrollment_code"], body)

    assert (refused.status_code, refused.json()["detail"]) == (400, "Key proof invalid")
    with keen.db() as db:
        assert db.query(AgentKey).count() == 0


def test_a_public_key_has_one_spelling():
    encoded = public(Ed25519PrivateKey.generate())
    assert signatures.public_key(encoded)

    with pytest.raises(signatures.SignatureError, match="^invalid public key$"):
        signatures.public_key(_non_canonical(encoded))


def test_a_key_the_cryptography_library_refuses_is_an_invalid_public_key(
    monkeypatch,
):
    class Refusing:
        @staticmethod
        def from_public_bytes(raw):
            raise ValueError("An Ed25519 public key is 32 bytes long")

    monkeypatch.setattr(signatures, "Ed25519PublicKey", Refusing)

    with pytest.raises(signatures.SignatureError, match="^invalid public key$"):
        signatures.public_key(public(Ed25519PrivateKey.generate()))


def test_a_signature_for_an_unsigned_operation_is_refused():
    key = Ed25519PrivateKey.generate()
    now = int(datetime.now(timezone.utc).timestamp())
    digest = signatures.content_digest(b"{}")
    params = (
        f"{signatures.COMPONENTS};created={now};"
        f'keyid="{"a" * 36}:{"0" * 16}";nonce="{"n" * 22}";alg="ed25519"'
    )
    headers = {
        "signature-input": "keen=" + params,
        "signature": "keen=:" + base64.b64encode(key.sign(b"x")).decode() + ":",
        "content-digest": digest,
        "keen-encryption-key": "0" * 16,
        "keen-agent-operation": "enroll",
    }
    assert signatures.parse_request(
        {**headers, "keen-agent-operation": "heartbeat"}, "heartbeat"
    )

    # Enrolment proves its key inside the encrypted body, so no signature names it.
    with pytest.raises(signatures.SignatureError, match="^unknown operation$"):
        signatures.parse_request(headers, "enroll")


@pytest.mark.parametrize(
    "signature", [None, "", "A" * 85, "A" * 87, "A" * 85 + "="], ids=repr
)
def test_a_malformed_detached_signature_is_refused_before_verification(signature):
    key = Ed25519PrivateKey.generate()
    message = b"manifest"
    signatures.verify_detached(public(key), message, b64url(key.sign(message)))

    with pytest.raises(signatures.SignatureError, match="^malformed signature$"):
        signatures.verify_detached(public(key), message, signature)


# -- OTLP payloads ----------------------------------------------------------


@pytest.mark.parametrize(
    "document,message",
    [
        (
            _document([_ueu_record(body={"intValue": "1"})]),
            "UEU records carry the envelope as a string body",
        ),
        (
            _document([_ueu_record(body={"stringValue": "{}", "intValue": "1"})]),
            "UEU records carry the envelope as a string body",
        ),
        (
            _document([_ueu_record(body={"stringValue": "{}"}, evidence_id=None)]),
            "UEU records name their evidence",
        ),
        (
            _document(
                [
                    _ueu_record(body={"stringValue": "{}"}, evidence_id=str(n))
                    for n in range(257)
                ]
            ),
            "Batch exceeds the record limit",
        ),
        (
            _document([{"body": {"stringValue": "no time"}, "attributes": []}]),
            "Records require timeUnixNano or observedTimeUnixNano",
        ),
        (
            {
                **_document(logs(record())),
                "resourceLogs": [
                    {
                        **_document(logs(record()))["resourceLogs"][0],
                        "resource": {
                            "attributes": [_attr("ueu.batch.signature", "A" * 86)]
                        },
                    }
                ],
            },
            "A signed batch carries UEU records only",
        ),
    ],
    ids=[
        "ueu-body-not-a-string",
        "ueu-body-with-two-values",
        "ueu-record-without-evidence-id",
        "too-many-records",
        "record-without-a-time",
        "signed-resource-with-log-records",
    ],
)
def test_malformed_otlp_batches_are_refused_whole(setup, document, message):  # noqa: F811
    client, factory, headers, info, stored, _ = setup
    with pytest.raises(ValueError, match="^" + message + "$"):
        decode_batch(json.dumps(document), info["agent"]["id"])

    refused = client.post("/v1/otlp/logs", json=document, headers=headers)

    assert (refused.status_code, refused.json()) == (
        400,
        {"code": 3, "message": INVALID_OTLP},
    )
    assert stored == []
    with factory() as db:
        assert db.query(Event).count() == 0


def test_a_records_trace_context_is_kept_with_its_event(setup):  # noqa: F811
    client, factory, headers, _, _, _ = setup
    doc = record()
    (log,) = logs(doc)
    seen = str(int(datetime.now(timezone.utc).timestamp()) * 10**9)
    log.update(
        traceId="4bf92f3577b34da6a3ce929d0e0e4736",
        spanId="00f067aa0ba902b7",
        severityText="WARN",
        observedTimeUnixNano=seen,
    )

    accepted = client.post("/v1/otlp/logs", json=doc, headers=headers)

    assert accepted.status_code == 200, accepted.text
    with factory() as db:
        fields = db.query(Event).one().normalized_payload["fields"]
    context = {
        "otel.traceId": "4bf92f3577b34da6a3ce929d0e0e4736",
        "otel.spanId": "00f067aa0ba902b7",
        "otel.severityText": "WARN",
        "otel.observedTimeUnixNano": seen,
    }
    assert {key: fields.get(key) for key in context} == context


# -- health reports ---------------------------------------------------------


@pytest.mark.parametrize(
    "report",
    [
        {"not_evidence_by_source": {"": 1}},
        {"not_evidence_by_source": {"s" * 65: 1}},
        {"not_evidence_by_source": {"journald": -1}},
        {"evidence_modes": {"": "events"}},
        {"evidence_modes": {"s" * 65: "events"}},
        {"sources": {"s" * 65: "healthy"}},
        {"sources": {"journald": "h" * 257}},
    ],
    ids=[
        "blank-source",
        "long-source",
        "negative-count",
        "blank-mode-source",
        "long-mode-source",
        "long-source-name",
        "long-source-status",
    ],
)
def test_a_health_report_outside_its_bounds_is_refused(setup, report):  # noqa: F811
    client, factory, headers, info, _, _ = setup

    refused = client.post("/v1/agents/heartbeat", json=report, headers=headers)

    assert (refused.status_code, refused.json()) == (
        400,
        {"detail": "Invalid health report"},
    )
    with factory() as db:
        row = db.get(KeenAgent, info["agent"]["id"])
        assert row.health == {} and row.last_seen is None


def test_a_health_report_within_its_bounds_is_recorded(setup):  # noqa: F811
    client, factory, headers, info, _, _ = setup
    report = {
        "not_evidence_by_source": {"journald": 3},
        "evidence_modes": {"journald": "events"},
        "sources": {"journald": "healthy"},
    }

    accepted = client.post("/v1/agents/heartbeat", json=report, headers=headers)

    assert accepted.status_code == 200, accepted.text
    with factory() as db:
        health = db.get(KeenAgent, info["agent"]["id"]).health
    assert {key: health[key] for key in report} == report
