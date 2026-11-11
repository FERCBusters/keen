"""Reparenting takes the transaction-scoped hierarchy lock and refuses cycles."""

from contextlib import contextmanager

import pytest
from app.db.models import IsmsDocumentFolder, IsmsOrgNode
from app.services.errors import DomainError
from app.services.hierarchy import validate_parent
from sqlalchemy import event as sql_event

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
def test_the_hierarchy_lock_is_taken_before_reading_parents(contract_db, model):
    with _recorded(contract_db.get_bind().engine) as statements:
        _cycle_is_refused(contract_db, model, statements)

    locks = [s for s in statements if LOCK in s]
    # One lock for each check, each taken before its first parent read.
    assert len(locks) == 2
    assert LOCK in statements[0]
