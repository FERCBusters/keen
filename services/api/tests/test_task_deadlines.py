"""A worker task's soft time limit ends the task, whatever its handlers catch.

The worker raises SoftTimeLimitExceeded once, wherever the task is. It is an
ordinary Exception, so a handler that describes one input's failure and carries
on would swallow it. The worker also records the deadline as each task starts,
and the shared work loops check it before each request, record, connection and
delivery, so a swallowed raise is raised again at the next of them.
"""

import ast
import inspect
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx2 as httpx
import pytest
from app.core import task_deadline
from app.ingest import common
from app.ingest import connections as sc
from app.ingest import http as ingest_http
from app.ingest.common import RecordReader
from app.integrations import transport
from app.security.errors import collection_error
from app.services import schedules
from app.worker import celery_app as worker
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded

APP = Path(__file__).resolve().parents[1] / "app"
STORE_ARGUMENTS = [
    name
    for name, parameter in inspect.signature(
        common.store_event_with_artifact
    ).parameters.items()
    if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    and parameter.default is inspect.Parameter.empty
]


@contextmanager
def passed_deadline():
    """A task whose deadline has passed, as after a swallowed soft limit."""
    task_deadline.start(-1)
    try:
        yield
    finally:
        task_deadline.finish()


def test_without_a_task_or_before_its_deadline_a_check_does_nothing():
    task_deadline.check()
    task_deadline.start(None)
    task_deadline.check()
    task_deadline.start(3600)
    try:
        task_deadline.check()
    finally:
        task_deadline.finish()
    with passed_deadline():
        with pytest.raises(SoftTimeLimitExceeded):
            task_deadline.check()
    task_deadline.check()


def test_the_worker_records_each_tasks_soft_limit_as_it_starts(monkeypatch):
    started = []
    monkeypatch.setattr(task_deadline, "start", started.append)
    for limits, annotated, expected in (
        ((600, 480), None, 480),
        (None, 1500, 1500),
        (None, None, worker.TASK_SECONDS),
        ((None, None), None, worker.TASK_SECONDS),
    ):
        task = SimpleNamespace(
            request=SimpleNamespace(timelimit=limits), soft_time_limit=annotated
        )
        assert worker.soft_time_limit(task) == expected
        worker._start_deadline(task=task)
    assert started == [480, 1500, worker.TASK_SECONDS, worker.TASK_SECONDS]
    with passed_deadline():
        worker._finish_deadline()
        task_deadline.check()


def test_every_shared_work_loop_stops_once_the_deadline_has_passed(monkeypatch):
    with passed_deadline():
        # Each outbound request through the built-in and custom transports.
        with pytest.raises(SoftTimeLimitExceeded):
            ingest_http.IngestionTransport().handle_request(
                httpx.Request("GET", "https://upstream.example/")
            )
        with pytest.raises(SoftTimeLimitExceeded):
            transport.request("https://upstream.example/")
        # Each record read, and each record stored.
        with pytest.raises(SoftTimeLimitExceeded):
            list(RecordReader().each([1, 2], lambda record: record))
        with pytest.raises(SoftTimeLimitExceeded):
            common.store_event_with_artifact(
                MagicMock(), **dict.fromkeys(STORE_ARGUMENTS)
            )
        # Each failure a collector describes.
        with pytest.raises(SoftTimeLimitExceeded):
            collection_error(RuntimeError("one input"))


def test_a_collector_that_swallows_the_limit_still_ends_its_run(monkeypatch):
    """The run's own handler swallows the worker's raise in the first
    connection's first input. Its second input and the second connection never
    start: the record reader and the fan-out check the deadline."""
    monkeypatch.setattr(sc.deployment_settings, "loki_enabled", True)
    monkeypatch.setattr(sc, "get_valkey", lambda: None)
    monkeypatch.setattr(
        sc,
        "connections",
        lambda *a: [
            sc.Connection(name, "loki", name, {}, {}, {}) for name in ("a", "b")
        ],
    )
    monkeypatch.setattr(sc, "resolve", lambda connection: connection)
    monkeypatch.setattr(sc, "run_lock", lambda db, key: _acquired())
    worked = []

    @sc.connection_runs("loki")
    def run(db):
        results = []
        for item in RecordReader().each(["first", "second"], str):
            worked.append((sc.identity()["connection_id"], item))
            try:
                # The worker's one raise, landing inside this input.
                task_deadline.start(-1)
                raise SoftTimeLimitExceeded()
            except Exception:  # noqa: BLE001 - the swallowing handler under test
                results.append({"error": "described and skipped"})
        return results

    try:
        with pytest.raises(SoftTimeLimitExceeded):
            run(MagicMock())
    finally:
        task_deadline.finish()
    assert worked == [("a", "first")]


@contextmanager
def _acquired():
    yield True


@pytest.mark.parametrize("limit", (SoftTimeLimitExceeded, TimeLimitExceeded))
def test_collection_error_raises_the_task_limit_itself(limit):
    with pytest.raises(limit):
        collection_error(limit())


def _calls(function: ast.FunctionDef) -> set[str]:
    return {
        ast.unparse(node.func)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
    }


# Each loop that does a task's repeated work, and the call that makes it stop.
CHECKPOINTS = {
    ("ingest/http.py", "handle_request"): "task_deadline.check",
    ("integrations/transport.py", "request"): "task_deadline.check",
    ("ingest/cloud/google.py", "request"): "task_deadline.check",
    ("ingest/common.py", "each"): "task_deadline.check",
    ("ingest/common.py", "store_event_with_artifact"): "task_deadline.check",
    ("ingest/connections.py", "run"): "task_deadline.check",
    ("ingest/cloud/runner.py", "refresh_all"): "task_deadline.check",
    ("ingest/cloud/runner.py", "activity_all"): "task_deadline.check",
    ("services/schedules.py", "deliver_once"): "task_deadline.check",
    ("security/errors.py", "collection_error"): "task_deadline.check",
}


def checkpoints_missing(root: Path) -> list[str]:
    missing = []
    for (path, name), call in CHECKPOINTS.items():
        tree = ast.parse((root / path).read_text())
        functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]
        if not functions or not any(call in _calls(f) for f in functions):
            missing.append(f"{path}:{name}")
    return missing


def test_every_shared_work_loop_checks_the_task_deadline():
    assert checkpoints_missing(APP) == []


def test_the_checkpoint_check_finds_a_loop_without_one(tmp_path):
    for path, _ in CHECKPOINTS:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((APP / path).read_text())
    store = tmp_path / "services/schedules.py"
    store.write_text(store.read_text().replace("task_deadline.check()", "pass"))
    assert checkpoints_missing(tmp_path) == ["services/schedules.py:deliver_once"]


def test_deliver_once_is_reachable_through_schedules():
    assert schedules.deliver_once.__module__ == "app.services.schedules"
