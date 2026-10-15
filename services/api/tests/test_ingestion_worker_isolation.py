"""Queue routing, manual-run authority and deployment isolation contracts."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from app.core.config import settings
from app.services.ingestion_pause import POLLING_SOURCES
from app.worker import tasks
from app.worker.celery_app import HTTP_INGEST_SOURCES, celery_app

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_assembled_authority_regressions import unsafe


def queue(task, args=()):
    return celery_app.amqp.router.route({}, task, args=args)["queue"].name


def test_every_polling_entry_point_routes_to_its_worker():
    assert set(HTTP_INGEST_SOURCES) == POLLING_SOURCES - {"imap"}
    for source in HTTP_INGEST_SOURCES:
        task = getattr(tasks, f"ingest_{source}_all_task")
        assert queue(task.name) == "ingest-http"
    assert (
        queue(tasks.ingest_source_connection_task.name, ("rss", "connection"))
        == "ingest-http"
    )
    assert queue(tasks.integration_run_task.name, ("run",)) == "ingest-http"
    assert queue(tasks.ingest_imap_connection_task.name, ("connection",)) == "imap"
    for task in (
        tasks.refresh_event_counter_cache_task,
        tasks.security_notifications_task,
        tasks.integration_tick_task,
        tasks.dispatch_imap_task,
        tasks.backfill_rule_task,
        tasks.evidence_retention_task,
    ):
        assert queue(task.name) == "celery"


@pytest.mark.parametrize("source", sorted(set(HTTP_INGEST_SOURCES) - {"elasticsearch"}))
def test_admin_runner_queues_without_collecting(keen, monkeypatch, source):
    monkeypatch.setattr(settings, source + "_enabled", True)
    dispatch = Mock(return_value=SimpleNamespace(id="queued-job"))
    monkeypatch.setattr(
        getattr(tasks, f"ingest_{source}_all_task"), "apply_async", dispatch
    )
    # Invoking the task on this request path must fail the test.
    monkeypatch.setattr(
        tasks,
        f"ingest_{source}_all",
        Mock(side_effect=AssertionError("inline collection")),
    )
    client = keen.client(keen.user(role="admin"))
    response = client.post(
        f"/v1/admin/ingest/{source}/run", json={}, headers=unsafe(client)
    )
    assert response.status_code == 202, response.text
    assert response.json()["queued"]
    dispatch.assert_called_once_with(expires=300)


@pytest.mark.parametrize("role", [None, "normal"])
def test_runner_denies_unprivileged_callers(keen, monkeypatch, role):
    dispatch = Mock()
    monkeypatch.setattr(tasks.ingest_rss_all_task, "apply_async", dispatch)
    client = keen.client(keen.user(role=role) if role else None)
    response = client.post(
        "/v1/admin/ingest/rss/run", json={}, headers=unsafe(client) if role else {}
    )
    assert response.status_code in (401, 403)
    dispatch.assert_not_called()


@pytest.mark.parametrize("state", ["csrf", "disabled", "paused"])
def test_runner_rejects_without_enqueuing(keen, monkeypatch, state):
    from app.db.models import SourceIngestionState

    monkeypatch.setattr(settings, "rss_enabled", state != "disabled")
    if state == "paused":
        keen.db.add(SourceIngestionState(source="rss", paused=True))
        keen.db.commit()
    dispatch = Mock()
    monkeypatch.setattr(tasks.ingest_rss_all_task, "apply_async", dispatch)
    client = keen.client(keen.user(role="admin"))
    response = client.post(
        "/v1/admin/ingest/rss/run",
        json={},
        headers={} if state == "csrf" else unsafe(client),
    )
    assert response.status_code == (403 if state == "csrf" else 409)
    dispatch.assert_not_called()


def test_compose_workers_and_python_capability_policy():
    root = Path(__file__).resolve().parents[3]
    services = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]
    for name in (
        "keen-api",
        "keen-worker",
        "keen-http-worker",
        "keen-imap-worker",
        "keen-beat",
    ):
        assert services[name]["cap_drop"] == ["ALL"]
        assert services[name]["security_opt"] == ["no-new-privileges:true"]
        assert not services[name].get("cap_add")
    for name, expected in (
        ("keen-worker", "celery"),
        ("keen-http-worker", "ingest-http"),
        ("keen-imap-worker", "imap"),
    ):
        command = " ".join(services[name]["command"])
        assert f"-Q {expected} " in command
        assert "--concurrency=1" in command
    http = services["keen-http-worker"]
    assert http["mem_limit"] == "768m" and http["cpus"] == 1.0
    assert not http.get("ports") and not http.get("profiles")
    builds = yaml.safe_load((root / "docker-compose.build.yml").read_text())["services"]
    assert builds["keen-http-worker"]["build"] == builds["keen-api"]["build"]
