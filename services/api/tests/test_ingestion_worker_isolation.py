"""Queue routing, manual-run authority and deployment isolation contracts."""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from app.core.config import settings
from app.services.ingestion_pause import POLLING_SOURCES
from app.worker import tasks
from app.worker.celery_app import HTTP_INGEST_SOURCES, celery_app
from pydantic import ValidationError

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


def test_every_ingest_http_task_ends_after_its_collection_budgets():
    # The ingest-http worker runs one task at a time, so a run that never
    # ends would hold back every other collection on it.
    from app.ingest import http
    from app.ingest.riskledger import RiskLedgerError, validate_config

    routed = {
        name
        for name, route in celery_app.conf.task_routes.items()
        if route["queue"] == "ingest-http"
    }
    assert routed >= {
        *(
            f"app.worker.tasks.ingest_{source}_all_task"
            for source in HTTP_INGEST_SOURCES
        ),
        tasks.ingest_source_connection_task.name,
        tasks.integration_run_task.name,
        tasks.cloud_posture_refresh_task.name,
        tasks.cloud_activity_task.name,
    }
    for name in routed:
        task = celery_app.tasks[name]
        assert 0 < task.soft_time_limit < task.time_limit, name
    # The longest per-connection run Risk Ledger accepts, plus one request in
    # flight, finishes before a built-in collection task is stopped.
    validate_config({"max_run_seconds": 1800})
    with pytest.raises(RiskLedgerError):
        validate_config({"max_run_seconds": 1801})
    assert tasks.ingest_riskledger_all_task.soft_time_limit > (
        1800 + http.MAX_RESPONSE_SECONDS
    )
    assert (
        tasks.ingest_source_connection_task.soft_time_limit
        > 1800 + http.MAX_RESPONSE_SECONDS
    )
    # Every other source's run has room for several requests.
    for source in HTTP_INGEST_SOURCES:
        task = getattr(tasks, f"ingest_{source}_all_task")
        assert task.soft_time_limit > 2 * http.MAX_RESPONSE_SECONDS, source


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


def test_compose_refuses_to_start_without_backing_service_passwords():
    root = Path(__file__).resolve().parents[3]
    compose = (root / "docker-compose.yml").read_text()
    references = re.findall(
        r"\$\{(POSTGRES_PASSWORD|KEEN_REDIS_PASSWORD)(\W[^}]*)?\}", compose
    )
    # Every use names no fallback and stops Compose when the value is unset.
    assert {name for name, _ in references} == {
        "POSTGRES_PASSWORD",
        "KEEN_REDIS_PASSWORD",
    }
    assert all(rest.startswith(":?") for _, rest in references), references
    services = yaml.safe_load(compose)["services"]
    assert "${KEEN_REDIS_PASSWORD:?" in " ".join(services["valkey"]["command"])
    # The example settings send cookies over HTTPS only, and keep SameSite=lax
    # so the session cookie survives the return from SSO sign-in.
    example = dict(
        line.split("=", 1)
        for line in (root / ".env.example").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    assert example["KEEN_COOKIE_SECURE"] == "true"
    assert example["KEEN_COOKIE_SAMESITE"] == "lax"
    # The example ships no password, so a copied .env cannot start the stack
    # with a known one; the service URLs take whatever the operator sets.
    assert example["POSTGRES_PASSWORD"] == example["KEEN_REDIS_PASSWORD"] == ""
    assert "${POSTGRES_PASSWORD}@postgres:" in example["KEEN_DATABASE_URL"]
    assert "${KEEN_REDIS_PASSWORD}@valkey:" in example["KEEN_REDIS_URL"]


@pytest.mark.parametrize("configured,expected", [(120, 120), (0, 60), (-5, 1)])
def test_event_counter_refresh_interval_follows_the_cache_ttl(
    monkeypatch, configured, expected
):
    # The beat interval falls back to a minute when caching is off and never
    # drops below one second.
    from app.worker.celery_app import _event_counter_refresh_seconds

    monkeypatch.setattr(settings, "aggregate_cache_ttl_seconds", configured)
    assert _event_counter_refresh_seconds() == expected


@pytest.mark.parametrize("configured", ["many", ""])
def test_a_non_integer_cache_ttl_is_refused_at_start_up(configured):
    # Settings validation rejects the value before any worker schedule reads it.
    with pytest.raises(ValidationError):
        type(settings)(aggregate_cache_ttl_seconds=configured)
