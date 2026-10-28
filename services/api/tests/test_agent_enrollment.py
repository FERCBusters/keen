"""Enrollment scope, revocation, nonce recovery and key renewal boundaries."""

import secrets
from datetime import timedelta

import app.services.agent_enrollment as agent_enrollment_operations
import pytest
from app.agents.credentials import digest
from app.api.context import change_context
from app.api.routes import agent_enrollment as api
from app.core.datetime_utils import utc_now_naive
from app.db.models import AgentEnrollmentProfile, AgentKey, AuditLog, KeenAgent
from app.services.errors import DomainError
from starlette.requests import Request

from tests.test_keen_agent import record

# Imported fixtures are discovered by pytest by name.
from tests.test_keen_agent import setup as setup
from tests.ueu_agent import FakeValkey, SimAgent


@pytest.fixture
def fleet(setup, monkeypatch):
    client, factory, headers, info, stored, app = setup
    monkeypatch.setattr(agent_enrollment_operations, "get_valkey", lambda: None)
    proofs = FakeValkey()
    monkeypatch.setattr(agent_enrollment_operations, "proof_store", lambda: proofs)
    monkeypatch.setattr(
        agent_enrollment_operations, "fixed_window_allow", lambda *a, **k: (True, 0)
    )
    r = client.post(
        "/v1/admin/agent-enrollment-profiles",
        json={
            "name": "Production web",
            "max_enrollments": 2,
            "labels": {"environment": "prod"},
        },
    )
    assert r.status_code == 201, r.text
    p = r.json()
    return client, factory, p, headers


def enroll(client, p, nonce=None, name="web-01", agent=None):
    agent = agent or SimAgent(client)
    r = agent.enroll(
        p["bootstrap_key"], name=name, nonce=nonce or secrets.token_urlsafe(32)
    )
    r.sim = agent
    return r


def action(client, p, name):
    return client.post(
        "/v1/admin/agent-enrollment-profiles/" + p["profile"]["id"] + "/" + name
    )


def firewall(agent):
    return agent.send(agent.observation("firewall", "ufw", {"enabled": True}))[0]


def test_profile_key_hash_and_enrollment_scope(fleet):
    c, f, p, _ = fleet
    with f() as db:
        row = db.get(AgentEnrollmentProfile, p["profile"]["id"])
        assert row.key_hash == digest(p["bootstrap_key"])
    listing = c.get("/v1/admin/agent-enrollment-profiles").json()
    assert "bootstrap_key" not in str(listing) and "key_hash" not in str(listing)
    assert listing["profiles"][0]["evidence_mode_ceiling"] == "events"
    r = enroll(c, p)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["agent"]["enrollment_profile_id"] == p["profile"]["id"]
    assert "token" not in a and a["subject"] == {"type": "pairwise"}
    # The bootstrap key enrols; it never authenticates evidence.
    assert (
        c.post(
            "/v1/otlp/logs",
            json=record(),
            headers={"Authorization": "Bearer " + p["bootstrap_key"]},
        ).status_code
        == 401
    )
    assert firewall(r.sim).status_code == 200
    with f() as db:
        assert db.query(AuditLog).filter_by(path="/v1/agents/enrolled").count() == 1
        agent = db.get(KeenAgent, a["agent"]["id"])
        assert agent.enrollment_labels == {"environment": "prod"}
        assert agent.credential_type == "key" and agent.token_hash == ""


def test_lost_response_retry_quota_and_agent_revocation(fleet):
    c, f, p, _ = fleet
    n = secrets.token_urlsafe(32)
    first = enroll(c, p, n)
    a = first.json()
    # The same nonce and key return the same agent and key.
    again = enroll(c, p, n, agent=first.sim)
    assert again.status_code == 201 and again.json()["key_id"] == a["key_id"]
    # The same nonce cannot bind another key or another name.
    assert enroll(c, p, n).status_code == 409
    assert enroll(c, p, n, name="another", agent=first.sim).status_code == 409
    assert enroll(c, p).status_code == 201
    assert enroll(c, p).status_code == 403
    c.post("/v1/admin/agents/" + a["agent"]["id"] + "/revoke")
    assert enroll(c, p, n, agent=first.sim).status_code == 409
    with f() as db:
        assert db.get(AgentEnrollmentProfile, p["profile"]["id"]).enrollment_count == 2


def test_pause_rotate_revoke_and_manual_independence(fleet):
    c, f, p, manual = fleet
    sim = enroll(c, p).sim
    assert action(c, p, "pause").status_code == 200
    assert enroll(c, p).status_code == 403
    assert firewall(sim).status_code == 200
    assert action(c, p, "resume").status_code == 200
    updated = action(c, p, "rotate").json()
    assert enroll(c, p).status_code == 401
    assert enroll(c, updated).status_code == 201
    assert firewall(sim).status_code == 200
    assert action(c, p, "revoke").status_code == 200
    assert firewall(sim).status_code == 401
    assert sim.renew()[0].status_code == 401
    assert enroll(c, updated).status_code == 401
    assert action(c, p, "resume").status_code == 409
    assert c.post("/v1/otlp/logs", json=record(), headers=manual).status_code == 200
    with f() as db:
        assert not any(
            a.enabled
            for a in db.query(KeenAgent).filter_by(
                enrollment_profile_id=p["profile"]["id"]
            )
        )
        assert {k.state for k in db.query(AgentKey)} == {"revoked"}


def test_renewal_retry_window_and_nonce_binding(fleet):
    c, f, p, _ = fleet
    sim = enroll(c, p).sim
    old = sim.key_id
    r, new, nonce = sim.renew()
    assert r.status_code == 200, r.text
    again, _, _ = sim.renew(nonce=nonce, new=new)
    assert again.status_code == 200 and again.json() == r.json()
    assert sim.renew()[0].status_code == 401
    assert firewall(sim).status_code == 401
    with f() as db:
        key = db.get(AgentKey, old)
        key.retired_at = utc_now_naive() - timedelta(hours=25)
        db.commit()
    assert sim.renew(nonce=nonce, new=new)[0].status_code == 401
    sim.accept(r.json(), new)
    assert firewall(sim).status_code == 200


def test_expiry_network_limits_and_limiter_failure(fleet, monkeypatch):
    c, f, p, _ = fleet
    with f() as db:
        row = db.get(AgentEnrollmentProfile, p["profile"]["id"])
        row.allowed_cidrs = ["192.0.2.0/24"]
        db.commit()
    # Forwarding headers cannot bypass an untrusted peer.
    assert enroll(c, p).status_code == 403
    from app.api import context as api_context

    monkeypatch.setattr(api_context, "request_ip", lambda request: "192.0.2.5")
    assert enroll(c, p).status_code == 201
    with f() as db:
        row = db.get(AgentEnrollmentProfile, p["profile"]["id"])
        row.expires_at = utc_now_naive() - timedelta(seconds=1)
        db.commit()
    assert enroll(c, p).status_code == 403
    monkeypatch.setattr(
        agent_enrollment_operations, "fixed_window_allow", lambda *a, **k: (False, 60)
    )
    assert enroll(c, p).status_code == 429


def test_admin_required_and_demo_block(fleet, monkeypatch):
    c, f, p, _ = fleet
    from app.core.config import settings

    monkeypatch.setattr(settings, "demo_mode", True)
    assert enroll(c, p).status_code == 403
    assert action(c, p, "rotate").status_code == 403


def test_session_admin_required(setup):
    c, _, _, _, _, app = setup
    app.dependency_overrides.pop(api.require_admin)
    assert c.get("/v1/admin/agent-enrollment-profiles").status_code in {401, 403}
    assert c.post(
        "/v1/admin/agent-enrollment-profiles", json={"name": "forbidden"}
    ).status_code in {401, 403}


def test_profile_revocation_checked_even_if_agent_flag_remains_enabled(fleet):
    c, f, p, _ = fleet
    sim = enroll(c, p).sim
    with f() as db:
        profile = db.get(AgentEnrollmentProfile, p["profile"]["id"])
        profile.state = "revoked"
        db.commit()
    assert firewall(sim).status_code == 401
    assert sim.heartbeat().status_code == 401
    assert sim.renew()[0].status_code == 401


def test_profile_and_nonce_validation(fleet):
    c, _, p, _ = fleet
    for values in [
        {"allowed_cidrs": ["invalid"]},
        {"token_days": 0},
        {"max_enrollments": 0},
        {"labels": {"": "invalid"}},
    ]:
        assert (
            c.post(
                "/v1/admin/agent-enrollment-profiles", json={"name": "bad", **values}
            ).status_code
            == 422
        )
    assert enroll(c, p, "short").status_code == 422
    for extra in ({"labels": {"admin": "true"}}, {"agent_type": "workstation"}):
        assert (
            c.post(
                "/v1/agents/enroll",
                json={
                    "name": "host",
                    "nonce": secrets.token_urlsafe(32),
                    "public_key": "A" * 43,
                    "proof": "A" * 86,
                    **extra,
                },
                headers={"Authorization": "Bearer " + p["bootstrap_key"]},
            ).status_code
            == 422
        )
    # Profile enrolment names the agent and carries a nonce.
    assert SimAgent(c).enroll(p["bootstrap_key"]).status_code == 400


def test_profile_expiry_must_be_a_future_instant_with_a_timezone(fleet):
    # The expiry is stored as UTC; naive or past values are refused.
    c, f, _, _ = fleet
    for value in ["2030-01-01T10:00:00", "2000-01-01T10:00:00+00:00"]:
        refused = c.post(
            "/v1/admin/agent-enrollment-profiles",
            json={"name": "bad", "expires_at": value},
        )
        assert refused.status_code == 422, refused.text
    accepted = c.post(
        "/v1/admin/agent-enrollment-profiles",
        json={"name": "zoned", "expires_at": "2030-01-01T10:00:00+10:00"},
    )
    assert accepted.status_code == 201, accepted.text
    with f() as db:
        row = db.get(AgentEnrollmentProfile, accepted.json()["profile"]["id"])
        assert row.expires_at.isoformat() == "2030-01-01T00:00:00"
    open_ended = c.post(
        "/v1/admin/agent-enrollment-profiles",
        json={"name": "open", "expires_at": None},
    )
    assert open_ended.status_code == 201, open_ended.text
    assert open_ended.json()["profile"]["expires_at"] is None


def test_unknown_profiles_actions_and_credentials_are_refused(fleet):
    c, f, p, manual = fleet
    import uuid

    assert action(c, p, "disable").status_code == 404
    assert (
        c.post(f"/v1/admin/agent-enrollment-profiles/{uuid.uuid4()}/pause").status_code
        == 404
    )
    for authorization in [
        "Bearer not-a-bootstrap-key",
        "Basic " + p["bootstrap_key"],
        "Bearer " + p["bootstrap_key"][:-1],
        "",
    ]:
        refused = c.post(
            "/v1/agents/enroll",
            json={
                "name": "web-01",
                "nonce": secrets.token_urlsafe(32),
                "public_key": "A" * 43,
                "proof": "A" * 86,
            },
            headers={"Authorization": authorization} if authorization else {},
        )
        assert refused.status_code in {401, 422}, authorization
    with f() as db:
        assert (
            db.query(KeenAgent)
            .filter_by(enrollment_profile_id=p["profile"]["id"])
            .count()
            == 0
        )
        assert db.get(AgentEnrollmentProfile, p["profile"]["id"]).state == "active"
    # Bearer-token collectors cannot use key renewal.
    renewal = c.post(
        "/v1/agents/renew",
        json={"nonce": secrets.token_urlsafe(32)},
        headers=manual,
    )
    assert renewal.status_code == 401
    assert c.post("/v1/otlp/logs", json=record(), headers=manual).status_code == 200


def revocation_request(ident, token):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/v1/admin/agent-enrollment-profiles/{ident}/revoke",
            "headers": [(b"authorization", ("Bearer " + token).encode())],
            "client": ("192.0.2.1", 1),
            "state": {},
        }
    )


def test_postgres_revocation_request_has_valid_http_context():
    context = change_context(revocation_request("profile-id", "synthetic-token"))
    assert context.method == "POST"
    assert context.path == "/v1/admin/agent-enrollment-profiles/profile-id/revoke"
    assert context.client_ip == "192.0.2.1"


def test_profile_locks_serialize_revocation_on_postgres(
    contract_postgres_engine, monkeypatch
):
    if contract_postgres_engine is None:
        pytest.skip("Requires PostgreSQL for row-lock concurrency")
    import uuid

    from app.agents.credentials import authenticate
    from app.core.config import settings
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session
    from starlette.responses import Response

    monkeypatch.setattr(settings, "demo_mode", False)
    ident = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    token = "ka_" + agent_id + "." + secrets.token_urlsafe(32)
    with Session(contract_postgres_engine) as db:
        db.add(
            AgentEnrollmentProfile(
                id=ident,
                name="Concurrency",
                key_hash="x" * 64,
                state="active",
                token_days=90,
                enrollment_count=1,
                allowed_cidrs=[],
                labels={},
            )
        )
        db.flush()
        db.add(
            KeenAgent(
                id=agent_id,
                name="host",
                credential_type="token",
                token_hash=digest(token),
                enrollment_profile_id=ident,
                enabled=True,
                health={},
                expires_at=utc_now_naive() + timedelta(days=1),
            )
        )
        db.commit()
    request = revocation_request(ident, token)
    with (
        Session(contract_postgres_engine) as first,
        Session(contract_postgres_engine) as second,
    ):
        authenticate(request, first, operation="otlp-logs", lock=True)
        second.execute(text("SET LOCAL lock_timeout='100ms'"))
        with pytest.raises(OperationalError):
            api.manage(ident, "revoke", request, Response(), second)
        second.rollback()
        first.rollback()
        api.manage(ident, "revoke", request, Response(), second)
        from fastapi import HTTPException

        with pytest.raises((HTTPException, DomainError)) as exc:
            authenticate(request, first, operation="otlp-logs", lock=True)
        assert exc.value.status_code == 401
