"""Pages of events never hold every event's stored payload at once.

An event's payloads hold its evidence, up to a webhook delivery's ten
megabytes, and decoded they cost several times their text. The event list,
the export, the rule preview, field samples and the rule backfill read a page
of events without their payloads, then read the payloads a bounded group at a
time, so a page of large evidence costs about one group, not the page.
"""

import tracemalloc
import uuid
from datetime import datetime, timedelta

import pytest
from app.db.models import Event
from app.services import event_payloads, events
from sqlalchemy import event as sa_event

# About a megabyte of stored text that decodes to many small values.
LARGE = {"values": list(range(150_000)), "url": "https://ci.example/run/1"}


@pytest.fixture
def stored(contract_db):
    base = datetime(2026, 11, 1, 9, 0)
    rows = [
        Event(
            id=uuid.uuid4(),
            timestamp=base + timedelta(minutes=n),
            source="webhook:enroll" if n == 0 else "webhook:ci",
            system="ci",
            summary=f"Delivery {n}",
            raw_pointer={"webhook": {"provider": "ci"}},
            normalized_payload=(
                {"new": {"host": "web-01"}} if n == 0 else {**LARGE, "n": n}
            ),
            external_id=f"payload-{n}",
        )
        for n in range(12)
    ]
    contract_db.add_all(rows)
    contract_db.flush()
    contract_db.expunge_all()
    return [row.id for row in rows]


def light(db, ids):
    return (
        db.query(Event)
        .options(*event_payloads.without_payloads())
        .filter(Event.id.in_(ids))
        .order_by(Event.timestamp)
        .all()
    )


def test_payloads_come_in_order_in_groups_within_the_read_size(
    contract_db, stored, monkeypatch
):
    statements = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    engine = contract_db.get_bind().engine
    sa_event.listen(engine, "before_cursor_execute", count)
    try:
        found = list(event_payloads.payloads(contract_db, list(reversed(stored))))
    finally:
        sa_event.remove(engine, "before_cursor_execute", count)
    assert [event_id for event_id, _, _ in found] == list(reversed(stored))
    assert found[-1][2] == {"new": {"host": "web-01"}}
    # One statement measures them all. Each large payload passes the read
    # size, so each is read on its own, and so is the small one after them.
    assert len(statements) == 1 + 12
    monkeypatch.setattr(event_payloads, "PAYLOAD_TEXT_PER_READ", 64 * 1024 * 1024)
    statements.clear()
    sa_event.listen(engine, "before_cursor_execute", count)
    try:
        assert len(list(event_payloads.payloads(contract_db, stored))) == 12
    finally:
        sa_event.remove(engine, "before_cursor_execute", count)
    assert len(statements) == 2
    assert list(event_payloads.payloads(contract_db, [])) == []
    # An event removed meanwhile is left out.
    assert list(event_payloads.payloads(contract_db, [uuid.uuid4()])) == []


def test_a_page_of_large_evidence_holds_about_one_payload(contract_db, stored):
    page = light(contract_db, stored)
    tracemalloc.start()
    try:
        links = events._links(contract_db, page)
        bounded = tracemalloc.get_traced_memory()[1]
        tracemalloc.reset_peak()
        whole = contract_db.query(Event).filter(Event.id.in_(stored)).all()
        hydrated = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    del whole
    assert links[stored[0]] == (None, "web-01")
    assert links[stored[1]] == ("https://ci.example/run/1", "ci")
    # Read whole, the page holds every payload at once.
    assert bounded * 4 < hydrated
