"""Control evidence listings and evidenced-control resolution at their edges.

A control's evidence list is counted and paged before its events are read, so
an event removed meanwhile (by evidence retention, for example) is left out of
the page. Resolving which controls have evidence stops before the crosswalk
when none of the framework's evidence is visible to the reader.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime

import pytest
from app.core.config import settings
from app.db.models import (
    ControlItem,
    CrossFrameworkControlLink,
    Event,
    Framework,
    ManagedConfiguration,
    Mapping,
    Permission,
    User,
)
from app.security.diary_visibility import (
    DIARY_SOURCE,
    LINK_ENTRY_SYSTEM,
    RISK_LINK_ENTRY_PREFIX,
    diary_filter_condition,
)
from app.services.control_inheritance import evidenced_control_ids
from app.services.controls import control_evidence
from sqlalchemy import event
from sqlalchemy.orm import Session

TGT, SRC = "edge-target", "edge-source"
DAY = datetime(2026, 3, 2, 9, 0)


@contextmanager
def removed_before(connection, starts, sql, params):
    """Run ``sql`` on the connection just before the first statement that
    starts with ``starts``, as a transaction committing meanwhile would."""
    seen = []

    def before(conn, cursor, statement, parameters, context, executemany):
        if not seen and statement.lstrip().startswith(starts):
            seen.append(statement)
            cursor.execute(sql, params)

    event.listen(connection, "before_cursor_execute", before)
    try:
        yield seen
    finally:
        event.remove(connection, "before_cursor_execute", before)


def _user(db, *codes, role="normal"):
    row = User(
        username="edge-" + uuid.uuid4().hex[:8],
        password_hash="unused",
        role=role,
        is_active=True,
    )
    db.add(row)
    for code in codes:
        permission = db.query(Permission).filter_by(code=code).one_or_none()
        row.permissions.append(permission or Permission(code=code, description=code))
    return row


def _event(db, name, *controls, **columns):
    row = Event(
        source=columns.pop("source", "ci"),
        summary=name,
        external_id=columns.pop("external_id", name + uuid.uuid4().hex[:6]),
        timestamp=columns.pop("timestamp", DAY),
        **columns,
    )
    db.add(row)
    db.flush()
    for control in controls:
        db.add(Mapping(event_id=row.id, control_item_id=control.id, method="rule"))
    return row


def test_an_event_removed_while_its_control_is_listed_is_left_out(contract_db):
    db = contract_db
    control = ControlItem(framework_slug=TGT, ref="C1", type="control")
    db.add(control)
    db.flush()
    kept = _event(db, "kept", control, timestamp=datetime(2026, 3, 1))
    removed = _event(db, "removed", control, timestamp=datetime(2026, 3, 2))
    admin = _user(db, role="admin")
    db.commit()

    with removed_before(
        db.get_bind(),
        "SELECT events.id",
        "DELETE FROM mappings WHERE event_id = %(id)s;"
        " DELETE FROM events WHERE id = %(id)s",
        {"id": str(removed.id)},
    ) as seen:
        page = control_evidence(
            control_id=str(control.id), limit=10, offset=0, db=db, user=admin
        )
    assert seen == [
        "SELECT events.id AS events_id, events.timestamp AS events_timestamp, events.connection_id AS events_connection_id, events.connection_name AS events_connection_name, events.source AS events_source, events.summary AS events_summary, events.raw_pointer AS events_raw_pointer, events.normalized_payload AS events_normalized_payload \nFROM events \nWHERE events.id IN (%(id_1_1)s::UUID, %(id_1_2)s::UUID)"
    ], "the events of the page were read after the count"
    # Both events were counted and paged; only the one still stored is listed.
    assert page["total"] == 2
    assert [item["event_id"] for item in page["items"]] == [str(kept.id)]
    assert page["items"][0]["summary"] == "kept"


@pytest.fixture
def hidden_evidence(contract_db, monkeypatch):
    """A target inheriting only a risk diary entry that events readers cannot see."""
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    db = contract_db
    db.add_all([Framework(slug=TGT), Framework(slug=SRC)])
    db.add(
        ManagedConfiguration(
            name="organisation-frameworks",
            version=1,
            document={"enabled": [TGT, SRC], "default": TGT},
        )
    )
    target = ControlItem(framework_slug=TGT, ref="T1", type="control")
    source = ControlItem(framework_slug=SRC, ref="S1", type="control")
    db.add_all([target, source])
    db.flush()
    db.add(
        CrossFrameworkControlLink(
            source_control_id=source.id, target_control_id=target.id
        )
    )
    _event(
        db,
        "risk entry",
        source,
        source=DIARY_SOURCE,
        system=LINK_ENTRY_SYSTEM,
        external_id=f"{RISK_LINK_ENTRY_PREFIX}{uuid.uuid4()}",
    )
    reader = _user(db, "events.read")
    db.commit()
    return db, target, reader


def test_controls_without_visible_evidence_resolve_no_crosswalk(hidden_evidence):
    db, target, reader = hidden_evidence
    visibility = diary_filter_condition(db, reader)
    # A session with a pending change resolves the crosswalk afresh on every
    # read, so any crosswalk read after the evidence probe would show here.
    session = Session(
        bind=db.get_bind(), autoflush=False, join_transaction_mode="create_savepoint"
    )
    session.add(Framework(slug="pending-" + uuid.uuid4().hex[:6]))
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        assert evidenced_control_ids(session, TGT, visibility) == set()
        probe = len(statements)
        # Every event visible: the inherited entry evidences the target.
        assert evidenced_control_ids(session, TGT, True) == {target.id}
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)
        session.rollback()
        session.close()
    # Nothing is read after the evidence probe finds no visible evidence ...
    assert "EXISTS" in statements[probe - 1], statements[probe - 1]
    # ... while visible evidence goes on to resolve the crosswalk targets.
    visible = statements[probe:]
    after = next(n for n, s in enumerate(visible) if "EXISTS" in s)
    assert any("cross_framework_control_links" in s for s in visible[after + 1 :])
