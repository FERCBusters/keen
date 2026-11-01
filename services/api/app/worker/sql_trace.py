"""Label each Celery task's SQL statements while KEEN_SQL_TRACE is on."""

from __future__ import annotations

import time

from celery.signals import task_postrun, task_prerun

from app.db import sql_trace

_running: dict[str, tuple] = {}


def _task_started(task_id=None, task=None, **_):
    record = {
        "kind": "task",
        "id": sql_trace.new_id(),
        "name": sql_trace.clean(task.name, 120),
    }
    token = sql_trace.begin("task", record["id"], record["name"])
    _running[task_id] = (token, time.perf_counter(), record)


def _task_finished(task_id=None, state=None, **_):
    started = _running.pop(task_id, None)
    if started is None:
        return
    token, began, record = started
    record["ms"] = round((time.perf_counter() - began) * 1000, 1)
    record["state"] = sql_trace.clean(state or "", 20)
    sql_trace.end(token, record)


def connect_task_labels() -> None:
    task_prerun.connect(_task_started, weak=False)
    task_postrun.connect(_task_finished, weak=False)


def disconnect_task_labels() -> None:
    task_prerun.disconnect(_task_started)
    task_postrun.disconnect(_task_finished)
