"""SQL statement labels for performance tracing (KEEN_SQL_TRACE)."""

import json
import re

import pytest
from app import main
from app.api.sql_trace import RequestLabelMiddleware, page_label, route_label
from app.core.config import Settings
from app.db.sql_trace import install_statement_labels
from app.worker.sql_trace import connect_task_labels, disconnect_task_labels
from celery.signals import task_postrun, task_prerun
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

LABEL = re.compile(r"^/\* keen (req|task)=([0-9a-f]{12}) (.*?) \*/ (.*)$", re.S)


@pytest.fixture
def engine():
    # One shared connection, closed by dispose, also for handler threads.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    install_statement_labels(engine)
    seen = []
    event.listen(
        engine,
        "after_cursor_execute",
        lambda conn, cursor, statement, *args: seen.append(statement),
    )
    engine.seen = seen
    yield engine
    engine.dispose()


def trace_lines(captured):
    prefix = "KEEN_SQL_TRACE "
    return [
        json.loads(line[len(prefix) :])
        for line in captured.err.splitlines()
        if line.startswith(prefix)
    ]


@pytest.mark.parametrize(
    "path,expected",
    [
        (
            "/v1/controls/17ac20d3-34e3-5704-b0a5-65e5d3e0e906/evidence",
            "/v1/controls/{id}/evidence",
        ),
        ("/v1/events/17ac20d334e35704b0a565e5d3e0e906", "/v1/events/{id}"),
        ("/v1/audits/42/report", "/v1/audits/{id}/report"),
        ("/v1/frameworks/iso_27001_2022", "/v1/frameworks/iso_27001_2022"),
        ("/v1/x*/ DROP TABLE users; --", "/v1/x/DROPTABLEusers--"),
        ("", "-"),
    ],
)
def test_routes_keep_their_shape_and_drop_identifiers(path, expected):
    assert route_label(path) == expected


@pytest.mark.parametrize(
    "referer,expected",
    [
        (
            "https://keen.example/control.html?id=17ac20d3&framework=a#evidence",
            "/control.html",
        ),
        ("https://keen.example/a*/; SELECT pg_sleep(1) --", "/a/SELECTpg_sleep1--"),
        ("", "-"),
        ("http://[broken", "-"),
    ],
)
def test_pages_are_paths_without_query_strings(referer, expected):
    assert page_label(referer) == expected


def test_statements_outside_a_request_or_task_are_unlabelled(engine):
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    assert engine.seen == ["SELECT 1"]


def test_a_request_labels_its_statements_and_reports_its_time(engine, capsys):
    api = FastAPI()

    @api.get("/v1/things/{thing_id}")
    def thing(thing_id: str):
        # Synchronous handlers run in a worker thread with the request's context.
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ok": True}

    @api.get("/v1/broken")
    def broken():
        with engine.connect() as connection:
            connection.execute(text("SELECT 2"))
        raise HTTPException(503, "down")

    client = TestClient(RequestLabelMiddleware(api))
    referer = "https://keen.example/events.html?q=secret*/ --"
    assert client.get("/v1/things/42", headers={"Referer": referer}).status_code == 200
    assert client.get("/v1/broken").status_code == 503

    labels = [LABEL.match(statement) for statement in engine.seen]
    assert [m[1] for m in labels] == ["req", "req"]
    assert labels[0][3] == "GET /v1/things/{id} page=/events.html"
    assert labels[1][3] == "GET /v1/broken page=-"
    assert [m[4] for m in labels] == ["SELECT 1", "SELECT 2"]
    assert labels[0][2] != labels[1][2]

    first, second = trace_lines(capsys.readouterr())
    assert first["id"] == labels[0][2] and second["id"] == labels[1][2]
    assert {k: first[k] for k in ("kind", "method", "route", "page", "status")} == {
        "kind": "request",
        "method": "GET",
        "route": "/v1/things/{id}",
        "page": "/events.html",
        "status": 200,
    }
    assert (
        second["status"] == 503 and first["ms"] >= 0 and first["end"].endswith("+00:00")
    )

    # The label ends with the request.
    with engine.connect() as connection:
        connection.execute(text("SELECT 3"))
    assert engine.seen[-1] == "SELECT 3"


@pytest.mark.anyio
async def test_other_connections_pass_through_unlabelled(capsys):
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    await RequestLabelMiddleware(app)({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]
    assert trace_lines(capsys.readouterr()) == []


def test_worker_tasks_label_their_statements(engine, capsys):
    connect_task_labels()
    try:
        task = type("Task", (), {"name": "keen.dispatch_imap"})()
        task_prerun.send(sender=task, task_id="t1", task=task)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        task_postrun.send(sender=task, task_id="t1", task=task, state="SUCCESS")
        # A task the worker never started is ignored.
        task_postrun.send(sender=task, task_id="unknown", task=task, state="FAILURE")
    finally:
        disconnect_task_labels()

    label = LABEL.match(engine.seen[0])
    assert (label[1], label[3]) == ("task", "keen.dispatch_imap")
    (record,) = trace_lines(capsys.readouterr())
    assert record["id"] == label[2]
    assert (record["kind"], record["name"], record["state"]) == (
        "task",
        "keen.dispatch_imap",
        "SUCCESS",
    )
    with engine.connect() as connection:
        connection.execute(text("SELECT 2"))
    assert engine.seen[-1] == "SELECT 2"


def test_tracing_is_off_by_default():
    assert Settings.model_fields["sql_trace"].default is False
    assert not any(m.cls is RequestLabelMiddleware for m in main.app.user_middleware)
