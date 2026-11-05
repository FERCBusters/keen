"""Shared event storage when another worker stores the same event concurrently.

A second collection worker can insert the same upstream event between this
worker's duplicate check and its insert. The insert's savepoint fails on the
(source, external ID) constraint; the stored event is then reported as a
duplicate, keeping the run's pending cursor. The competing worker writes on its
own connection to the contract database, as a separate worker process would.
Rule placeholders for one reference named twice are covered here too.
"""

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.db.models import Artifact, ControlItem, Event, Framework, Mapping
from app.ingest import common
from app.mapping.rules import parse_rules
from sqlalchemy import delete, event, insert
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def pg(contract_db, contract_postgres_engine):
    if contract_postgres_engine is None:
        pytest.skip("A competing worker needs its own PostgreSQL connection")
    return SimpleNamespace(db=contract_db, engine=contract_postgres_engine)


@pytest.fixture
def written(monkeypatch):
    objects = []

    def put_bytes(**kwargs):
        objects.append(kwargs["key"])
        return SimpleNamespace(
            uri="local://" + kwargs["key"], sha256="0" * 64, size_bytes=2
        )

    monkeypatch.setattr(common, "put_bytes", put_bytes)
    return objects


def store(db, external_id):
    return common.store_event_with_artifact(
        db,
        timestamp=datetime(2026, 10, 1, 9, 0),
        source="race-test",
        system="ci",
        actor=None,
        action="deploy",
        outcome="success",
        severity=2,
        summary="Release deployed",
        raw_pointer={},
        normalized_payload={},
        external_id=external_id,
        artifact_kind="json",
        artifact_bytes=b"{}",
        artifact_content_type="application/json",
        artifact_key="race/" + external_id + ".json",
        captured_by="test",
        commit=False,
    )


def competing_worker(engine, external_id):
    """Store the event on another connection, as a second worker would."""
    event_id = uuid.uuid4()
    with engine.begin() as other:
        other.execute(
            insert(Event.__table__).values(
                id=event_id,
                timestamp=datetime(2026, 10, 1, 9, 0),
                source="race-test",
                summary="Release deployed",
                raw_pointer={},
                normalized_payload={},
                external_id=external_id,
                created_at=datetime(2026, 10, 1, 9, 0),
            )
        )
    return event_id


def remove(engine, event_id):
    with engine.begin() as other:
        other.execute(delete(Event.__table__).where(Event.id == event_id))


def test_an_event_another_worker_stored_first_is_reported_as_a_duplicate(pg, written):
    external_id = uuid.uuid4().hex
    winner = []
    # The competitor commits after the duplicate check, as this insert flushes.
    event.listen(
        pg.db,
        "before_flush",
        lambda *a: winner.append(competing_worker(pg.engine, external_id)),
        once=True,
    )
    try:
        result = store(pg.db, external_id)
    finally:
        for event_id in winner:
            remove(pg.engine, event_id)

    assert result == {"ok": True, "deduped": True, "event_id": str(winner[0])}
    # This worker stores neither an artefact nor a second event.
    assert written == []
    assert pg.db.query(Artifact).count() == 0


def test_a_constraint_failure_without_a_stored_event_is_raised(pg, written):
    external_id = uuid.uuid4().hex
    winner = []
    event.listen(
        pg.db,
        "before_flush",
        lambda *a: winner.append(competing_worker(pg.engine, external_id)),
        once=True,
    )

    def competitor_gone(session, transaction):
        # The competing event is removed before this worker can read it back.
        if transaction.nested and winner:
            remove(pg.engine, winner.pop())

    event.listen(pg.db, "after_transaction_end", competitor_gone)
    try:
        with pytest.raises(IntegrityError, match="uq_event_source_external"):
            store(pg.db, external_id)
    finally:
        event.remove(pg.db, "after_transaction_end", competitor_gone)
        for event_id in winner:
            remove(pg.engine, event_id)

    assert written == []


def test_a_reference_two_targets_name_gets_one_placeholder_control(
    contract_db, monkeypatch
):
    contract_db.add(Framework(slug="RACE"))
    contract_db.flush()
    rules = parse_rules(
        {
            "rules": [
                {
                    "id": "twice",
                    "when": {"source": "race-test"},
                    "map_to": [
                        {"framework": "RACE", "ref": "9.7"},
                        {"framework": "RACE", "ref": "9.7", "kind": "control"},
                    ],
                }
            ]
        }
    )
    monkeypatch.setattr(common, "load_rules", lambda *a, **k: rules)
    row = Event(
        source="race-test",
        summary="Release deployed",
        timestamp=datetime(2026, 10, 1, 9, 0),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    contract_db.add(row)
    contract_db.flush()

    assert common.apply_rules(contract_db, row) == 1

    (placeholder,) = contract_db.query(ControlItem).filter_by(framework_slug="RACE")
    assert (placeholder.ref, placeholder.type, placeholder.title) == (
        "9.7",
        "annex_control",
        None,
    )
    assert [m.control_item_id for m in contract_db.query(Mapping)] == [placeholder.id]
