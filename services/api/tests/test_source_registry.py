"""Each source type declares itself once, and everything else reads that."""

from importlib import import_module
from pathlib import Path

import pytest
from app.core.config import Settings, settings
from app.ingest import registry
from app.ingest.registry import HTTP_QUEUE, IMAP_QUEUE, Source
from app.services.ingestion_pause import pausable_sources
from app.worker import tasks
from app.worker.celery_app import celery_app

INGEST = Path(registry.__file__).parent


def test_every_collector_module_declares_its_source():
    # A module with an ingest_<name>_all entry point is a source, and says so.
    collectors = {
        path.stem
        for path in INGEST.glob("*.py")
        if f"def ingest_{path.stem}_all(" in path.read_text()
    }
    assert collectors == {source.name for source in registry.collected()}
    for source in registry.sources():
        assert source.module == f"app.ingest.{source.name}"
        assert import_module(source.module)


def test_a_declared_source_has_the_settings_its_kind_reads():
    fields = set(Settings.model_fields)
    for source in registry.collected():
        assert f"{source.name}_enabled" in fields, source.name
    for source in registry.polled():
        assert f"{source.name}_run_seconds" in fields, source.name
        assert source.schedule is not None, source.name
        assert source.config_setting, source.name
        # New items go to a section the collector reads.
        assert source.section in source.input_sections, source.name
    for source in registry.configured():
        assert source.config_setting in fields, source.name
        assert source.config_path == getattr(settings, source.config_setting)


def test_the_worker_routes_limits_and_schedules_every_polled_source():
    routes = celery_app.conf.task_routes
    limits = celery_app.conf.task_annotations
    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    for source in registry.polled():
        assert routes[source.task_name] == {"queue": HTTP_QUEUE}
        assert limits[source.task_name]["soft_time_limit"] <= source.run_seconds
        assert source.task_name in scheduled
        assert tasks.COLLECTION_TASKS[source.name].name == source.task_name
    assert set(tasks.COLLECTION_TASKS) == {s.name for s in registry.polled()}


def test_kinds_of_source_are_told_apart():
    imap, webhooks, loki = (registry.get(n) for n in ("imap", "webhooks", "loki"))
    assert (imap.queue, imap.collected, imap.polled) == (IMAP_QUEUE, True, False)
    assert (webhooks.collected, webhooks.polled) == (False, False)
    assert (loki.collected, loki.polled) == (True, True)
    assert webhooks in registry.configured() and imap not in registry.configured()
    # Every declared source can be paused, with the cloud providers and the
    # two that are not source modules.
    assert {s.name for s in registry.sources()} < pausable_sources()
    assert {"keen-agent", "api-ingesters", "aws"} <= pausable_sources()


def test_a_source_is_enabled_by_its_setting_and_withheld_from_a_demo(monkeypatch):
    loki, redmine = registry.get("loki"), registry.get("redmine")
    assert loki.demo and not redmine.demo
    for source in (loki, redmine):
        monkeypatch.setattr(settings, f"{source.name}_enabled", False)
        assert source.enabled is False
        monkeypatch.setattr(settings, f"{source.name}_enabled", True)
        assert source.enabled is True
    monkeypatch.setattr(settings, "demo_mode", True)
    assert loki.enabled is True and redmine.enabled is False


def test_names_are_looked_up_and_declared_once():
    assert registry.find("nagios") is None
    with pytest.raises(KeyError):
        registry.get("nagios")
    loki = registry.get("loki")
    # Importing a module again declares the same source again.
    assert registry.register(loki) is loki
    with pytest.raises(ValueError, match="declared twice"):
        registry.register(Source(name="loki", module="elsewhere"))
    assert registry.get("loki") is loki


def test_a_collector_is_found_by_name_when_it_runs(monkeypatch):
    seen = []
    monkeypatch.setattr(
        import_module("app.ingest.rss"),
        "ingest_rss_all",
        lambda db, **options: seen.append((db, options)) or ["done"],
    )
    assert registry.get("rss").collect("db", connection_id="c1") == ["done"]
    assert seen == [("db", {"connection_id": "c1"})]
