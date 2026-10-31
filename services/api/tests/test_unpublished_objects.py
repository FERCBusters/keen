"""Objects stored by a transaction that never commits are queued for cleanup.

Stored evidence objects are never replaced, so an object written by a rolled
back attempt has no record pointing at it. The retention worker deletes queued
objects that nothing references; a savepoint's outcome never queues objects the
outer transaction may still commit.
"""

import pytest
from app.db.session import track_unpublished_object
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tests.db_helpers import create_sqlite_schema


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_sqlite_schema(engine)
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


def test_a_session_joined_to_a_connection_queues_in_its_transaction(contract_db):
    contract_db.execute(text("SELECT 1"))
    track_unpublished_object(contract_db, "local://local/joined.json")
    contract_db.rollback()
    rows = contract_db.execute(
        text("SELECT storage_uri FROM evidence_object_cleanup")
    ).scalars()
    assert list(rows) == ["local://local/joined.json"]
