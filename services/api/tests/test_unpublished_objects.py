"""Objects stored by a transaction that never commits are queued for cleanup.

Stored evidence objects are never replaced, so an object written by a rolled
back attempt has no record pointing at it. The retention worker deletes queued
objects that nothing references; a savepoint's outcome never queues objects the
outer transaction may still commit.
"""

import logging

import pytest
from app.db.session import track_unpublished_object
from myf.core.task_deadline import TIME_LIMITS
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.task_limits import checked_statements, deadline_at


@pytest.fixture
def engine(postgres_engine):
    engine = postgres_engine
    yield engine
    engine.dispose()


def queued(engine):
    with engine.connect() as connection:
        return set(
            connection.execute(
                text("SELECT storage_uri FROM evidence_object_cleanup")
            ).scalars()
        )


def test_a_committed_object_is_never_queued(engine):
    with Session(engine) as db:
        db.execute(text("SELECT 1"))
        track_unpublished_object(db, "local://local/kept.json")
        db.commit()
    assert queued(engine) == set()


@pytest.mark.parametrize("finish", ["rollback", "close"])
def test_an_uncommitted_object_is_queued(engine, finish):
    db = Session(engine)
    db.execute(text("SELECT 1"))
    track_unpublished_object(db, "local://local/orphan.json")
    getattr(db, finish)()
    db.close()
    assert queued(engine) == {"local://local/orphan.json"}


def test_savepoints_leave_the_decision_to_the_outer_transaction(engine):
    with Session(engine) as db:
        db.execute(text("SELECT 1"))
        track_unpublished_object(db, "local://local/outer.json")
        with db.begin_nested():
            track_unpublished_object(db, "local://local/inner.json")
        try:
            with db.begin_nested():
                raise RuntimeError("savepoint rolls back")
        except RuntimeError:
            pass
        assert queued(engine) == set()
        db.commit()
    assert queued(engine) == set()

    with Session(engine) as db:
        db.execute(text("SELECT 1"))
        with db.begin_nested():
            track_unpublished_object(db, "local://local/released.json")
        db.rollback()
    assert queued(engine) == {"local://local/released.json"}


def test_an_object_from_a_transaction_the_time_limit_ended_is_still_queued(engine):
    # Past a worker task's deadline every further statement is refused, but
    # queueing the transaction's stored objects is its cleanup, not more work:
    # without it the object would stay in storage outside retention.
    with checked_statements(engine):
        db = Session(engine)
        # The first two checks are the transaction's shared ingestion lock and
        # SELECT 1; the third, SELECT 2, finds the deadline passed.
        with deadline_at(3) as refused:
            db.execute(text("SELECT 1"))
            track_unpublished_object(db, "local://local/stopped.json")
            with pytest.raises(TIME_LIMITS):
                db.execute(text("SELECT 2"))
            db.rollback()
            db.close()
        assert refused == [3]
    assert queued(engine) == {"local://local/stopped.json"}


def test_the_time_limit_leaves_a_savepoint_by_rolling_it_back(engine):
    # The raise that ends a nested block rolls its savepoint back; that undoes
    # work rather than doing more, so the deadline lets it through, and the
    # limit, raised once, is what leaves the block.
    with checked_statements(engine), Session(engine) as db:
        db.execute(text("SELECT 1"))
        # Counted from here: SAVEPOINT, SELECT 2, and SELECT 3, which finds the
        # deadline passed.
        with deadline_at(3) as refused:
            with pytest.raises(TIME_LIMITS):
                with db.begin_nested():
                    db.execute(text("SELECT 2"))
                    db.execute(text("SELECT 3"))
        assert refused == [3]
        db.rollback()


def test_a_session_joined_to_a_connection_queues_in_its_transaction(contract_db):
    contract_db.execute(text("SELECT 1"))
    track_unpublished_object(contract_db, "local://local/joined.json")
    contract_db.rollback()
    rows = contract_db.execute(
        text("SELECT storage_uri FROM evidence_object_cleanup")
    ).scalars()
    assert list(rows) == ["local://local/joined.json"]


def test_objects_that_cannot_be_queued_are_logged_without_failing_the_rollback(
    empty_schema_engine,
    caplog,
):
    # A database without the cleanup queue, as when it is unavailable.
    bare = empty_schema_engine
    try:
        db = Session(bare)
        db.execute(text("SELECT 1"))
        track_unpublished_object(db, "local://local/synthetic-secret-name.json")
        track_unpublished_object(db, "local://local/second.json")
        with caplog.at_level(logging.WARNING, logger="app.db.session"):
            db.rollback()
            db.execute(text("SELECT 1"))
            db.rollback()
        db.close()
    finally:
        bare.dispose()

    (warning,) = [r for r in caplog.records if r.name == "app.db.session"]
    assert warning.getMessage() == "Could not queue 2 unpublished evidence objects"
    assert "synthetic-secret-name" not in caplog.text
