"""Scheduled collection runs: run locks, lost lock sessions and environment defaults.

Each collection run holds a PostgreSQL session advisory lock on a dedicated
handle, so a connection, a Redmine instance or the Risk Ledger import runs once
at a time. These tests hold and lose those locks against the contract database.
"""

import re
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
from app.ingest import connections as sc
from app.ingest import redmine, riskledger
from sqlalchemy import event, text
from sqlalchemy.exc import DataError, OperationalError

INGEST = Path(sc.__file__).resolve().parent


@pytest.fixture
def pg(contract_db):
    if contract_db.get_bind().dialect.name != "postgresql":
        pytest.skip("Run locks are PostgreSQL advisory locks")
    return contract_db


def named(key="db:loki", source="loki"):
    return sc.Connection(key, source, "Named " + key, {}, {}, {})


@contextmanager
def statements(engine):
    sent = []

    def record(conn, cursor, statement, parameters, context, executemany):
        sent.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield sent
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_a_connection_already_running_elsewhere_is_skipped(pg, monkeypatch):
    monkeypatch.setattr(sc.deployment_settings, "loki_enabled", True)
    monkeypatch.setattr(sc, "connections", lambda *a: [named()])
    calls = []

    @sc.connection_runs("loki")
    def run(db):
        calls.append(sc.identity()["connection_id"])
        return [{"created_events": 1}]

    with sc.run_lock(pg, "db:loki") as held:
        assert held is True
        with statements(pg.get_bind().engine) as sent:
            busy = run(pg)

    assert busy == [
        {
            "connection_id": "db:loki",
            "connection_name": "Named db:loki",
            "skipped": True,
            "reason": "Connection is already running",
        }
    ]
    assert calls == []
    # The skipped run asked for the lock once and released nothing it lacked.
    assert [q for q in sent if "advisory" in q] == [
        "SELECT pg_try_advisory_lock(%(key)s)"
    ]
    # Released by the first holder, the connection runs again.
    assert run(pg)[0]["created_events"] == 1
    assert calls == ["db:loki"]


def _advisory_holder(db, before=()):
    """The pid and key of the advisory lock taken since ``before``."""
    rows = db.execute(
        text(
            "SELECT pid, classid, objid FROM pg_locks WHERE locktype = 'advisory' "
            "AND granted AND database = (SELECT oid FROM pg_database "
            "WHERE datname = current_database())"
        )
    ).all()
    (row,) = [row for row in rows if row.pid not in before]
    key = (row.classid << 32) | row.objid
    return row.pid, key - (1 << 64) if key >= 1 << 63 else key


def _holders(db):
    return {
        pid
        for (pid,) in db.execute(
            text("SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted")
        )
    }


def _taken_by_another_session(engine, key):
    """Whether another session obtains the lock within a bounded wait."""
    with engine.connect() as other:
        other.execute(text("SET LOCAL lock_timeout = '5s'"))
        other.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        other.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        other.commit()
    return True


LOCKS = {
    "connection": lambda db: sc.run_lock(db, "db:loki"),
    "redmine": lambda db: redmine._run_lock(db, "https://redmine.example"),
    "riskledger": lambda db: riskledger._run_lock(db),
}


@pytest.mark.parametrize("lock", LOCKS.values(), ids=LOCKS.keys())
def test_a_lock_whose_session_is_lost_is_released_and_the_loss_reported(pg, lock):
    before = _holders(pg)
    with pytest.raises(OperationalError) as lost:
        with lock(pg) as acquired:
            assert acquired is True
            holder, key = _advisory_holder(pg, before)
            # The database ends the lock's session, as a restart or an
            # administrator would, before the run releases the lock.
            assert (
                pg.execute(
                    text("SELECT pg_terminate_backend(:pid, 5000)"), {"pid": holder}
                ).scalar()
                is True
            )

    # The release reached a closed connection, and that loss is reported.
    assert lost.value.connection_invalidated is True
    assert str(lost.value.orig).startswith("server closed the connection unexpectedly")
    # The lock ended with its session, so another run takes it.
    assert _taken_by_another_session(pg.get_bind().engine, key) is True


@contextmanager
def failing_release(engine):
    """Make the database refuse the release, its session still open."""

    def refuse(conn, cursor, statement, parameters, context, executemany):
        if "pg_advisory_unlock" in statement:
            return "SELECT 1 / 0", parameters
        return statement, parameters

    event.listen(engine, "before_cursor_execute", refuse, retval=True)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", refuse)


@pytest.mark.parametrize("lock", LOCKS.values(), ids=LOCKS.keys())
def test_a_lock_that_cannot_be_released_never_outlives_its_handle(pg, lock):
    engine = pg.get_bind().engine
    before = _holders(pg)
    with pytest.raises(DataError, match="division by zero"):
        with failing_release(engine), lock(pg) as acquired:
            assert acquired is True
            _, key = _advisory_holder(pg, before)

    # The handle is discarded rather than pooled with the lock still held, so
    # its session ends and the lock with it.
    assert _taken_by_another_session(engine, key) is True


def test_an_environment_imap_default_never_runs(monkeypatch):
    monkeypatch.setattr(sc.deployment_settings, "imap_enabled", True)
    monkeypatch.setattr(sc.deployment_settings, "demo_mode", False)
    default = sc.Connection(
        "env:imap", "imap", "Environment default", {}, {}, None, True, "environment"
    )
    monkeypatch.setattr(
        sc, "connections", lambda *a: [default, named("db:mail", "imap")]
    )
    monkeypatch.setattr(
        sc,
        "deployment_document",
        Mock(side_effect=AssertionError("IMAP has no environment inputs to read")),
    )
    seen = []

    @sc.connection_runs("imap")
    def run(db):
        seen.append(sc.identity()["connection_id"])
        return []

    # IMAP collects only through named connections.
    assert run(None) == []
    assert seen == ["db:mail"]


def test_every_fanned_out_source_declares_its_input_sections():
    # Environment defaults are skipped unless their document selects evidence,
    # which the runner reads from INPUT_SECTIONS; IMAP alone has no defaults.
    decorated = {
        name
        for path in INGEST.glob("*.py")
        for name in re.findall(r'@connection_runs\("([a-z_]+)"\)', path.read_text())
    }
    assert "loki" in decorated and "imap" in decorated
    assert decorated - {"imap"} <= set(sc.INPUT_SECTIONS)
