"""A worker task's soft time limit ends the task, whatever its handlers catch.

The worker raises SoftTimeLimitExceeded once, wherever the task is. It is an
ordinary Exception, so a handler that describes one input's failure and carries
on would swallow it. The worker also records the deadline as each task starts,
and every SQL statement, outbound request and email checks it, so a swallowed
raise is raised again at the next of them. The contract is checked against
every task the worker registers.
"""

import inspect
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx2 as httpx
import pytest
from app.core.config import settings
from app.db.session import SessionLocal
from app.ingest import common
from app.ingest import connections as sc
from app.ingest import http as ingest_http
from app.ingest.common import RecordReader
from app.integrations import transport
from app.services import schedules
from app.worker import celery_app as worker
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
from myf import worker as myf_worker
from myf.core import task_deadline
from myf.security.errors import collection_error
from sqlalchemy import event as sa_event
from sqlalchemy import text

from tests.source_tree import SOURCES
from tests.task_limits import checked_statements, ending

APP = SOURCES
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
        assert myf_worker.soft_time_limit(task) == expected
        myf_worker.start_deadline(task=task)
    assert started == [480, 1500, worker.TASK_SECONDS, worker.TASK_SECONDS]
    with passed_deadline():
        myf_worker.finish_deadline()
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


def _arguments(task) -> list:
    """Placeholder arguments for a task: identifiers name nothing stored."""
    values = []
    for name, parameter in inspect.signature(task.run).parameters.items():
        if parameter.default is not inspect.Parameter.empty:
            continue
        if name == "source":
            values.append("loki")
        elif name.endswith("_ids"):
            values.append([str(uuid.uuid4())])
        else:
            values.append(str(uuid.uuid4()))
    return values


def at_passed_deadline(task) -> str:
    """How ``task`` ends when the first thing it asks for finds the deadline passed."""
    return ending(lambda: task.run(*_arguments(task)), 1)


def keen_tasks():
    import app.worker.tasks  # noqa: F401 - registers the worker tasks
    from app.worker.celery_app import celery_app

    return sorted(
        (name, task)
        for name, task in celery_app.tasks.items()
        if not name.startswith("celery.")
    )


@pytest.fixture
def worker_database(postgres_engine):
    """The worker's sessions on a migrated schema, checked as the worker's are."""
    bound = SessionLocal.kw["bind"]
    SessionLocal.configure(bind=postgres_engine)
    try:
        with checked_statements(postgres_engine):
            yield postgres_engine
    finally:
        SessionLocal.configure(bind=bound)


def test_the_application_engine_checks_the_deadline_before_each_statement():
    from app.db import session
    from myf.db.engine import within_task_deadline

    assert sa_event.contains(
        session.engine, "before_cursor_execute", within_task_deadline
    )


def test_a_statement_past_the_deadline_is_refused_except_in_grace(worker_database):
    with SessionLocal() as db:
        assert db.execute(text("SELECT 1")).scalar() == 1
        db.rollback()
        with passed_deadline():
            with pytest.raises(SoftTimeLimitExceeded):
                db.execute(text("SELECT 1"))
            db.rollback()
            with task_deadline.grace():
                assert db.execute(text("SELECT 2")).scalar() == 2
            db.rollback()
            with pytest.raises(SoftTimeLimitExceeded):
                db.execute(text("SELECT 3"))


def test_every_worker_task_stops_at_a_passed_deadline(worker_database, monkeypatch):
    """Derived from the tasks the worker registers, not from a list: each one,
    started past its deadline, raises the time limit or finishes having asked
    for nothing at all.
    One that catches the limit at its first statement and returns normally,
    recording a failure or carrying on, is reported."""
    monkeypatch.setattr(settings, "smtp_host", "smtp.example")
    monkeypatch.setattr(settings, "smtp_from_email", "keen@example.test")
    outcomes = {name: at_passed_deadline(task) for name, task in keen_tasks()}
    assert len(outcomes) >= 40
    assert [
        name
        for name, outcome in outcomes.items()
        if outcome in ("swallowed", "continued")
    ] == []
    # Every scheduled collection, and the work that reads or writes evidence,
    # reaches the database and so stops there.
    for name in (
        "app.worker.tasks.ingest_loki_all_task",
        "app.worker.tasks.evidence_retention_task",
        "app.worker.tasks.integration_run_task",
        "app.worker.tasks.backfill_rule_task",
        "app.worker.tasks.security_notifications_task",
        "keen.ingest_source_connection",
    ):
        assert outcomes[name] == "limit", name


def test_the_task_check_reports_a_task_that_swallows_the_limit(worker_database):
    from app.worker.celery_app import celery_app

    @celery_app.task(name="tests.swallowing_task")
    def swallowing_task():
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 - the swallowing shape under test
            return {"error": "Database failed"}

    @celery_app.task(name="tests.continuing_task")
    def continuing_task():
        with SessionLocal() as db:
            try:
                db.execute(text("SELECT 1"))
            except Exception:  # noqa: BLE001 - the swallowing shape under test
                db.rollback()
            db.execute(text("SELECT 2"))

    @celery_app.task(name="tests.idle_task")
    def idle_task():
        return {}

    try:
        assert at_passed_deadline(swallowing_task) == "swallowed"
        assert at_passed_deadline(continuing_task) == "continued"
        assert at_passed_deadline(idle_task) == "finished"
    finally:
        for name in ("swallowing_task", "continuing_task", "idle_task"):
            celery_app.tasks.pop(f"tests.{name}")


def test_deliver_once_is_reachable_through_schedules():
    assert schedules.deliver_once.__module__ == "app.services.schedules"
