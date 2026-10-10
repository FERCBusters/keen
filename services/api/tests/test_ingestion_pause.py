"""Pause gates preserve configuration and reject intake before collection."""

import importlib
from unittest.mock import Mock

# Imported fixtures are discovered by pytest by name.
from tests.test_integration_builder import database as database
from tests.test_keen_agent import setup as agent_setup  # noqa: F401 - pytest fixture

import pytest
from app.api.routes import managed_configurations as routes
from app.api.routes import webhooks
from app.db.models import SourceIngestionState, User
from app.db.session import get_db
from app.services.ingestion_pause import POLLING_SOURCES, is_paused, pausable
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.test_keen_agent import record


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SourceIngestionState.__table__.create(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def pause(db, source, value=True):
    row = db.get(SourceIngestionState, source)
    if row:
        row.paused = value
    else:
        db.add(SourceIngestionState(source=source, paused=value))
    db.commit()


@pytest.mark.parametrize("source", sorted(POLLING_SOURCES))
def test_every_poller_checks_persistent_pause_before_collection(db, source):
    pause(db, source)
    function = getattr(
        importlib.import_module("app.ingest." + source), "ingest_" + source + "_all"
    )
    assert function(db)[0]["paused"] is True


def test_resume_allows_next_call_without_resetting_state(db):
    collect = Mock(return_value=["collected"])
    run = pausable("rss")(collect)
    assert run(db) == ["collected"]
    pause(db, "rss")
    assert run(db)[0]["skipped"]
    assert collect.call_count == 1
    pause(db, "rss", False)
    assert run(db) == ["collected"]
    assert collect.call_count == 2
    assert not is_paused(db, "github")


def test_state_endpoint_requires_admin_and_persists_upsert(db):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: db
    identity = {"role": None}

    @app.middleware("http")
    async def user(request: Request, call_next):
        if identity["role"]:
            request.state.user = User(username="operator", role=identity["role"])
        return await call_next(request)

    with TestClient(app) as client:
        url = "/v1/admin/sources/rss/ingestion-state"
        assert client.put(url, json={"paused": True}).status_code == 401
        identity["role"] = "normal"
        assert client.put(url, json={"paused": True}).status_code == 403
        identity["role"] = "admin"
        assert client.put(url, json={"paused": True}).status_code == 200
        assert is_paused(db, "rss")
        assert db.get(SourceIngestionState, "rss").updated_by == "operator"
        assert client.put(url, json={"paused": False}).status_code == 200
        db.expire_all()
        assert not is_paused(db, "rss")
        assert db.query(SourceIngestionState).count() == 1
        assert (
            client.put(
                "/v1/admin/sources/no-such-source/ingestion-state",
                json={"paused": True},
            ).status_code
            == 404
        )


def test_agents_retry_without_losing_records_or_revoking_credentials(agent_setup):
    client, factory, headers, info, stored, _ = agent_setup
    with factory() as db:
        pause(db, "keen-agent")
    response = client.post("/v1/otlp/logs", json=record(), headers=headers)
    assert response.status_code == 503 and response.headers["Retry-After"] == "60"
    assert stored == []
    with factory() as db:
        pause(db, "keen-agent", False)
    assert (
        client.post("/v1/otlp/logs", json=record(), headers=headers).status_code == 200
    )
    assert len(stored) == 1


@pytest.mark.parametrize(
    "path",
    ["/v1/webhooks/example/event", "/v1/webhooks/isms/effectiveness-metrics/test"],
)
def test_webhooks_retry_before_ingest(db, monkeypatch, path):
    app = FastAPI()
    app.include_router(webhooks.router)
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(webhooks, "verify_secret", lambda *a: True)
    monkeypatch.setattr(webhooks, "get_valkey", lambda: None)
    monkeypatch.setattr(webhooks, "fixed_window_allow", lambda *a, **kw: (True, 0))
    ingest = Mock()
    monkeypatch.setattr(webhooks, "ingest_webhook", ingest)
    pause(db, "webhooks")
    with TestClient(app) as client:
        response = client.post(path, json={})
    assert response.status_code == 503 and response.headers["Retry-After"] == "60"
    ingest.assert_not_called()


def test_custom_api_queue_and_already_queued_run_are_paused(database, monkeypatch):
    from app.db.models import IntegrationCollector as Collector
    from app.db.models import IntegrationRun as Run
    from app.integrations import runtime

    db, factory = database
    collector = db.get(Collector, "collector")
    collector.cursor = "keep-me"
    db.commit()
    run = runtime.queue_run(db, collector, preview=True)
    db.commit()
    run_id = run.id
    pause(db, "api-ingesters")
    with pytest.raises(ValueError, match="paused"):
        runtime.queue_run(db, collector, preview=True)
    fetch = Mock()
    monkeypatch.setattr(runtime, "collect", fetch)
    runtime.run_job(run_id)
    db.expire_all()
    assert db.get(Run, run_id).status == "cancelled"
    assert db.get(Collector, "collector").cursor == "keep-me"
    assert db.get(Collector, "collector").failures == 0
    fetch.assert_not_called()
    pause(db, "api-ingesters", False)
    assert runtime.queue_run(db, collector, preview=True).status == "queued"


def test_custom_tick_does_not_advance_schedule_or_failure_budget(database):
    from datetime import datetime

    from app.db.models import IntegrationCollector as Collector
    from app.db.models import IntegrationRun as Run
    from app.integrations import runtime

    db, factory = database
    collector = db.get(Collector, "collector")
    collector.enabled = True
    collector.next_run = datetime(2025, 1, 1)
    db.commit()
    run = runtime.queue_run(db, collector, preview=True)
    db.commit()
    run_id = run.id
    pause(db, "api-ingesters")
    runtime.tick()
    db.expire_all()
    assert db.get(Run, run_id).status == "cancelled"
    assert collector.next_run == datetime(2025, 1, 1)
    assert collector.enabled and collector.failures == 0


def test_bookstack_snapshot_obeys_source_pause(db, monkeypatch):
    from app.api.routes import bookstack_sections as sections
    from fastapi import HTTPException

    monkeypatch.setattr(sections.settings, "bookstack_enabled", True)
    fetch = Mock()
    monkeypatch.setattr(sections, "_client", fetch)
    pause(db, "bookstack")
    with pytest.raises(HTTPException) as error:
        sections.capture_section(None, User(username="operator"), db)
    assert error.value.status_code == 503
    fetch.assert_not_called()
