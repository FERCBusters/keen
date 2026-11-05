"""Reparenting takes PostgreSQL's transaction-scoped hierarchy lock; SQLite,
used for quick local runs, has no advisory locks and still refuses cycles."""

from contextlib import contextmanager

import pytest
from app.db.models import IsmsDocumentFolder, IsmsOrgNode
from app.services.errors import DomainError
from app.services.hierarchy import validate_parent
from sqlalchemy import create_engine
from sqlalchemy import event as sql_event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tests.db_helpers import create_sqlite_schema

LOCK = "pg_advisory_xact_lock"


def _cycle_is_refused(db, model, statements):
    top, child = model(name="top"), model(name="child")
    db.add_all([top, child])
    db.flush()
    child.parent_id = top.id
    db.flush()
    statements.clear()

    with pytest.raises(DomainError) as refused:
        validate_parent(db, model, child.id, top.id)

    assert (refused.value.status_code, refused.value.detail) == (
        400,
        "Parent relationship would contain a cycle",
    )
    validate_parent(db, model, top.id, child.id)


@contextmanager
def _recorded(engine):
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sql_event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        sql_event.remove(engine, "before_cursor_execute", record)


@pytest.mark.parametrize("model", [IsmsOrgNode, IsmsDocumentFolder])
def test_sqlite_checks_cycles_without_an_advisory_lock(model):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    try:
        create_sqlite_schema(engine)
        with _recorded(engine) as statements, Session(engine) as db:
            _cycle_is_refused(db, model, statements)
        assert statements and not any(LOCK in s for s in statements)
    finally:
        engine.dispose()


@pytest.mark.parametrize("model", [IsmsOrgNode, IsmsDocumentFolder])
def test_postgresql_takes_the_hierarchy_lock_before_reading_parents(contract_db, model):
    if contract_db.get_bind().dialect.name != "postgresql":
        pytest.skip("Advisory locks are a PostgreSQL feature")
    with _recorded(contract_db.get_bind().engine) as statements:
        _cycle_is_refused(contract_db, model, statements)

    locks = [s for s in statements if LOCK in s]
    # One lock for each check, each taken before its first parent read.
    assert len(locks) == 2
    assert LOCK in statements[0]
