"""Signed UEU evidence from keen-agent: keys, requests, batches, chains,
catalogue checks and custody."""

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import app.services.agent_enrollment as enrollment_operations
import pytest
from app.agents.ueu.envelope import validate
from app.agents.ueu.jcs import CanonicalError, canonical, parse, parse_canonical
from app.core.datetime_utils import utc_now_naive
from app.db.models import AgentKey, Artifact, Event, KeenAgent, UeuBatch, UeuEvidence
from app.ingest import common
from app.security import agent_signatures as signatures
from app.services.ueu_evidence import display_payload, evidence_record
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.test_keen_agent import setup as setup
from tests.ueu_agent import FakeValkey, SimAgent, b64url, key_id, stamp

VECTORS = json.loads(
    (Path(__file__).resolve().parent / "fixtures/ueu-vectors.json").read_text()
)
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def keen(setup, monkeypatch):
    client, factory, _, _, _, app = setup
    monkeypatch.setattr(enrollment_operations, "get_valkey", lambda: None)
    proofs = FakeValkey()
    monkeypatch.setattr(enrollment_operations, "proof_store", lambda: proofs)
    monkeypatch.setattr(
        enrollment_operations, "fixed_window_allow", lambda *a, **k: (True, 0)
    )
    objects = {}

    def put(**k):
        uri = "mem://" + k["key"]
        objects[uri] = k["data"]
        return SimpleNamespace(
            uri=uri, sha256=hashlib.sha256(k["data"]).hexdigest(), size_bytes=0
        )

    monkeypatch.setattr(common, "put_bytes", put)
    return SimpleNamespace(client=client, db=factory, objects=objects, app=app)


def register(keen, **extra):
    body = {"name": "web-01", **extra}
    r = keen.client.post("/v1/admin/agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def enrolled(keen, **extra):
    info = register(keen, **extra)
    agent = SimAgent(keen.client)
    r = agent.enroll(info["enrollment_code"])
    assert r.status_code == 201, r.text
    return agent, info


def lock_facts(**extra):
    return {
        "automatic_lock_configured": True,
        "lock_after_seconds": 300,
        "password_required": True,
        "lock_paths": ["secure_screen_saver"],
        **extra,
    }


# -- shared bytes with the Go agent ----------------------------------------


def test_python_reproduces_the_agents_golden_vectors():
    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(VECTORS["seed_hex"]))
    pub = b64url(priv.public_key().public_bytes_raw())
    assert pub == VECTORS["public_key"]
    agent = "4f1c9a2e-0000-4000-8000-000000000001"
    assert signatures.key_id(agent, pub) == VECTORS["key_id"]
    env = validate(
        VECTORS["envelope"], now=datetime(2026, 10, 27, 7, tzinfo=timezone.utc)
    )
    assert env.evidence_hash == VECTORS["evidence_hash"]
    assert canonical(parse(VECTORS["envelope"])) == VECTORS["envelope"].encode()
    signatures.verify_detached(
        pub, VECTORS["manifest"].encode(), VECTORS["manifest_signature"]
    )
    mac = hmac.new(
        bytes.fromhex(VECTORS["subject_key_hex"]),
        b"keen-ueu-subject-v1\x00"
        + VECTORS["subject_kind"].encode()
        + b"\x00"
        + VECTORS["subject_value"].encode(),
        hashlib.sha256,
    )
    assert "hmac-sha256:" + mac.hexdigest() == VECTORS["subject_identifier"]
    body = VECTORS["request_body"].encode()
    assert signatures.content_digest(body) == VECTORS["content_digest"]
    headers = {
        "signature-input": VECTORS["signature_input"],
        "signature": VECTORS["signature"],
        "content-digest": VECTORS["content_digest"],
        "keen-agent-operation": "heartbeat",
    }
    signed = signatures.parse_request(headers, "heartbeat", now=1793083418)
    assert (
        signatures.signature_base(
            "POST", "heartbeat", signed.digest, "application/json", signed.params
        )
        == VECTORS["signature_base"].encode()
    )
    signatures.verify_request(signed, pub, "POST", "heartbeat", "application/json")
    with pytest.raises(signatures.SignatureError):
        signatures.verify_request(signed, pub, "POST", "renew", "application/json")


@pytest.mark.parametrize(
    "text",
    [
        '{"a":1,"a":2}',
        '{"a":1.5}',
        '{"a":NaN}',
        '{"a":"\\ud800"}',
        '{"b":1, "a":2}',
        '{"a":9007199254740992}',
    ],
)
def test_canonical_form_is_strict(text):
    with pytest.raises(CanonicalError):
        parse_canonical(text)


def test_canonical_ordering_and_escapes_follow_rfc_8785():
    value = {"\u20ac": 1, "\U0001f600": 2, 'a\u0001\n"': "\u2028\x7f", "": None}
    # U+20AC sorts before U+1F600, whose first UTF-16 code unit is 0xD83D.
    assert (
        canonical(value)
        == (
            '{"":null,"a\\u0001\\n\\"":"\u2028\x7f","\u20ac":1,"\U0001f600":2}'
        ).encode()
    )


# -- enrolment ---------------------------------------------------------------


def test_code_enrolment_binds_one_key_and_is_single_use(keen):
    info = register(keen, agent_type="server")
    code = info["enrollment_code"]
    assert "token" not in info and code.startswith("kc_" + info["agent"]["id"])
    assert code not in keen.client.get("/v1/admin/agents").text
    agent = SimAgent(keen.client)
    assert agent.enroll(code).status_code == 201
    with keen.db() as db:
        key = db.query(AgentKey).one()
        assert key.state == "active" and key.origin == "code"
        assert code not in key.credential_hash
        row = db.get(KeenAgent, agent.agent_id)
        assert row.enrollment_code_hash is None and row.credential_type == "key"
    # A retry after a lost response returns the same binding.
    again = agent.enroll(code)
    assert again.status_code == 201 and again.json()["key_id"] == agent.key_id
    # The code cannot bind another key.
    other = SimAgent(keen.client)
    assert other.enroll(code).status_code == 409
    assert agent.grant["subject"] == {"type": "pairwise"}
    assert agent.grant["evidence_mode_ceiling"] == "events"
    assert agent.grant["catalogue"] == {"id": "keen", "version": "2026.10"}


def test_enrolment_rejects_bad_codes_proofs_and_expired_codes(keen):
    info = register(keen)
    code = info["enrollment_code"]
    agent = SimAgent(keen.client)
    wrong = code[:-4] + ("AAAA" if not code.endswith("AAAA") else "BBBB")
    assert agent.enroll(wrong).status_code == 401
    # A proof made for another credential does not bind the key.
    body = agent.enrollment(code)
    body["proof"] = b64url(agent.key.sign(b"something else"))
    assert agent.post_enrollment(code, body).status_code == 400
    # So does a proof signed for another credential.
    assert agent.post_enrollment(code, agent.enrollment(wrong)).status_code == 400
    assert agent.enroll(code, name="x").status_code == 400
    with keen.db() as db:
        row = db.get(KeenAgent, info["agent"]["id"])
        row.enrollment_code_expires_at = utc_now_naive() - timedelta(seconds=1)
        db.commit()
    assert agent.enroll(code).status_code == 401
    with keen.db() as db:
        assert db.query(AgentKey).count() == 0


def test_recorded_enrolment_requests_cannot_be_replayed(keen, monkeypatch):
    import time

    from cryptography.fernet import Fernet

    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    info = register(keen, subject_type="public")
    code = info["enrollment_code"]
    agent = SimAgent(keen.client)
    recorded = agent.enrollment(code)
    first = agent.post_enrollment(code, recorded)
    assert first.status_code == 201 and first.json()["subject"]["type"] == "public"
    # Someone who saw the request cannot send it again for the grant.
    replay = keen.client.post(
        "/v1/agents/enroll",
        json=recorded,
        headers={"Authorization": "Bearer " + code},
    )
    assert replay.status_code == 400
    assert "subject" not in replay.text
    # The key holder's retry, with a fresh proof, recovers the same binding.
    again = agent.enroll(code)
    assert again.status_code == 201 and again.json()["key_id"] == agent.key_id
    # Proofs outside the five-minute window are refused before anything else.
    now = int(time.time())
    for created in (now - 301, now + 301):
        stale = agent.enrollment(code, created=created)
        assert agent.post_enrollment(code, stale).status_code == 400
    assert (
        agent.post_enrollment(
            code, agent.enrollment(code, created=now - 290)
        ).status_code
        == 201
    )

    class Down:
        def set(self, *a, **k):
            raise ConnectionError("valkey down")

    monkeypatch.setattr(enrollment_operations, "proof_store", lambda: Down())
    r = agent.enroll(code)
    assert r.status_code == 503 and r.headers["retry-after"]
    with keen.db() as db:
        assert db.query(AgentKey).count() == 1


def test_profile_enrolment_proofs_are_single_use(keen):
    r = keen.client.post("/v1/admin/agent-enrollment-profiles", json={"name": "fleet"})
    assert r.status_code in (200, 201), r.text
    bootstrap = r.json()["bootstrap_key"]
    agent = SimAgent(keen.client)
    body = agent.enrollment(bootstrap)
    body["name"], body["nonce"] = "host-1", secrets.token_urlsafe(32)
    assert agent.post_enrollment(bootstrap, body).status_code == 201
    assert agent.post_enrollment(bootstrap, body).status_code == 400
    retry = agent.enrollment(bootstrap)
    retry["name"], retry["nonce"] = body["name"], body["nonce"]
    again = agent.post_enrollment(bootstrap, retry)
    assert again.status_code == 201 and again.json()["key_id"] == agent.key_id


def test_token_collectors_cannot_enrol_or_sign(keen):
    info = register(keen, name="otel", credential_type="token")
    assert info["token"].startswith("ka_") and "enrollment_code" not in info
    r = keen.client.post(
        "/v1/admin/agents",
        json={
            "name": "laptop",
            "credential_type": "token",
            "agent_type": "workstation",
            "person_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 422


# -- signed requests ---------------------------------------------------------


def test_signed_batch_is_stored_with_provenance_and_typed_facts(keen):
    agent, _ = enrolled(keen)
    env = agent.observation(
        "screen_lock",
        "windows_user_policy",
        lock_facts(),
        scope="user",
        subject=("windows_sid", "S-1-5-21-1-2-3-1001"),
    )
    r, sealed = agent.send(env)
    assert r.status_code == 200, r.text
    with keen.db() as db:
        event = db.query(Event).one()
        row = db.get(UeuEvidence, event.id)
        assert row.verification == "verified" and row.chain_status == "started"
        assert row.schema_validation == "valid" and row.custody["changes"] == []
        assert row.evidence_hash == sealed[0]["hash"]
        facts = event.normalized_payload["ueu"]["observation"]["facts"]
        # Typed measurements are kept whatever their names.
        assert facts["password_required"] is True
        assert event.action == "endpoint.screen_lock.observed"
        assert event.actor == env["subject"]["identifier"]
        assert "S-1-5-21" not in json.dumps(event.normalized_payload)
        artifact = db.query(Artifact).one()
        assert keen.objects[artifact.storage_uri] == sealed[0]["bytes"]
        assert db.query(UeuBatch).one().verification == "verified"
        shown = display_payload(event.normalized_payload)
        assert shown["ueu"]["observation"]["facts"]["password_required"] is True
        assert evidence_record(db, event.id)["batch"]["manifest"]["evidence"] == [
            {"evidence_hash": sealed[0]["hash"], "evidence_id": env["evidence_id"]}
        ]
    # Retrying the same batch stores nothing new.
    body = agent.otlp(sealed)
    assert agent.post("/v1/otlp/logs", body, "otlp-logs").status_code == 200
    r, _ = agent.send(agent.observation("firewall", "ufw", {"enabled": True}))
    assert r.status_code == 200
    with keen.db() as db:
        assert db.query(Event).count() == 2
        assert {e.chain_status for e in db.query(UeuEvidence)} == {
            "started",
            "continuous",
        }
        health = db.get(KeenAgent, agent.agent_id).health
        assert health["ueu_duplicates_total"] == 1
    health = {
        "version": "0.2.0",
        "chain_sequence": 2,
        "sources": {"auth": "healthy", "app": "error: password=hunter2"},
        "filtered_by_source": {"auth": 3},
        "evidence_modes": {"auth": "events_with_lines"},
    }
    assert agent.heartbeat(health).status_code == 200
    with keen.db() as db:
        stored = db.get(KeenAgent, agent.agent_id).health
        # Source names are operator-chosen keys, never redacted as secrets.
        assert stored["sources"]["auth"] == "healthy"
        assert stored["filtered_by_source"] == {"auth": 3}
        assert stored["evidence_modes"] == {"auth": "events_with_lines"}
        assert "hunter2" not in stored["sources"]["app"]


def test_request_signatures_cannot_be_replayed_redirected_or_altered(keen):
    agent, _ = enrolled(keen)
    env = agent.observation("firewall", "ufw", {"enabled": True})
    body = agent.otlp([agent.seal(env)])
    headers = agent.signed_headers(body, "otlp-logs")
    assert (
        keen.client.post("/v1/otlp/logs", content=body, headers=headers).status_code
        == 200
    )
    # The same signature again is a replay.
    assert (
        keen.client.post("/v1/otlp/logs", content=body, headers=headers).status_code
        == 401
    )
    # A heartbeat signature does not authorise ingestion.
    hb = agent.signed_headers(body, "heartbeat")
    assert (
        keen.client.post("/v1/otlp/logs", content=body, headers=hb).status_code == 401
    )
    hb = agent.signed_headers(body, "heartbeat")
    hb["Keen-Agent-Operation"] = "otlp-logs"
    assert (
        keen.client.post("/v1/otlp/logs", content=body, headers=hb).status_code == 401
    )
    # The body is bound by its digest.
    fresh = agent.signed_headers(body, "otlp-logs")
    altered = body.replace(b"keen-agent", b"keen-agenT", 1)
    assert (
        keen.client.post("/v1/otlp/logs", content=altered, headers=fresh).status_code
        == 401
    )
    # Signatures outside the five-minute window are refused.
    old = int((datetime.now(timezone.utc) - timedelta(minutes=6)).timestamp())
    stale = agent.signed_headers(body, "otlp-logs", created=old)
    assert (
        keen.client.post("/v1/otlp/logs", content=body, headers=stale).status_code
        == 401
    )
    # A key that is not the agent's own cannot sign for it.
    other = Ed25519PrivateKey.generate()
    forged = agent.signed_headers(body, "otlp-logs", key=other)
    assert (
        keen.client.post("/v1/otlp/logs", content=body, headers=forged).status_code
        == 401
    )
    # Bearer credentials are not accepted for key identities.
    bearer = {"Authorization": "Bearer ka_" + agent.agent_id + "." + "A" * 43}
    assert (
        keen.client.post(
            "/v1/otlp/logs",
            content=body,
            headers={**bearer, "Content-Type": "application/json"},
        ).status_code
        == 401
    )


def test_key_agents_send_only_ueu_evidence(keen):
    from tests.test_keen_agent import record

    agent, _ = enrolled(keen)
    body = json.dumps(record()).encode()
    assert agent.post("/v1/otlp/logs", body, "otlp-logs").status_code == 400


def test_replay_protection_fails_closed(keen, monkeypatch):
    from app.agents import credentials

    agent, _ = enrolled(keen)

    class Down:
        def set(self, *a, **k):
            raise ConnectionError("valkey down")

    monkeypatch.setattr(credentials, "get_valkey", lambda: Down())
    assert agent.heartbeat().status_code == 503


# -- batch signatures and envelope checks ------------------------------------


def test_unsigned_and_unknown_key_batches_are_accepted_and_marked(keen):
    agent, _ = enrolled(keen)
    r, _ = agent.send(
        agent.observation("firewall", "ufw", {"enabled": True}), sign=False
    )
    assert r.status_code == 200
    stranger = Ed25519PrivateKey.generate()
    r, _ = agent.send(
        agent.observation("firewall", "ufw", {"enabled": True}),
        key=stranger,
        key_ident=agent.agent_id + ":" + "0" * 16,
    )
    assert r.status_code == 200
    with keen.db() as db:
        assert sorted(e.verification for e in db.query(UeuEvidence)) == [
            "unknown_key",
            "unsigned",
        ]


def test_a_batch_changed_after_signing_is_rejected_whole(keen):
    agent, _ = enrolled(keen)
    one = agent.seal(agent.observation("firewall", "ufw", {"enabled": True}))
    two = agent.seal(agent.observation("firewall", "ufw", {"enabled": False}))
    manifest = agent.manifest([one, two])
    signature_for_other = agent.manifest([one])
    # A manifest that does not list exactly the batch's envelopes.
    body = agent.otlp([one, two], manifest=signature_for_other)
    assert agent.post("/v1/otlp/logs", body, "otlp-logs").status_code == 400
    # A signature that does not verify with the producer's key.
    body = agent.otlp([one, two], manifest=manifest).replace(
        b'"ueu.batch.signature", "value": {"stringValue": "',
        b'"ueu.batch.signature", "value": {"stringValue": "A',
        1,
    )
    doc = json.loads(body)
    for a in doc["resourceLogs"][0]["resource"]["attributes"]:
        if a["key"] == "ueu.batch.signature":
            a["value"]["stringValue"] = b64url(agent.key.sign(b"other"))
    body = json.dumps(doc).encode()
    r = agent.post("/v1/otlp/logs", body, "otlp-logs")
    assert r.status_code == 400 and "UEU_EVIDENCE_VERIFICATION_FAILED" in r.text
    with keen.db() as db:
        assert db.query(Event).count() == 0 and db.query(UeuBatch).count() == 0


@pytest.mark.parametrize(
    "change, code",
    [
        (lambda e: e["observation"]["facts"].update(username="x"), "PROHIBITED"),
        (lambda e: e["observation"]["facts"].update(lock_after_seconds="5"), "SCHEMA"),
        (lambda e: e["observation"]["facts"].update({"Bad Name": "x"}), "SCHEMA"),
        (lambda e: e.update(event_type="endpoint.unknown.observed"), "EVENT_TYPE"),
        (lambda e: e["privacy"].update(personal_data_included=True), "PRIVACY"),
        (lambda e: e.update(ueu_spec_version="0.1"), "SPEC_VERSION"),
        (lambda e: e.update(extra=1), "SCHEMA"),
        (lambda e: e["event"].update(outcome="success"), "SCHEMA"),
        (
            lambda e: e.update(
                subject={"identifier_type": "pairwise", "identifier": "alice"}
            ),
            "SCHEMA",
        ),
    ],
)
def test_envelopes_breaking_the_profile_or_catalogue_are_rejected(keen, change, code):
    agent, _ = enrolled(keen)
    env = agent.observation("screen_lock", "gnome", lock_facts())
    change(env)
    r, _ = agent.send(env)
    assert r.status_code == 400 and code in r.text, r.text
    with keen.db() as db:
        assert db.query(Event).count() == 0


def test_non_canonical_bodies_and_other_producers_are_rejected(keen):
    agent, _ = enrolled(keen)
    sealed = agent.seal(agent.observation("firewall", "ufw", {"enabled": True}))
    sealed["bytes"] = json.dumps(sealed["env"], indent=1).encode()
    body = agent.otlp([sealed], sign=False)
    r = agent.post("/v1/otlp/logs", body, "otlp-logs")
    assert r.status_code == 400 and "CANONICALISATION" in r.text
    env = agent.observation("firewall", "ufw", {"enabled": True})
    sealed = agent.seal(env, stream=str(uuid.uuid4()))
    r = agent.post("/v1/otlp/logs", agent.otlp([sealed]), "otlp-logs")
    assert r.status_code == 400


def test_correlation_context_is_accepted_and_bounded(keen):
    agent, _ = enrolled(keen)
    env = agent.observation("firewall", "ufw", {"enabled": True})
    env["context"] = {
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "workflow": "patch-run",
    }
    assert agent.send(env)[0].status_code == 200
    for bad in (
        {"username": "alice"},
        {"trace_id": "has spaces"},
        {"related_evidence_ids": ["x"]},
    ):
        env = agent.observation("firewall", "ufw", {"enabled": True})
        env["context"] = bad
        assert agent.send(env)[0].status_code == 400


def test_unknown_catalogues_are_accepted_with_structural_checks(keen):
    agent, _ = enrolled(keen)
    env = agent.observation("firewall", "ufw", {"enabled": True})
    env["catalogue"] = {"id": "acme", "version": "1"}
    r, _ = agent.send(env)
    assert r.status_code == 200, r.text
    with keen.db() as db:
        assert db.query(UeuEvidence).one().schema_validation == "catalogue_unknown"


# -- chains ------------------------------------------------------------------


def test_chain_records_gaps_breaks_restarts_and_late_records(keen):
    agent, _ = enrolled(keen)

    def send_one(**fiddle):
        env = agent.observation("firewall", "ufw", {"enabled": True})
        sealed = agent.seal(env)
        if fiddle:
            env["integrity"].update(fiddle)
            sealed["bytes"] = canonical(env)
            sealed["hash"] = "sha256:" + hashlib.sha256(sealed["bytes"]).hexdigest()
            agent.last_hash = sealed["hash"]
        r = agent.post("/v1/otlp/logs", agent.otlp([sealed]), "otlp-logs")
        assert r.status_code == 200, r.text
        return env["evidence_id"]

    statuses = {}
    statuses[send_one()] = "started"
    statuses[send_one()] = "continuous"
    agent.sequence += 3
    statuses[send_one()] = "gap"
    statuses[send_one(previous_evidence_hash="sha256:" + "0" * 64)] = "broken"
    agent.sequence = 0
    agent.last_hash = None
    statuses[send_one()] = "restarted"
    with keen.db() as db:
        got = {r.evidence_id: r.chain_status for r in db.query(UeuEvidence)}
    assert got == statuses


# -- custody and evidence modes ------------------------------------------------


def test_payloads_are_redacted_with_a_custody_record(keen):
    agent, _ = enrolled(keen, evidence_mode_ceiling="all_lines")
    line = "Oct 27 sshd[1]: login password=hunter2 accepted"
    env = agent.event(
        "log.line.collected",
        "log_line",
        {"source": "auth", "parser": "syslog"},
        component="auth",
        payload=line,
        mode="all_lines",
        outcome="info",
    )
    r, sealed = agent.send(env)
    assert r.status_code == 200, r.text
    with keen.db() as db:
        event = db.query(Event).one()
        row = db.get(UeuEvidence, event.id)
        assert "hunter2" not in json.dumps(event.normalized_payload)
        assert row.custody["changes"] == [
            {"path": "evidence.payload", "reason": "sensitive_value"}
        ]
        assert row.custody["original_evidence_hash"] == sealed[0]["hash"]
        stored = keen.objects[db.query(Artifact).one().storage_uri]
        assert b"hunter2" not in stored and stored != sealed[0]["bytes"]


def test_the_ceiling_follows_content_when_the_mode_label_is_omitted(keen):
    agent, _ = enrolled(keen)
    line = "Oct 27 sshd: session opened for alice"
    whole = agent.event(
        "log.line.collected",
        "log_line",
        {"source": "auth", "parser": "syslog"},
        payload=line,
        mode="all_lines",
        outcome="info",
    )
    recognised = agent.event(
        "software.package.changed",
        "package",
        {"manager": "dpkg", "action": "install", "package": "openssl"},
        payload=line,
        mode="events_with_lines",
    )
    for env in (whole, recognised):
        del env["evidence"]["collection_mode"]
    r, _ = agent.send(whole, recognised)
    assert r.status_code == 200, r.text
    with keen.db() as db:
        event = db.query(Event).one()
        assert event.action == "software.package.changed"
        assert "payload" not in event.normalized_payload["ueu"]["evidence"]
        assert b"alice" not in json.dumps(event.normalized_payload).encode()
        assert db.get(KeenAgent, agent.agent_id).health["ueu_withheld_total"] == 1


def test_undeclared_integers_under_secret_names_are_redacted():
    from app.agents.ueu.custody import redact

    doc, changes = redact(
        {"attributes": {"password": 123456, "count": 5, "password_set": True}},
        None,
    )
    assert doc["attributes"] == {
        "password": "REDACTED",
        "count": 5,
        "password_set": True,
    }
    assert changes == [{"path": "attributes.password", "reason": "sensitive_name"}]


def test_duplicate_json_names_cannot_hide_a_secret():
    from app.security.redaction import redact_str

    out = redact_str('{"password":"hunter2","password":"REDACTED"}')
    assert "hunter2" not in out


def test_signed_requests_are_limited_before_nonces_are_stored(keen, monkeypatch):
    from app.agents import credentials

    agent, _ = enrolled(keen)
    monkeypatch.setattr(credentials, "SIGNED_PER_MINUTE", 2)
    assert agent.heartbeat().status_code == 200
    assert agent.heartbeat().status_code == 200
    stored = len(credentials.get_valkey().keys)
    r = agent.heartbeat()
    assert r.status_code == 429 and r.headers["retry-after"]
    assert len(credentials.get_valkey().keys) == stored


def test_token_agents_cannot_report_posture_and_can_move_to_keys(keen):
    r = keen.client.post(
        "/v1/admin/agents",
        json={"name": "relay", "credential_type": "token"},
    )
    assert r.status_code in (200, 201), r.text
    agent_id = r.json()["agent"]["id"]
    with keen.db() as db:
        from app.db.models import IsmsPerson

        person = IsmsPerson(name="Alex")
        db.add(person)
        db.commit()
        person_id = str(person.id)
    r = keen.client.patch(
        f"/v1/admin/agents/{agent_id}",
        json={"agent_type": "workstation", "person_id": person_id},
    )
    assert r.status_code == 409
    r = keen.client.post(
        f"/v1/admin/agents/{agent_id}/rotate",
        json={"expires_days": 30, "credential_type": "key"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"]["credential_type"] == "key"
    assert body["enrollment_code"].startswith("kc_" + agent_id + ".")
    assert "token" not in body
    with keen.db() as db:
        row = db.get(KeenAgent, agent_id)
        assert row.token_hash == "" and row.previous_token_hash is None


def test_the_evidence_mode_ceiling_is_enforced_on_receipt(keen):
    agent, info = enrolled(keen)
    line = "Oct 27 dpkg: status installed openssl:amd64 3.0.15"
    whole = agent.event(
        "log.line.collected",
        "log_line",
        {"source": "dpkg", "parser": "dpkg"},
        payload=line,
        mode="all_lines",
        outcome="info",
    )
    recognised = agent.event(
        "software.package.changed",
        "package",
        {"manager": "dpkg", "action": "install", "package": "openssl"},
        payload=line,
        mode="events_with_lines",
    )
    r, _ = agent.send(whole, recognised)
    assert r.status_code == 200, r.text
    with keen.db() as db:
        assert db.query(Event).count() == 1
        event = db.query(Event).one()
        assert event.action == "software.package.changed"
        assert "payload" not in event.normalized_payload["ueu"]["evidence"]
        row = db.get(UeuEvidence, event.id)
        assert row.custody["changes"] == [
            {"path": "evidence.payload", "reason": "above_evidence_mode_ceiling"}
        ]
        assert db.get(KeenAgent, agent.agent_id).health["ueu_withheld_total"] == 1
    r = keen.client.patch(
        f"/v1/admin/agents/{info['agent']['id']}/evidence",
        json={"evidence_mode_ceiling": "all_lines"},
    )
    assert (
        r.status_code == 200
        and r.json()["agent"]["evidence_mode_ceiling"] == "all_lines"
    )


# -- renewal and revocation ----------------------------------------------------


def test_renewal_replaces_the_key_and_a_lost_response_can_be_retried(keen):
    agent, _ = enrolled(keen)
    old_key, old_id = agent.key, agent.key_id
    r, new, nonce = agent.renew()
    assert r.status_code == 200, r.text
    grant = r.json()
    assert grant["key_id"] == key_id(agent.agent_id, new)
    # Retrying with the retired key and the same nonce returns the same grant.
    again, _, _ = agent.renew(nonce=nonce, new=new)
    assert again.status_code == 200 and again.json()["key_id"] == grant["key_id"]
    # The retired key authorises nothing else.
    assert agent.heartbeat().status_code == 401
    other, _, _ = agent.renew(new=Ed25519PrivateKey.generate())
    assert other.status_code == 401
    agent.accept(grant, new)
    assert agent.heartbeat().status_code == 200
    with keen.db() as db:
        keys = {k.key_id: k for k in db.query(AgentKey)}
        assert keys[old_id].state == "retired"
        assert keys[agent.key_id].previous_key_id == old_id
    # A key cannot be bound twice.
    reuse, _, _ = agent.renew(new=old_key)
    assert reuse.status_code == 409


def test_admin_codes_rotate_keys_and_revocation_ends_access(keen):
    agent, info = enrolled(keen)
    ident = info["agent"]["id"]
    code = keen.client.post(f"/v1/admin/agents/{ident}/rotate", json={}).json()
    assert code["enrollment_code"].startswith("kc_" + ident)
    # The current key keeps working until the new code is used.
    assert agent.heartbeat().status_code == 200
    replacement = SimAgent(keen.client)
    assert replacement.enroll(code["enrollment_code"]).status_code == 201
    assert agent.heartbeat().status_code == 401
    assert replacement.heartbeat().status_code == 200
    assert keen.client.post(f"/v1/admin/agents/{ident}/revoke").status_code == 200
    assert replacement.heartbeat().status_code == 401
    with keen.db() as db:
        assert {k.state for k in db.query(AgentKey)} == {"revoked"}


def test_public_subjects_need_the_integration_key(keen, monkeypatch):
    monkeypatch.delenv("KEEN_INTEGRATION_SECRET_KEY", raising=False)
    r = keen.client.post(
        "/v1/admin/agents", json={"name": "a", "subject_type": "public"}
    )
    assert r.status_code == 503
    from cryptography.fernet import Fernet

    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    one, _ = enrolled(keen, name="a", subject_type="public")
    two, _ = enrolled(keen, name="b", subject_type="public")
    assert one.grant["subject"]["type"] == "public"
    assert one.grant["subject"]["key"] == two.grant["subject"]["key"]
    assert one.pseudonym("unix_uid", "1000") == two.pseudonym("unix_uid", "1000")


# -- verification is shown, not exported ---------------------------------------


def test_verification_bundle_downloads_are_gone(keen):
    # Keen shows each record's checks on the event page; it no longer packages
    # envelopes, manifests and keys for offline verification.
    agent, info = enrolled(keen)
    agent.send(agent.observation("firewall", "ufw", {"enabled": True}))
    with keen.db() as db:
        event_id = db.query(Event).one().id
    since = stamp(datetime.now(timezone.utc) - timedelta(hours=1))
    until = stamp(datetime.now(timezone.utc) + timedelta(hours=1))
    assert (
        keen.client.get(
            f"/v1/admin/agents/{info['agent']['id']}/evidence-bundle",
            params={"since": since, "until": until},
        ).status_code
        == 404
    )
    assert keen.client.get(f"/v1/events/{event_id}/ueu-bundle").status_code == 404
    assert not (ROOT / "tools/ueu_verify_bundle.py").exists()
    with keen.db() as db:
        # The checks stay recorded and shown with the event.
        assert evidence_record(db, event_id)["verification"] == "verified"


def test_manifest_signature_material_is_not_redacted_from_storage(keen):
    agent, _ = enrolled(keen)
    r, _ = agent.send(agent.observation("firewall", "ufw", {"enabled": True}))
    assert r.status_code == 200
    with keen.db() as db:
        batch = db.query(UeuBatch).one()
        manifest = json.loads(batch.manifest)
        assert manifest["context"] == "keen-ueu-batch-v1"
        signatures.verify_detached(
            db.get(AgentKey, batch.key_id).public_key,
            batch.manifest.encode(),
            batch.signature,
        )
        pointer = db.query(Event).one().raw_pointer["ueu"]
        assert pointer["verification"] == "verified"
