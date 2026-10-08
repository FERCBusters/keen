"""OTLP contract, scoped credentials, atomic acceptance and redaction regressions."""
from app.core.datetime_utils import utc_now_naive
from tests.db_helpers import create_sqlite_schema

import copy
import gzip
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event as sql_event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from app.api.routes import agents as api
from app.agents.otlp import decode
from app.agents.schema import fingerprint
from app.db.models import Base, KeenAgent, Event, Artifact
from app.ingest import common


@compiles(JSONB, "sqlite")
def json_type(*a, **k):
    return "JSON"


def record(identity=None, body="package upgraded"):
    log = {
        "timeUnixNano": str(int(datetime.now(timezone.utc).timestamp()) * 10**9),
        "body": {"stringValue": body},
        "attributes": [
            {
                "key": "keen.event.id",
                "value": {"stringValue": identity or str(uuid.uuid4())},
            },
            {"key": "keen.action", "value": {"stringValue": "package.upgrade"}},
        ],
    }
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "host.name", "value": {"stringValue": "untrusted-name"}}
                    ]
                },
                "scopeLogs": [{"scope": {"name": "test"}, "logRecords": [log]}],
            }
        ]
    }


def logs(doc):
    return doc["resourceLogs"][0]["scopeLogs"][0]["logRecords"]


@pytest.fixture
def setup(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @sql_event.listens_for(engine, "connect")
    def connected(c, r):
        c.isolation_level = None
        c.create_function("NOW", 0, lambda: utc_now_naive().isoformat(" "))

    @sql_event.listens_for(engine, "begin")
    def begin(conn):
        conn.exec_driver_sql("BEGIN")

    create_sqlite_schema(engine)
    factory = sessionmaker(bind=engine, autoflush=False)

    def db():
        with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_db] = db
    app.dependency_overrides[api.require_admin] = lambda: True
    monkeypatch.setattr(api.settings, "demo_mode", False)
    monkeypatch.setattr(api, "get_valkey", lambda: None)
    monkeypatch.setattr(api, "fixed_window_allow", lambda *a, **k: (True, 0))
    stored = []

    def put(**k):
        stored.append(k["data"])
        return SimpleNamespace(
            uri="test://artifact", sha256="0" * 64, size_bytes=len(k["data"])
        )

    monkeypatch.setattr(common, "put_bytes", put)
    monkeypatch.setattr(common, "apply_rules", lambda *a: 0)
    with TestClient(app, raise_server_exceptions=False) as client:
        registration = client.post("/v1/admin/agents", json={"name": "trusted-server"})
        assert registration.status_code == 201, registration.text
        info = registration.json()
        headers = {"Authorization": "Bearer " + info["token"]}
        yield client, factory, headers, info, stored, app
    engine.dispose()


def test_full_success_dedup_identity_and_artifact(setup):
    client, factory, headers, info, stored, _ = setup
    doc = record(body="password=do-not-store")
    for _ in range(2):
        r = client.post("/v1/otlp/logs", json=doc, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json() == {}
    with factory() as db:
        row = db.query(Event).one()
        assert row.system == "trusted-server" and row.source == "keen-agent"
        assert db.query(Artifact).count() == 1
        assert (
            row.normalized_payload["fields"]["resource.host.name"] == "untrusted-name"
        )
        assert "do-not-store" not in row.summary
        assert db.get(KeenAgent, info["agent"]["id"]).last_seen
    assert len(stored) == 1 and b"do-not-store" not in stored[0]


def test_uuid_conflict_rolls_back_whole_batch(setup):
    c, f, h, _, _, _ = setup
    existing = record()
    assert c.post("/v1/otlp/logs", json=existing, headers=h).status_code == 200
    new = record()
    changed = copy.deepcopy(logs(existing)[0])
    changed["body"] = {"stringValue": "changed"}
    logs(new).append(changed)
    assert c.post("/v1/otlp/logs", json=new, headers=h).status_code == 409
    with f() as db:
        assert db.query(Event).count() == 1


def test_storage_failure_rolls_back_and_retry(setup, monkeypatch):
    c, f, h, _, _, _ = setup
    doc = record()
    logs(doc).append(logs(record())[0])
    original = common.put_bytes
    count = 0

    def fail(**kw):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("storage unavailable")
        return original(**kw)

    monkeypatch.setattr(common, "put_bytes", fail)
    assert c.post("/v1/otlp/logs", json=doc, headers=h).status_code == 500
    with f() as db:
        assert db.query(Event).count() == 0 and db.query(Artifact).count() == 0
    monkeypatch.setattr(common, "put_bytes", original)
    assert c.post("/v1/otlp/logs", json=doc, headers=h).status_code == 200
    with f() as db:
        assert db.query(Event).count() == 2


def test_rotation_revocation_and_no_token_disclosure(setup):
    c, f, h, info, _, _ = setup
    identity = info["agent"]["id"]
    token = info["token"]
    assert token not in c.get("/v1/admin/agents").text
    with f() as db:
        assert token not in db.get(KeenAgent, identity).token_hash
    replacement = c.post("/v1/admin/agents/" + identity + "/rotate", json={}).json()[
        "token"
    ]
    assert c.post("/v1/otlp/logs", json=record(), headers=h).status_code == 401
    fresh = {"Authorization": "Bearer " + replacement}
    assert c.post("/v1/otlp/logs", json=record(), headers=fresh).status_code == 200
    assert c.post("/v1/admin/agents/" + identity + "/revoke").status_code == 200
    assert c.post("/v1/otlp/logs", json=record(), headers=fresh).status_code == 401
    assert (
        c.post("/v1/admin/agents/" + identity + "/rotate", json={}).status_code == 409
    )


def test_expiry_demo_admin_boundary_and_health(setup, monkeypatch):
    c, f, h, info, _, app = setup
    assert (
        c.post(
            "/v1/agents/heartbeat",
            json={"version": "0.1", "queued": 5, "sources": {"syslog": "healthy"}},
            headers=h,
        ).status_code
        == 200
    )
    assert c.get("/v1/admin/agents").json()["agents"][0]["health"]["queued"] == 5
    app.dependency_overrides.pop(api.require_admin)
    assert (
        c.post("/v1/admin/agents", json={"name": "forbidden"}, headers=h).status_code
        == 401
    )
    with f() as db:
        a = db.get(KeenAgent, info["agent"]["id"])
        a.expires_at = utc_now_naive() - timedelta(seconds=1)
        db.commit()
    assert c.post("/v1/otlp/logs", json=record(), headers=h).status_code == 401
    monkeypatch.setattr(api.settings, "demo_mode", True)
    assert c.post("/v1/otlp/logs", json=record(), headers=h).status_code == 403
    assert c.post("/v1/agents/heartbeat", json={}, headers=h).status_code == 403


def test_limits_compression_format_and_rate(setup, monkeypatch):
    c, f, h, _, _, _ = setup
    assert c.post("/v1/otlp/logs", json=record()).status_code == 401
    assert (
        c.post(
            "/v1/otlp/logs",
            content=b"x",
            headers={**h, "Content-Type": "application/x-protobuf"},
        ).status_code
        == 415
    )
    assert (
        c.post(
            "/v1/otlp/logs",
            content=gzip.compress(json.dumps(record()).encode()),
            headers={
                **h,
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
            },
        ).status_code
        == 200
    )
    for data in (b"x" * 1048577, gzip.compress(b"x" * 1048577)):
        headers = {**h, "Content-Type": "application/json"}
        if len(data) < 1048577:
            headers["Content-Encoding"] = "gzip"
        assert c.post("/v1/otlp/logs", content=data, headers=headers).status_code == 413
    doc = record()
    logs(doc).extend([copy.deepcopy(logs(doc)[0]) for _ in range(64)])
    assert c.post("/v1/otlp/logs", json=doc, headers=h).status_code == 400
    monkeypatch.setattr(api, "fixed_window_allow", lambda *a, **k: (False, 20))
    r = c.post("/v1/otlp/logs", json=record(), headers=h)
    assert r.status_code == 429 and r.headers["Retry-After"] == "20"


def test_generic_otlp_canonical_identity_and_scoped_dedup():
    doc = record()
    logs(doc)[0]["attributes"] = [
        {"key": "a", "value": {"intValue": "42"}},
        {"key": "b", "value": {"boolValue": True}},
    ]
    one = decode(json.dumps(doc), "agent-1")[0]
    logs(doc)[0]["attributes"].reverse()
    two = decode(json.dumps(doc), "agent-1")[0]
    assert one.id == two.id and fingerprint(one) == fingerprint(two)
    assert one.id != decode(json.dumps(doc), "agent-2")[0].id


@pytest.mark.parametrize("body", [b"[]", b"{", b'{"resourceLogs":[null]}'])
def test_malformed_otlp_rejected(setup, body):
    c, _, h, _, _, _ = setup
    assert (
        c.post(
            "/v1/otlp/logs",
            content=body,
            headers={**h, "Content-Type": "application/json"},
        ).status_code
        == 400
    )


def test_plain_mapping_reasons_explain_exact_values():
    from app.api.routes.managed_configurations import _plain_mismatch_reasons

    event = SimpleNamespace(
        system="prod", actor=None, action="build", outcome="failed", severity=4
    )
    reasons = _plain_mismatch_reasons(
        event, {"action": "Build", "outcome": "failure", "severity": 5}
    )
    assert len(reasons) == 3
    assert "Build" in reasons[0] and "build" in reasons[0]
    assert "failure" in reasons[1] and "failed" in reasons[1]


def test_go_agent_wire_contract():
    from pathlib import Path

    data = (
        Path(__file__).resolve().parent / "fixtures/keen-agent-otlp.json"
    ).read_bytes()
    event = decode(data, "agent-1")[0]
    assert str(event.id) == "6f8459a2-2768-41a4-94d5-a0cdcc2d7187"
    assert event.action == "package.upgrade" and event.outcome == "info"
    assert (
        event.fields["package"] == "example:amd64"
        and event.fields["agent.version"] == "0.1.0"
    )
    assert event.severity == 3


def test_webhook_artifact_metadata_excludes_all_credential_headers(monkeypatch):
    from app.ingest import webhooks

    monkeypatch.setattr(
        webhooks,
        "load_webhook_policies",
        lambda *a: {"providers": {"test": {"secret_header": "User-Agent"}}},
    )
    monkeypatch.setattr(webhooks, "_check_replay_protection", lambda *a: False)
    captured = {}

    def store(db, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(webhooks, "store_event_with_artifact", store)
    webhooks.ingest_webhook(
        None,
        "test",
        "record",
        b"{}",
        {
            "Authorization": "secret",
            "X-Custom-Key": "secret",
            "User-Agent": "secret",
            "content-type": "application/json",
        },
    )
    assert captured["raw_pointer"]["webhook"]["headers"] == {
        "content-type": "application/json"
    }


def test_real_middleware_keeps_agent_paths_scoped(setup, monkeypatch):
    from app import main

    _, factory, headers, _, _, _ = setup

    def db():
        with factory() as session:
            yield session

    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(main, "get_current_user_from_request", lambda *a: None)
    main.app.dependency_overrides[api.get_db] = db
    try:
        client = TestClient(main.app, raise_server_exceptions=False)
        assert (
            client.post("/v1/otlp/logs", json=record(), headers=headers).status_code
            == 200
        )
        assert (
            client.post("/v1/agents/heartbeat", json={}, headers=headers).status_code
            == 200
        )
        assert client.post("/v1/otlp/logs", json=record()).status_code == 401
        assert client.get("/v1/admin/agents", headers=headers).status_code == 401
        assert client.get("/v1/events", headers=headers).status_code == 401
    finally:
        main.app.dependency_overrides.pop(api.get_db, None)
