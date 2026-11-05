"""KEEN_SQL_TRACE read when the database and worker modules load.

Each module's source runs afresh, as a new process imports it, with the
setting on and off. The fresh copies are discarded afterwards: the listeners
they register on the shared Session class are removed, the worker's signal
handlers disconnected and the running Celery application restored.
"""

import inspect
import runpy

from app.core.config import settings
from app.db import session as live_session
from app.db import sql_trace as db_trace
from app.worker import celery_app as live_worker
from app.worker import sql_trace as worker_trace
from celery.signals import task_postrun, task_prerun
from sqlalchemy import event
from sqlalchemy.orm import Session, SessionEvents

SESSION_EVENTS = [
    name
    for name, value in vars(SessionEvents).items()
    if not name.startswith("_") and inspect.isfunction(value)
]


def _fresh(module, monkeypatch, trace):
    monkeypatch.setattr(settings, "sql_trace", trace)
    return runpy.run_path(module.__file__, run_name=f"fresh_{module.__name__}")


def _forget_session_listeners(namespace):
    for value in namespace.values():
        if (
            not callable(value)
            or getattr(value, "__module__", None) != namespace["__name__"]
        ):
            continue
        for name in SESSION_EVENTS:
            if event.contains(Session, name, value):
                event.remove(Session, name, value)


def _session_listeners():
    probe = Session()
    return {name: list(getattr(probe.dispatch, name)) for name in SESSION_EVENTS}


def test_the_database_engine_labels_statements_only_with_sql_trace_on(monkeypatch):
    listeners = _session_listeners()
    for trace in (True, False):
        fresh = _fresh(live_session, monkeypatch, trace)
        try:
            labelled = event.contains(
                fresh["engine"], "before_cursor_execute", db_trace._label_statement
            )
            assert labelled is trace
        finally:
            _forget_session_listeners(fresh)
            fresh["engine"].dispose()
    assert _session_listeners() == listeners
    assert not event.contains(
        live_session.engine, "before_cursor_execute", db_trace._label_statement
    )


def test_worker_tasks_are_labelled_only_with_sql_trace_on(monkeypatch, capsys):
    task = type("Task", (), {"name": "keen.synthetic_task"})()
    running = live_worker.celery_app
    try:
        for trace in (True, False):
            _fresh(live_worker, monkeypatch, trace)
            try:
                task_prerun.send(sender=task, task_id=f"t-{trace}", task=task)
                assert (f"t-{trace}" in worker_trace._running) is trace
                task_postrun.send(
                    sender=task, task_id=f"t-{trace}", task=task, state="SUCCESS"
                )
            finally:
                worker_trace.disconnect_task_labels()
    finally:
        running.set_current()

    lines = [line for line in capsys.readouterr().err.splitlines() if line]
    assert len(lines) == 1 and '"name": "keen.synthetic_task"' in lines[0]
    assert worker_trace._running == {}
