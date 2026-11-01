"""End-to-end encryption between keen-agent and KEEN: KEEN's identity and
encryption keys, sealed requests, sealed responses, rotation and erasure."""

import json
from datetime import timedelta
from types import SimpleNamespace

import app.services.agent_enrollment as enrollment_operations
import pytest
from app.agents import server_keys
from app.core.datetime_utils import utc_now_naive
from app.db.models import AgentServerKey
from app.security import request_limits
from app.services.errors import DomainError
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tests.test_keen_agent import setup as setup
from tests.ueu_agent import (
    SEALED,
    FakeValkey,
    SimAgent,
    response_aad,
    verify_server_key,
)


@pytest.fixture
def keen(setup, monkeypatch):
    client, factory, _, _, _, app = setup
    monkeypatch.setattr(enrollment_operations, "get_valkey", lambda: None)
    proofs = FakeValkey()
    monkeypatch.setattr(enrollment_operations, "proof_store", lambda: proofs)
    monkeypatch.setattr(
        enrollment_operations, "fixed_window_allow", lambda *a, **k: (True, 0)
    )
    return SimpleNamespace(client=client, db=factory, app=app)


def register(keen, **extra):
    r = keen.client.post("/v1/admin/agents", json={"name": "web-01", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def enrolled(keen):
    info = register(keen)
    agent = SimAgent(keen.client)
    r = agent.enroll(info["enrollment_code"])
    assert r.status_code == 201, r.text
    return agent, info


def age_keys(keen, delta):
    """Move every key's clock back by delta, as if time had passed."""
    with keen.db() as db:
        for row in db.query(AgentServerKey):
            row.created_at -= delta
            if row.not_after is not None:
                row.not_after -= delta
        db.commit()


# -- the identity key and the server-key document --------------------------


def test_codes_bootstrap_keys_and_the_document_name_one_identity(keen):
    assert keen.client.get("/v1/admin/agents").json()["server_key_fingerprint"] is None
    # An anonymous request never creates KEEN's keys.
    assert keen.client.get("/v1/agents/server-key").status_code == 404
    with keen.db() as db:
        assert db.query(AgentServerKey).count() == 0
    code = register(keen)["enrollment_code"]
    profile = keen.client.post(
        "/v1/admin/agent-enrollment-profiles", json={"name": "fleet"}
    ).json()
    pin = code.rsplit(".", 1)[1]
    assert profile["bootstrap_key"].rsplit(".", 1)[1] == pin
    assert keen.client.get("/v1/admin/agents").json()["server_key_fingerprint"] == pin
    first = keen.client.get("/v1/agents/server-key")
    assert first.headers["cache-control"] == "no-store"
    statement = verify_server_key(first.json(), pin)
    # The document is stable until the key is due for rotation.
    assert keen.client.get("/v1/agents/server-key").json() == first.json()
    assert statement["key_id"] == server_keys.ENCRYPTION_KEY_ID.fullmatch(
        statement["key_id"]
    ).group(0)
    with keen.db() as db:
        rows = db.query(AgentServerKey).all()
        assert sorted(r.purpose for r in rows) == ["encryption", "identity"]
        # Private halves are stored only encrypted with the integration key.
        for row in rows:
            assert row.private_key.startswith("gAAAAA")


def test_a_tampered_document_is_refused_by_the_agent(keen):
    pin = register(keen)["enrollment_code"].rsplit(".", 1)[1]
    doc = keen.client.get("/v1/agents/server-key").json()
    forged = json.loads(json.dumps(doc))
    forged["encryption_key"]["public_key"] = doc["encryption_key"]["public_key"][::-1]
    with pytest.raises(Exception):
        verify_server_key(forged, pin)
    with pytest.raises(AssertionError):
        verify_server_key(doc, "A" * 22)


def test_keys_need_the_integration_secret(keen, monkeypatch):
    monkeypatch.delenv("KEEN_INTEGRATION_SECRET_KEY")
    r = keen.client.post("/v1/admin/agents", json={"name": "web-01"})
    assert r.status_code == 503
    assert "KEEN_INTEGRATION_SECRET_KEY" in r.json()["detail"]
    # Bearer-token collectors do not use KEEN's keys.
    token = keen.client.post(
        "/v1/admin/agents", json={"name": "alloy", "credential_type": "token"}
    )
    assert token.status_code == 201
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    register(keen)
    # A different secret cannot read the stored keys.
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    r = keen.client.get("/v1/agents/server-key")
    assert r.status_code == 503
    assert "cannot be read" in r.json()["detail"]


def test_an_inconsistent_identity_row_is_refused(keen):
    register(keen)
    with keen.db() as db:
        row = server_keys.active(db, "identity")
        row.public_key = "A" * 43
        db.commit()
    assert keen.client.get("/v1/agents/server-key").status_code == 503


# -- sealed requests ---------------------------------------------------------


def test_every_signed_request_and_response_is_sealed(keen):
    agent, _ = enrolled(keen)
    r, _ = agent.send(agent.observation("firewall", "ufw", {"enabled": True}))
    assert r.status_code == 200 and r.json() == {}
    assert r.headers["content-type"] == "application/vnd.keen.sealed-response"
    assert r.headers["keen-encryption-key"] == agent.server["key_id"]
    heartbeat = agent.heartbeat({"version": "0.3.0"})
    assert heartbeat.status_code == 200 and heartbeat.json() == {"ok": True}
    renewed, new, _ = agent.renew()
    assert renewed.status_code == 200
    assert renewed.json()["key_id"] != agent.key_id
    assert b"key_id" not in renewed.sealed


def test_a_sealed_response_opens_only_with_its_request_key_and_purpose(keen):
    agent, _ = enrolled(keen)
    plain = json.dumps({"version": "0.3.0"}).encode()
    wire, response_key = agent.encrypt(plain, "heartbeat", agent.key_id)
    r = agent.post_raw("/v1/agents/heartbeat", wire, "heartbeat")
    assert r.status_code == 200
    raw = r.content
    nonce, sealed = raw[:12], raw[12:]
    assert AESGCM(response_key).decrypt(
        nonce, sealed, response_aad("heartbeat", 200)
    ) == (b'{"ok":true}')
    # An impostor without KEEN's key cannot make one; nor does this one
    # pass for another operation's or status's answer.
    for key, aad in (
        (AESGCM.generate_key(256), response_aad("heartbeat", 200)),
        (response_key, response_aad("otlp-logs", 200)),
        (response_key, response_aad("heartbeat", 201)),
    ):
        with pytest.raises(InvalidTag):
            AESGCM(key).decrypt(nonce, sealed, aad)


def test_plaintext_and_compressed_requests_from_key_agents_are_refused(keen):
    agent, _ = enrolled(keen)
    body = json.dumps({"version": "0.2.2"}).encode()
    plain = agent.post_raw(
        "/v1/agents/heartbeat", body, "heartbeat", content_type="application/json"
    )
    assert plain.status_code == 415
    assert "upgrade keen-agent" in plain.json()["detail"]
    otlp = agent.post_raw(
        "/v1/otlp/logs", body, "otlp-logs", content_type="application/json"
    )
    assert otlp.status_code == 415 and otlp.json()["code"] == 12
    wire, _ = agent.encrypt(body, "heartbeat", agent.key_id)
    headers = agent.signed_headers(wire, "heartbeat")
    headers["Content-Encoding"] = "gzip"
    r = keen.client.post("/v1/agents/heartbeat", content=wire, headers=headers)
    assert r.status_code == 415


def test_sealed_bodies_are_bound_to_operation_sender_and_contents(keen):
    agent, _ = enrolled(keen)
    other, _ = enrolled(keen)
    body = json.dumps({"version": "0.3.0"}).encode()
    # Sealed for another operation.
    wire, _ = agent.encrypt(body, "otlp-logs", agent.key_id)
    assert agent.post_raw("/v1/agents/heartbeat", wire, "heartbeat").status_code == 400
    # Sealed by another agent, then re-signed: KEEN will not open it as ours.
    wire, _ = other.encrypt(body, "heartbeat", other.key_id)
    assert agent.post_raw("/v1/agents/heartbeat", wire, "heartbeat").status_code == 400
    # Altered, then re-signed.
    wire, _ = agent.encrypt(body, "heartbeat", agent.key_id)
    altered = wire[:-1] + bytes([wire[-1] ^ 1])
    assert (
        agent.post_raw("/v1/agents/heartbeat", altered, "heartbeat").status_code == 400
    )
    # Too short to hold a sealed body.
    assert (
        agent.post_raw("/v1/agents/heartbeat", wire[:100], "heartbeat").status_code
        == 400
    )
    # A malformed key identifier is not a signed request.
    headers = agent.signed_headers(wire, "heartbeat", key_id="not-hex")
    r = keen.client.post("/v1/agents/heartbeat", content=wire, headers=headers)
    assert r.status_code == 401


def test_enrolment_is_sealed_and_names_its_key(keen):
    info = register(keen)
    agent = SimAgent(keen.client)
    agent.fetch_server_key(info["enrollment_code"])
    body = json.dumps(agent.enrollment(info["enrollment_code"])).encode()
    wire, _ = agent.encrypt(body, "enroll")
    headers = agent.enrollment_headers()
    headers.pop("Keen-Encryption-Key")
    r = keen.client.post("/v1/agents/enroll", content=wire, headers=headers)
    assert r.status_code == 400
    # Not JSON once opened.
    assert agent.post_enrollment(info["enrollment_code"], {}, raw=b"[").status_code == (
        400
    )
    assert agent.enroll(info["enrollment_code"]).status_code == 201


def test_unknown_keys_get_421_so_the_agent_fetches_the_document(keen):
    agent, _ = enrolled(keen)
    body = json.dumps({"version": "0.3.0"}).encode()
    r = agent.post("/v1/agents/heartbeat", body, "heartbeat", key_id="0" * 16)
    assert r.status_code == 421
    sealed_logs = agent.post("/v1/otlp/logs", b"{}", "otlp-logs", key_id="0" * 16)
    assert sealed_logs.status_code == 421 and sealed_logs.json()["code"] == 14


def test_plaintext_limits_apply_after_opening(keen):
    agent, _ = enrolled(keen)
    big = json.dumps({"version": "x" * 33000}).encode()
    assert agent.post("/v1/agents/heartbeat", big, "heartbeat").status_code == 413
    assert request_limits._SEALED_OVERHEAD == server_keys.SEALED_OVERHEAD
    assert request_limits.body_limit("/v1/agents/heartbeat") == (
        32 * 1024 + server_keys.SEALED_OVERHEAD
    )


def test_oversized_sealed_batches_are_refused_after_opening(keen, monkeypatch):
    from app.api.routes import agents as api

    agent, _ = enrolled(keen)
    monkeypatch.setattr(api, "MAX_BODY", 1000)
    r = agent.post("/v1/otlp/logs", b"x" * 1001, "otlp-logs")
    assert r.status_code == 413


# -- rotation and erasure ----------------------------------------------------


def test_rotation_keeps_the_old_key_until_agents_must_stop_using_it(keen):
    agent, info = enrolled(keen)
    old = agent.server["key_id"]
    age_keys(keen, server_keys.ROTATE_AFTER)
    # The next document carries a new key, signed by the same identity.
    doc = keen.client.get("/v1/agents/server-key").json()
    new = verify_server_key(doc, agent.pinned)
    assert new["key_id"] != old
    # Responses name the active key, so running agents notice the change.
    r = agent.heartbeat()
    assert r.status_code == 200 and r.headers["keen-encryption-key"] == new["key_id"]
    with keen.db() as db:
        assert db.get(AgentServerKey, old).state == "retired"
    # Once agents may no longer use it, the old key is refused, then erased,
    # and this process forgets it too.
    assert old in server_keys._OPENED
    age_keys(keen, server_keys.VALID_FOR - server_keys.ROTATE_AFTER)
    age_keys(keen, server_keys.DESTROY_GRACE + timedelta(seconds=1))
    assert agent.heartbeat().status_code == 421
    assert old not in server_keys._OPENED
    with keen.db() as db:
        assert server_keys.destroy_expired(db, utc_now_naive()) >= 1
        db.commit()
        row = db.get(AgentServerKey, old)
        assert row.state == "destroyed" and row.private_key is None
        assert row.destroyed_at is not None
    # The agent fetches the document again and carries on.
    agent.fetch_server_key(pinned=agent.pinned)
    assert agent.heartbeat().status_code == 200


def test_scheduled_maintenance(keen, monkeypatch):
    with keen.db() as db:
        assert server_keys.scheduled_maintenance(db) == {"identity": False}
    register(keen)
    keen.client.get("/v1/agents/server-key")
    age_keys(keen, server_keys.VALID_FOR + timedelta(days=1))
    with keen.db() as db:
        first = server_keys.active_key_id(db)
        result = server_keys.scheduled_maintenance(db)
    assert result["identity"] is True and result["encryption_key"] != first
    with keen.db() as db:
        states = sorted(
            r.state for r in db.query(AgentServerKey).filter_by(purpose="encryption")
        )
        assert states == ["active", "destroyed"]
    monkeypatch.delenv("KEEN_INTEGRATION_SECRET_KEY")
    with keen.db() as db:
        result = server_keys.scheduled_maintenance(db)
    assert (
        result["identity"] is True
        and "KEEN_INTEGRATION_SECRET_KEY" in (result["error"])
    )


def test_the_worker_task_runs_maintenance(monkeypatch):
    from app.worker import tasks

    closed = []

    class Session:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(tasks, "SessionLocal", Session)
    monkeypatch.setattr(
        server_keys, "scheduled_maintenance", lambda db: {"identity": False}
    )
    assert tasks.agent_server_keys_task() == {"identity": False}
    assert closed == [True]


def test_a_retired_key_from_an_earlier_identity_is_replaced(keen):
    register(keen)
    keen.client.get("/v1/agents/server-key")
    with keen.db() as db:
        row = server_keys.active(db, "encryption")
        row.statement = {**row.statement, "identity": "A" * 22}
        db.commit()
        old = row.key_id
    doc = keen.client.get("/v1/agents/server-key").json()
    assert doc["encryption_key"]["key_id"] != old


def test_opened_keys_leave_memory_when_they_may_no_longer_be_used(keen):
    agent, _ = enrolled(keen)
    kid = agent.server["key_id"]
    assert agent.heartbeat().status_code == 200 and kid in server_keys._OPENED
    sealed, until, opened = server_keys._OPENED[kid]
    # Another key past its time is dropped on the next lookup.
    server_keys._OPENED["0" * 16] = (sealed, until - timedelta(days=400), opened)
    assert agent.heartbeat().status_code == 200
    assert "0" * 16 not in server_keys._OPENED and kid in server_keys._OPENED
    server_keys.forget_opened()
    assert server_keys._OPENED == {}
    assert agent.heartbeat().status_code == 200


def test_an_inconsistent_encryption_key_is_refused(keen):
    agent, _ = enrolled(keen)
    with keen.db() as db:
        row = server_keys.active(db, "encryption")
        row.private_key = server_keys.seal_private(b"short")
        db.commit()
    assert agent.heartbeat().status_code == 503


# -- replacing the identity --------------------------------------------------


def test_resetting_the_identity_strands_every_earlier_credential(keen, capsys):
    import agent_server_key

    agent, info = enrolled(keen)
    profile = keen.client.post(
        "/v1/admin/agent-enrollment-profiles", json={"name": "fleet"}
    ).json()
    old_pin = agent.pinned
    agent_server_key.main([], session=keen.db)
    assert capsys.readouterr().out.strip() == old_pin
    with pytest.raises(SystemExit):
        agent_server_key.main(
            ["--reset", "--confirm-fingerprint", "A" * 22], session=keen.db
        )
    agent_server_key.main(
        ["--reset", "--confirm-fingerprint", old_pin], session=keen.db
    )
    out = capsys.readouterr().out
    with keen.db() as db:
        new_pin = server_keys.current_fingerprint(db)
        assert new_pin != old_pin and new_pin in out
        assert (
            db.query(AgentServerKey).filter(AgentServerKey.state != "destroyed").count()
            == 2
        )
    # Requests sealed to the old key cannot be opened...
    assert agent.heartbeat().status_code == 421
    # ...and the new document fails the agent's pin.
    with pytest.raises(AssertionError):
        agent.fetch_server_key(pinned=old_pin)
    # Credentials issued under the old identity are refused by KEEN as well.
    fresh = SimAgent(keen.client)
    body = {
        **fresh.enrollment(profile["bootstrap_key"]),
        "name": "h",
        "nonce": "n" * 43,
    }
    fresh.fetch_server_key(pinned=new_pin)
    assert fresh.post_enrollment(profile["bootstrap_key"], body).status_code == 401
    # A new code works.
    rotated = keen.client.post(
        "/v1/admin/agents/" + info["agent"]["id"] + "/rotate", json={}
    ).json()
    assert rotated["enrollment_code"].endswith("." + new_pin)


def test_the_script_reports_a_missing_identity(keen, capsys):
    import agent_server_key

    agent_server_key.main([], session=keen.db)
    assert "no agent identity key yet" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        agent_server_key.main(
            ["--reset", "--confirm-fingerprint", "A" * 22], session=keen.db
        )


def test_document_and_open_request_in_isolation(keen):
    register(keen)
    with keen.db() as db:
        with pytest.raises(DomainError) as missing:
            server_keys.open_request(
                db, {"content-type": SEALED}, b"", "heartbeat", "a:b"
            )
        assert missing.value.status_code == 400
        assert server_keys.request_info("enroll", "k", "") == (
            b"keen-agent-request-v1\x00enroll\x00k\x00"
        )


@pytest.mark.filterwarnings("ignore:New instance:sqlalchemy.exc.SAWarning")
def test_concurrent_key_creation_keeps_one_active_key(keen, monkeypatch):
    register(keen)
    keen.client.get("/v1/agents/server-key")
    now = utc_now_naive()
    with keen.db() as db:
        ident = server_keys.identity(db)
        current = server_keys.active_key_id(db)
        # Another request made the identity first.
        duplicate = AgentServerKey(
            key_id="B" * 22,
            purpose="identity",
            public_key="B" * 43,
            private_key="sealed",
            state="active",
            created_at=now,
        )
        assert server_keys.insert(db, duplicate).key_id == ident.fingerprint
        # Another request rotated between our read and our lock.
        real = server_keys.active
        reads = []

        def stale(session, purpose):
            if not reads:
                reads.append(purpose)
                return None
            return real(session, purpose)

        monkeypatch.setattr(server_keys, "active", stale)
        assert server_keys.rotate_if_due(db, ident, now).key_id == current
        monkeypatch.setattr(server_keys, "active", real)
        # Another request inserted the same new key first.
        clash = db.get(AgentServerKey, current)
        monkeypatch.setattr(
            server_keys,
            "new_encryption_key",
            lambda ident, now: AgentServerKey(
                key_id=clash.key_id,
                purpose="encryption",
                public_key=clash.public_key,
                private_key="sealed",
                state="active",
                created_at=now,
            ),
        )
        later = now + server_keys.ROTATE_AFTER
        assert server_keys.rotate_if_due(db, ident, later).key_id == current
        assert server_keys.active(db, "encryption").state == "active"


def test_an_unreadable_renewal_is_refused(keen):
    agent, _ = enrolled(keen)
    assert agent.post("/v1/agents/renew", b"{", "renew").status_code == 400
