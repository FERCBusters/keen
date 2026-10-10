"""Enrollment scope, revocation, nonce recovery and renewal boundaries."""

import secrets
from datetime import timedelta

import pytest
from app.agents.credentials import digest
from app.api.routes import agent_enrollment as api
from app.core.datetime_utils import utc_now_naive
from app.db.models import AgentEnrollmentProfile, AuditLog, KeenAgent

from tests.test_keen_agent import record


@pytest.fixture
def fleet(setup, monkeypatch):
    client, factory, headers, info, stored, app = setup
    monkeypatch.setattr(api, "get_valkey", lambda: None)
    monkeypatch.setattr(api, "fixed_window_allow", lambda *a, **k: (True, 0))
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


def enroll(client, p, nonce=None, name="web-01"):
    return client.post(
        "/v1/agents/enroll",
        json={"name": name, "nonce": nonce or secrets.token_urlsafe(32)},
        headers={"Authorization": "Bearer " + p["bootstrap_key"]},
    )


def action(client, p, name):
    return client.post(
        "/v1/admin/agent-enrollment-profiles/" + p["profile"]["id"] + "/" + name
    )


def test_profile_key_hash_and_enrollment_scope(fleet):
    c, f, p, _ = fleet
    with f() as db:
        row = db.get(AgentEnrollmentProfile, p["profile"]["id"])
        assert row.key_hash == digest(p["bootstrap_key"])
    listing = c.get("/v1/admin/agent-enrollment-profiles").json()
    assert "bootstrap_key" not in str(listing) and "key_hash" not in str(listing)
    r = enroll(c, p)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["agent"]["enrollment_profile_id"] == p["profile"]["id"]
    assert a["agent"]["enrollment_labels"] == {"environment": "prod"}
    assert (
        c.post(
            "/v1/otlp/logs",
            json=record(),
            headers={"Authorization": "Bearer " + p["bootstrap_key"]},
        ).status_code
        == 401
    )
    assert (
        c.post(
            "/v1/otlp/logs",
            json=record(),
            headers={"Authorization": "Bearer " + a["token"]},
        ).status_code
        == 200
    )
    with f() as db:
        assert db.query(AuditLog).filter_by(path="/v1/agents/enrolled").count() == 1


def test_lost_response_retry_quota_and_agent_revocation(fleet):
    c, f, p, _ = fleet
    n = secrets.token_urlsafe(32)
    a = enroll(c, p, n).json()
    assert enroll(c, p, n).json() == a
    assert enroll(c, p, n, name="another").status_code == 409
    assert enroll(c, p).status_code == 201
    assert enroll(c, p).status_code == 403
    c.post("/v1/admin/agents/" + a["agent"]["id"] + "/revoke")
    assert enroll(c, p, n).status_code == 409
    with f() as db:
        assert db.get(AgentEnrollmentProfile, p["profile"]["id"]).enrollment_count == 2


def test_pause_rotate_revoke_and_manual_independence(fleet):
    c, f, p, manual = fleet
    a = enroll(c, p).json()
    header = {"Authorization": "Bearer " + a["token"]}
    assert action(c, p, "pause").status_code == 200
    assert enroll(c, p).status_code == 403
    assert c.post("/v1/otlp/logs", json=record(), headers=header).status_code == 200
    assert action(c, p, "resume").status_code == 200
    updated = action(c, p, "rotate").json()
    assert enroll(c, p).status_code == 401
    assert enroll(c, updated).status_code == 201
    assert c.post("/v1/otlp/logs", json=record(), headers=header).status_code == 200
    assert action(c, p, "revoke").status_code == 200
    assert c.post("/v1/otlp/logs", json=record(), headers=header).status_code == 401
    assert (
        c.post(
            "/v1/agents/renew",
            json={"nonce": secrets.token_urlsafe(32)},
            headers=header,
        ).status_code
        == 401
    )
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


def test_renewal_retry_old_token_scope_and_nonce_binding(fleet):
    c, f, p, _ = fleet
    a = enroll(c, p).json()
    header = {"Authorization": "Bearer " + a["token"]}
    nonce = secrets.token_urlsafe(32)
    r = c.post("/v1/agents/renew", json={"nonce": nonce}, headers=header)
    assert r.status_code == 200, r.text
    assert (
        c.post("/v1/agents/renew", json={"nonce": nonce}, headers=header).json()
        == r.json()
    )
    assert (
        c.post(
            "/v1/agents/renew",
            json={"nonce": secrets.token_urlsafe(32)},
            headers=header,
        ).status_code
        == 401
    )
    assert c.post("/v1/otlp/logs", json=record(), headers=header).status_code == 401
    assert (
        c.post(
            "/v1/otlp/logs",
            json=record(),
            headers={"Authorization": "Bearer " + r.json()["token"]},
        ).status_code
        == 200
    )
    with f() as db:
        row = db.get(KeenAgent, a["agent"]["id"])
        row.renewal_retry_until = utc_now_naive() - timedelta(seconds=1)
        db.commit()
    assert (
        c.post("/v1/agents/renew", json={"nonce": nonce}, headers=header).status_code
        == 401
    )


def test_expiry_network_limits_and_limiter_failure(fleet, monkeypatch):
    c, f, p, _ = fleet
    with f() as db:
        row = db.get(AgentEnrollmentProfile, p["profile"]["id"])
        row.allowed_cidrs = ["192.0.2.0/24"]
        db.commit()
    # Forwarding headers cannot bypass an untrusted peer.
    assert enroll(c, p).status_code == 403
    monkeypatch.setattr(api, "request_ip", lambda request: "192.0.2.5")
    assert enroll(c, p).status_code == 201
    with f() as db:
        row = db.get(AgentEnrollmentProfile, p["profile"]["id"])
        row.expires_at = utc_now_naive() - timedelta(seconds=1)
        db.commit()
    assert enroll(c, p).status_code == 403
    monkeypatch.setattr(api, "fixed_window_allow", lambda *a, **k: (False, 60))
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
    a = enroll(c, p).json()
    with f() as db:
        profile = db.get(AgentEnrollmentProfile, p["profile"]["id"])
        profile.state = "revoked"
        db.commit()
    header = {"Authorization": "Bearer " + a["token"]}
    assert c.post("/v1/otlp/logs", json=record(), headers=header).status_code == 401
    assert c.post("/v1/agents/heartbeat", json={}, headers=header).status_code in {
        401,
        422,
    }
    assert (
        c.post(
            "/v1/agents/renew",
            json={"nonce": secrets.token_urlsafe(32)},
            headers=header,
        ).status_code
        == 401
    )


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
    assert (
        c.post(
            "/v1/agents/enroll",
            json={
                "name": "host",
                "nonce": secrets.token_urlsafe(32),
                "labels": {"admin": "true"},
            },
            headers={"Authorization": "Bearer " + p["bootstrap_key"]},
        ).status_code
        == 422
    )


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
    from starlette.requests import Request
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
                token_hash=digest(token),
                enrollment_profile_id=ident,
                enabled=True,
                health={},
                expires_at=utc_now_naive() + timedelta(days=1),
            )
        )
        db.commit()
    request = Request(
        {
            "type": "http",
            "headers": [(b"authorization", ("Bearer " + token).encode())],
            "client": ("192.0.2.1", 1),
            "state": {},
        }
    )
    with (
        Session(contract_postgres_engine) as first,
        Session(contract_postgres_engine) as second,
    ):
        authenticate(request, first)
        second.execute(text("SET LOCAL lock_timeout='100ms'"))
        with pytest.raises(OperationalError):
            api.manage(ident, "revoke", request, Response(), second)
        second.rollback()
        first.rollback()
        api.manage(ident, "revoke", request, Response(), second)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            authenticate(request, first)
        assert exc.value.status_code == 401
