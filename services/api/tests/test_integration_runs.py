"""Background runs of custom API integrations.

The worker claims a queued run, collects through the published revision and
records a safe outcome; the scheduler queues due collectors and surfaces
failures. Collection itself is replaced so each test chooses the outcome.
"""

import json
import socket
import ssl
from datetime import timedelta
from functools import partial
from types import SimpleNamespace

import pytest
from app.api.routes import integrations as api
from app.core.datetime_utils import utc_now_naive
from app.db.models import Event, SourceIngestionState
from app.db.models import IntegrationCollector as Collector
from app.db.models import IntegrationRun as Run
from app.db.models import IsmsEffectivenessMeasure as Measure
from app.integrations import engine, runtime
from app.integrations.engine import skipped_summary
from app.integrations.errors import IntegrationError
from app.integrations.schema import Definition
from cryptography.fernet import InvalidToken

# Imported fixture: an integration database behind runtime.SessionLocal.
from tests.test_integration_builder import database  # noqa: F401


def published(db):
    api.action_collector("collector", "publish", api.Action(version=1), db)
    run = runtime.queue_run(db, db.get(Collector, "collector"))
    db.commit()
    return run.id


def outcome(db, run_id):
    db.expire_all()
    run = db.get(Run, run_id)
    return run.status, run.message


@pytest.mark.parametrize(
    "error,message",
    [
        (
            InvalidToken(),
            "Saved credentials could not be decrypted. Check that API and worker use "
            "the original shared integration secret key.",
        ),
        (
            socket.gaierror("lookup failed for api.internal"),
            "DNS lookup failed for the configured API host.",
        ),
        (
            ssl.SSLError("certificate verify failed"),
            "TLS verification failed. Check the upstream certificate and trust configuration.",
        ),
        (
            TimeoutError("slow"),
            "The upstream API timed out. Narrow the query or check service availability.",
        ),
        (
            json.JSONDecodeError("Expecting value", "<html>", 0),
            "The API response was not valid JSON. Check the endpoint and authentication.",
        ),
        (
            RuntimeError("driver failure with token=synthetic-secret"),
            "Collection failed; verify definition, connection and worker configuration.",
        ),
    ],
    ids=["credentials", "dns", "tls", "timeout", "json", "unexpected"],
)
def test_failures_are_recorded_with_a_safe_explanation(
    database,  # noqa: F811 - fixture imported above
    monkeypatch,
    error,
    message,
):
    db, _ = database
    run_id = published(db)

    def broken(*args):
        raise error

    monkeypatch.setattr(runtime, "collect", broken)

    runtime.run_job(run_id)

    assert outcome(db, run_id) == ("failed", message)
    assert db.get(Collector, "collector").failures == 1


def test_integration_error_text_is_redacted_before_it_is_kept(
    database,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    db, _ = database
    run_id = published(db)

    def broken(*args):
        raise IntegrationError("HTTP 401 from upstream; password=synthetic-secret")

    monkeypatch.setattr(runtime, "collect", broken)

    runtime.run_job(run_id)

    status, message = outcome(db, run_id)
    assert status == "failed" and message.startswith("HTTP 401 from upstream")
    assert "synthetic-secret" not in message


def test_a_run_claimed_elsewhere_is_left_alone(
    database,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    db, _ = database
    run_id = published(db)
    db.get(Run, run_id).status = "running"
    db.commit()
    monkeypatch.setattr(runtime, "collect", lambda *a: pytest.fail("collected twice"))

    runtime.run_job(run_id)

    assert outcome(db, run_id)[0] == "running"


def test_a_pause_cancels_runs_before_collection(
    database,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    db, _ = database
    run_id = published(db)
    db.add(SourceIngestionState(source="api-ingesters", paused=True))
    db.commit()
    monkeypatch.setattr(runtime, "collect", lambda *a: pytest.fail("collected"))

    runtime.run_job(run_id)

    assert outcome(db, run_id) == (
        "cancelled",
        "Source ingestion paused before collection began",
    )
    with pytest.raises(ValueError, match="paused in Sources & evidence mapping"):
        runtime.queue_run(db, db.get(Collector, "collector"))


@pytest.mark.parametrize("change", ["unit", "deleted"])
def test_a_measurement_destination_that_changed_fails_the_run(
    database,  # noqa: F811 - fixture imported above
    monkeypatch,
    change,
):
    db, _ = database
    measure = Measure(framework_slug="test", summary="Uptime", target_unit="%")
    db.add(measure)
    db.commit()
    fields = Definition().model_dump()["fields"]
    db.get(Collector, "collector").draft = Definition(
        output="measurement",
        measure_id=str(measure.id),
        unit="%",
        fields={
            **fields,
            "value": {"value": 99.9},
            "period_start": {"value": "2026-01-01"},
            "period_end": {"value": "2026-01-31"},
        },
    ).model_dump()
    db.commit()
    run_id = published(db)
    if change == "unit":
        measure.target_unit = "hours"
    else:
        db.delete(measure)
    db.commit()
    monkeypatch.setattr(
        runtime, "collect", lambda *a: ([], None, 1, skipped_summary({}))
    )

    runtime.run_job(run_id)

    assert outcome(db, run_id) == (
        "failed",
        "Measurement destination no longer exists or its unit differs",
    )


def test_the_scheduler_queues_due_collectors_and_reports_broker_failures(
    database,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    db, _ = database
    api.action_collector("collector", "publish", api.Action(version=1), db)
    collector = db.get(Collector, "collector")
    collector.next_run = utc_now_naive() - timedelta(minutes=1)
    db.commit()

    def unavailable(run_id):
        raise ConnectionError("broker down")

    monkeypatch.setattr(runtime, "dispatch", unavailable)
    before = utc_now_naive()

    runtime.tick()

    db.expire_all()
    (run,) = db.query(Run).all()
    assert (run.status, run.message) == (
        "failed",
        "Could not queue run; check broker health.",
    )
    # The next run waits for the published interval.
    assert db.get(Collector, "collector").next_run >= before + timedelta(minutes=60)


def test_the_scheduler_passes_over_a_collector_whose_live_revision_is_gone(
    database,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    # Connection removal can delete a revision a concurrent rollback has just
    # made live; that collector waits, and the other due collectors are queued.
    db, _ = database
    api.action_collector("collector", "publish", api.Action(version=1), db)
    db.add(
        Collector(
            id="orphan",
            name="Orphan",
            connection_id="connection",
            draft=Definition().model_dump(),
            live_revision=4,
            enabled=True,
        )
    )
    due = utc_now_naive() - timedelta(minutes=1)
    for ident in ("collector", "orphan"):
        db.get(Collector, ident).next_run = due
    db.commit()
    dispatched = []
    monkeypatch.setattr(runtime, "dispatch", dispatched.append)

    runtime.tick()

    db.expire_all()
    (run,) = db.query(Run).all()
    assert (run.collector_id, run.status) == ("collector", "queued")
    assert dispatched == [run.id]
    assert db.get(Collector, "orphan").next_run == due


def test_a_pause_cancels_queued_runs_at_the_next_tick(
    database,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    db, _ = database
    run_id = published(db)
    db.add(SourceIngestionState(source="api-ingesters", paused=True))
    db.commit()
    monkeypatch.setattr(runtime, "dispatch", lambda run_id: pytest.fail("dispatched"))

    runtime.tick()

    assert outcome(db, run_id) == (
        "cancelled",
        "Source ingestion paused before collection began",
    )


def test_records_the_database_cannot_hold_as_written_are_stored(
    database,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    # Upstream JSON can decode to lone surrogates and non-finite numbers,
    # which PostgreSQL text and JSONB refuse. One such record, wherever the
    # value sits, must not fail the run, hold the cursor back and count
    # towards pausing the collector.
    db, _ = database
    run_id = published(db)
    page = [
        {
            "id": "check-\ud800",
            "created_at": "2026-01-01T12:00:00Z",
            "title": "Lone \udfff surrogate",
            "extra": {"key\ud800": [float("inf"), "x\x00y"]},
        },
        {
            "id": 2,
            "created_at": "2026-01-01T12:05:00Z",
            "title": "Not a number",
            "load": float("nan"),
            "floor": float("-inf"),
        },
        {"id": 3, "created_at": "2026-01-01T12:10:00Z", "title": "Restore tested"},
    ]
    monkeypatch.setattr(
        runtime, "collect", partial(engine.collect, fetch=lambda *a, **k: (page, {}))
    )
    import app.ingest.common as common

    written = []

    def put_bytes(**stored):
        written.append(stored["data"])
        return SimpleNamespace(uri="test://artifact", sha256="0" * 64, size_bytes=50)

    monkeypatch.setattr(common, "put_bytes", put_bytes)
    monkeypatch.setattr(common, "apply_rules", lambda *a: 0)
    collector = db.get(Collector, "collector")
    collector.cursor = "2025-12-31T00:00:00+00:00"
    collector.failures = 2
    db.commit()

    runtime.run_job(run_id)

    status, message = outcome(db, run_id)
    assert (status, message) == ("succeeded", "3 new records; 0 duplicates.")
    events = {e.summary: e for e in db.query(Event).all()}
    assert set(events) == {"Lone \ufffd surrogate", "Not a number", "Restore tested"}
    surrogate = events["Lone \ufffd surrogate"].normalized_payload
    assert surrogate["id"] == "check-\ufffd"
    assert surrogate["extra"] == {"key\ufffd": ["inf", "x\ufffdy"]}
    numbers = events["Not a number"].normalized_payload
    assert (numbers["load"], numbers["floor"]) == ("nan", "-inf")
    # Every artifact was written as UTF-8.
    assert len(written) == 3 and all(data.decode("utf-8") for data in written)
    # Records whose identifiers differ only in a lone surrogate stay distinct.
    assert engine.external_id("c", "check-\ud800") != engine.external_id(
        "c", "check-\ud801"
    )
    collector = db.get(Collector, "collector")
    assert collector.cursor == "2026-01-01T12:10:00+00:00"
    assert collector.failures == 0 and collector.enabled


def test_an_unusable_record_is_left_out_of_a_stored_run(
    database,  # noqa: F811 - fixture imported above
    monkeypatch,
):
    # One upstream record with an empty title, among records the definition
    # can use, is counted in the run's outcome; the others are stored, the
    # cursor moves on and the run does not count towards pausing.
    db, _ = database
    run_id = published(db)
    page = [
        {"id": 1, "created_at": "2026-01-01T12:00:00Z", "title": "Backup done"},
        {"id": 2, "created_at": "2026-01-01T12:05:00Z", "title": ""},
        {"id": 3, "created_at": "2026-01-01T12:10:00Z", "title": "Restore tested"},
    ]
    monkeypatch.setattr(
        runtime, "collect", partial(engine.collect, fetch=lambda *a, **k: (page, {}))
    )
    import app.ingest.common as common

    monkeypatch.setattr(
        common,
        "put_bytes",
        lambda **k: SimpleNamespace(
            uri="test://artifact", sha256="0" * 64, size_bytes=50
        ),
    )
    monkeypatch.setattr(common, "apply_rules", lambda *a: 0)
    collector = db.get(Collector, "collector")
    collector.cursor = "2025-12-31T00:00:00+00:00"
    collector.failures = 2
    db.commit()

    runtime.run_job(run_id)

    status, message = outcome(db, run_id)
    assert status == "succeeded"
    assert message == (
        "2 new records; 0 duplicates. 1 record skipped: "
        "Record identifier and summary must not be empty (1)."
    )
    assert sorted(e.summary for e in db.query(Event).all()) == [
        "Backup done",
        "Restore tested",
    ]
    run = db.get(Run, run_id)
    assert run.result["skipped"] == {
        "count": 1,
        "reasons": [
            {"reason": "Record identifier and summary must not be empty", "count": 1}
        ],
    }
    collector = db.get(Collector, "collector")
    assert collector.cursor == "2026-01-01T12:10:00+00:00"
    assert collector.failures == 0 and collector.enabled
