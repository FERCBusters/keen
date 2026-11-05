"""A blank control reference names no control, even beside a catalogue row
whose reference is empty."""

import pytest
from app.db.models import ControlItem
from app.services.control_refs import control_by_id_or_ref, control_by_ref
from sqlalchemy import event

FRAMEWORK = "blank-refs"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_blank_reference_names_no_control_and_reads_nothing(contract_db, value):
    db = contract_db
    db.add(ControlItem(framework_slug=FRAMEWORK, type="control", ref=""))
    db.commit()
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        assert control_by_ref(db, FRAMEWORK, value) is None
        assert control_by_ref(db, FRAMEWORK, value, ignore_case=True) is None
        assert control_by_id_or_ref(db, FRAMEWORK, value) is None
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)
    assert statements == []
