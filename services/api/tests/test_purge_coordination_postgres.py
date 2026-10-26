"""Real PostgreSQL concurrency regression. Runs in the normal GitHub CI database."""

import os
import threading
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from app.core.datetime_utils import utc_now_naive
from app.db.models import Base, Event, SourceIngestionState
from app.services import evidence_retention as retention
from app.services.errors import DomainError
from app.services.ingestion_pause import is_paused, require_receiving
from fastapi import HTTPException
from sqlalchemy import DefaultClause, MetaData, create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.db_helpers import schema_engine


@pytest.fixture
def database():
    url = os.environ.get("KEEN_RETENTION_TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "Set KEEN_RETENTION_TEST_DATABASE_URL for real PostgreSQL concurrency tests"
        )
    schema = "purge_coord_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as c:
        c.execute(text(f"CREATE SCHEMA {schema}"))
    engine = schema_engine(url, schema)
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    for table in metadata.tables.values():
        for column in table.columns:
            if (
                column.server_default is not None
                and str(column.server_default.arg) == "now() AT TIME ZONE 'UTC'"
            ):
                column.server_default = DefaultClause(
                    text("(now() AT TIME ZONE 'UTC')")
                )
    metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO evidence_retention_policy (id,mode) VALUES (1,'disabled')"
            )
        )
    with (
        patch.object(retention, "SessionLocal", factory),
        patch.object(retention, "clear_stats_caches"),
    ):
        yield engine, factory
    engine.dispose()
    with admin.begin() as c:
        c.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    admin.dispose()


def test_purge_drains_transactions_rejects_writes_and_preserves_manual_pause(database):
    engine, factory = database
    with factory() as db:
        db.add(
            Event(
                source="test",
                external_id="before",
                timestamp=utc_now_naive(),
                summary="Before purge",
            )
        )
        db.add(SourceIngestionState(source="rss", paused=True))
        db.commit()
        retention.enqueue(db, "all", None, False, "test")
        db.commit()
    with factory() as db:
        assert is_paused(db, "keen-agent")
        with pytest.raises((HTTPException, DomainError)):
            require_receiving(db, "webhooks")
        db.add(
            Event(
                source="test",
                external_id="during",
                timestamp=utc_now_naive(),
                summary="Must not arrive",
            )
        )
        with pytest.raises((HTTPException, DomainError)):
            db.flush()
        db.rollback()
    held = factory()
    held.execute(text("SELECT 1"))  # Holds the ordinary transaction's shared lease.
    finished = threading.Event()
    started = threading.Event()
    errors = []

    def purge():
        started.set()
        try:
            retention.database_batch()
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=purge, daemon=True)
    worker.start()
    try:
        assert started.wait(2)
        time.sleep(0.15)
        assert not finished.is_set(), "Purge must wait for the active transaction"
    finally:
        held.rollback()
        held.close()
        worker.join(8)
    assert finished.is_set() and not errors
    assert not retention.database_batch()  # Empty batch marks completion.
    with factory() as db:
        assert db.query(Event).count() == 0
        assert is_paused(db, "keen-agent")  # Completion starts a full cooldown.
        assert db.get(SourceIngestionState, "rss").paused
        db.execute(
            text(
                "UPDATE evidence_purge_jobs SET updated_at=clock_timestamp() AT TIME ZONE 'UTC' - interval '6 minutes'"
            )
        )
        db.commit()
        assert not is_paused(db, "keen-agent")
        assert is_paused(db, "rss")


def test_cancellation_also_cools_down(database):
    _, factory = database
    with factory() as db:
        retention.enqueue(db, "all", None, False, "test")
        db.commit()
        db.execute(
            text(
                "UPDATE evidence_purge_jobs SET status='cancelled',updated_at=clock_timestamp() AT TIME ZONE 'UTC'"
            )
        )
        db.commit()
        assert is_paused(db, "webhooks")


def _run_beside_a_transaction(factory, function, settle=0.15):
    """Run ``function`` while another session holds the shared ingestion lease.

    Returns whether it finished before that transaction ended, and its result.
    """
    held = factory()
    held.execute(text("SELECT 1"))
    finished = threading.Event()
    outcome = {}

    def run():
        try:
            outcome["value"] = function()
        except Exception as error:
            outcome["error"] = error
        finally:
            finished.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        early = finished.wait(2)
        time.sleep(settle)
    finally:
        held.rollback()
        held.close()
        worker.join(8)
    assert finished.is_set() and "error" not in outcome, outcome
    return early, outcome["value"]


def _automatic_age_policy(factory, days):
    with factory() as db:
        db.execute(
            text("UPDATE evidence_retention_policy SET mode='age', value=:days"),
            {"days": days},
        )
        db.commit()


def test_disabled_retention_never_waits_for_the_exclusive_lease(database):
    _, factory = database
    early, more = _run_beside_a_transaction(factory, retention.database_batch)
    assert early, "Disabled retention must not queue behind open transactions"
    assert more is False
    with factory() as db:
        assert (
            db.execute(text("SELECT count(*) FROM evidence_purge_jobs")).scalar() == 0
        )


def test_automatic_retention_with_nothing_eligible_completes_beside_transactions(
    database,
):
    _, factory = database
    _automatic_age_policy(factory, 30)
    with factory() as db:
        db.add(
            Event(
                source="test",
                external_id="recent",
                timestamp=utc_now_naive(),
                summary="Inside the retention period",
            )
        )
        db.commit()
    early, more = _run_beside_a_transaction(factory, retention.database_batch)
    assert early, "An automatic job with nothing to delete must not drain requests"
    assert more is False
    with factory() as db:
        job = db.execute(
            text("SELECT automatic, status, deleted FROM evidence_purge_jobs")
        ).one()
        assert tuple(job) == (True, "complete", 0)
        assert db.query(Event).count() == 1
        # Automatic retention never pauses ingestion.
        assert not is_paused(db, "keen-agent")


def test_automatic_retention_with_eligible_events_still_drains_and_deletes(database):
    _, factory = database
    _automatic_age_policy(factory, 30)
    with factory() as db:
        db.add_all(
            [
                Event(
                    source="test",
                    external_id="expired",
                    timestamp=utc_now_naive() - timedelta(days=40),
                    summary="Beyond the retention period",
                ),
                Event(
                    source="test",
                    external_id="recent",
                    timestamp=utc_now_naive(),
                    summary="Inside the retention period",
                ),
            ]
        )
        db.commit()
    early, more = _run_beside_a_transaction(factory, retention.database_batch)
    assert not early, "Deletion must wait for open transactions to finish"
    assert more is True
    with factory() as db:
        assert [e.external_id for e in db.query(Event)] == ["recent"]
        assert db.execute(text("SELECT status FROM evidence_purge_jobs")).scalar() == (
            "running"
        )
    # The running job finishes on the next batch.
    assert retention.database_batch() is False
    with factory() as db:
        job = db.execute(text("SELECT status, deleted FROM evidence_purge_jobs")).one()
        assert tuple(job) == ("complete", 1)
