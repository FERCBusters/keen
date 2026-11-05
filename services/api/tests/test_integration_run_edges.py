"""Custom API integrations: page requests without a size parameter, failed
previews, runs removed while their claim fails, and handing runs to the worker.

The upstream API is a fetch function, the database is the SQLite integration
database behind runtime.SessionLocal, and the broker is replaced where a run
is queued.
"""

import pytest
from app.api.routes import integrations as api
from app.db.models import IntegrationCollector as Collector
from app.db.models import IntegrationRun as Run
from app.integrations import runtime
from app.integrations.engine import collect
from app.integrations.errors import IntegrationError
from app.integrations.schema import Definition
from app.worker import tasks
from app.worker.celery_app import celery_app
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

# Imported fixture: a SQLite integration database behind runtime.SessionLocal.
from tests.test_integration_builder import (
    database,  # noqa: F401
    record,
)


def _pages(size_parameter):
    payloads, sent = [[record(1)], [record(2)], []], []

    def fetch(url, **kw):
        sent.append(dict(kw["query"]))
        return payloads[len(sent) - 1], {}

    paging = {"mode": "page", "size": 1, "size_parameter": size_parameter}
    d = Definition(pagination=paging, query={"state": "open"})
    rows, _, pages, _ = collect(d, "https://example.org", {}, fetch=fetch)
    return rows, pages, sent


def test_page_requests_name_a_page_size_only_when_the_api_takes_one():
    rows, pages, sent = _pages("")
    assert (len(rows), pages) == (2, 3)
    assert sent == [{"state": "open", "page": n} for n in (1, 2, 3)]

    _, _, sized = _pages("per_page")
    assert sized == [{"state": "open", "page": n, "per_page": 1} for n in (1, 2, 3)]


def test_failed_previews_spend_none_of_the_collectors_failure_budget(
    database,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    db, _ = database
    api.action_collector("collector", "publish", api.Action(version=1), db)

    def refused(*args):
        raise IntegrationError("HTTP 403 from upstream")

    monkeypatch.setattr(runtime, "collect", refused)
    previews = []
    for _ in range(3):
        run = runtime.queue_run(db, db.get(Collector, "collector"), True)
        db.commit()
        runtime.run_job(run.id)
        previews.append(run.id)

    db.expire_all()
    assert [(db.get(Run, r).status, db.get(Run, r).message) for r in previews] == [
        ("failed", "HTTP 403 from upstream")
    ] * 3
    collector = db.get(Collector, "collector")
    assert (collector.failures, collector.enabled) == (0, True)


def test_a_run_removed_while_its_claim_fails_leaves_nothing_to_record(
    database,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    db, factory = database
    api.action_collector("collector", "publish", api.Action(version=1), db)
    run_id = runtime.queue_run(db, db.get(Collector, "collector")).id
    db.commit()
    # Connection removal deletes the queued run while the worker's claim is
    # the deadlock victim.
    db.query(Run).filter_by(id=run_id).delete()
    db.commit()
    monkeypatch.setattr(runtime, "collect", lambda *a: pytest.fail("collected"))
    engine = factory.kw["bind"]

    def deadlocked(conn, cursor, statement, *args):
        if statement.startswith("UPDATE integration_runs"):
            raise OperationalError(statement, {}, Exception("deadlock detected"))

    event.listen(engine, "before_cursor_execute", deadlocked)
    try:
        runtime.run_job(run_id)
    finally:
        event.remove(engine, "before_cursor_execute", deadlocked)

    db.expire_all()
    assert db.query(Run).count() == 0
    assert db.get(Collector, "collector").failures == 0


def test_a_dispatched_run_is_queued_for_the_http_collection_workers(monkeypatch):
    queued = []

    def apply_async(args=None, kwargs=None, **options):
        queued.append((args, kwargs, options))

    monkeypatch.setattr(tasks.integration_run_task, "apply_async", apply_async)

    runtime.dispatch("9b2f6c1e-run")

    assert queued == [(("9b2f6c1e-run",), {}, {})]
    assert celery_app.conf.task_routes[tasks.integration_run_task.name] == {
        "queue": "ingest-http"
    }
