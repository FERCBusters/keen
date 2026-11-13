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
from sqlalchemy.exc import DataError, IntegrityError


@pytest.fixture
def pg(contract_db, contract_postgres_engine):
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


@pytest.mark.parametrize(
    "hostile",
    [float("inf"), float("-inf"), float("nan"), "lone \ud800 surrogate", "nul \x00"],
    ids=["inf", "-inf", "nan", "surrogate", "nul"],
)
def test_every_collected_value_reaches_postgresql_in_a_form_it_can_hold(
    pg, written, hostile
):
    # Shared storage is the one step every collector, webhook and agent passes
    # through, so a value PostgreSQL text or JSONB refuses is made storable
    # here: in the payload, a dictionary key, the pointer and the text columns.
    text = hostile if isinstance(hostile, str) else "plain"
    result = common.store_event_with_artifact(
        pg.db,
        timestamp=datetime(2026, 10, 1, 9, 0),
        source="race-test",
        system=text,
        actor=text,
        action=text,
        outcome=text,
        severity=2,
        summary=text,
        raw_pointer={"pointer": [hostile]},
        normalized_payload={"value": hostile, text: {"nested": (hostile, 1.5)}},
        external_id="odd-" + text,
        artifact_kind="json",
        artifact_bytes=b"{}",
        artifact_content_type="application/json",
        artifact_key="race/odd.json",
        captured_by="test",
        commit=False,
    )
    pg.db.flush()
    pg.db.expire_all()

    stored = pg.db.get(Event, uuid.UUID(result["event_id"]))
    expected = {
        float("inf"): "inf",
        float("-inf"): "-inf",
        "lone \ud800 surrogate": "lone \ufffd surrogate",
        "nul \x00": "nul \ufffd",
    }.get(hostile, "nan")
    kept = expected if isinstance(hostile, str) else "plain"
    assert stored.raw_pointer == {"pointer": [expected]}
    assert stored.normalized_payload == {
        "value": expected,
        kept: {"nested": [expected, 1.5]},
    }
    assert (stored.system, stored.actor, stored.action, stored.outcome) == (kept,) * 4
    assert stored.summary == kept
    assert stored.external_id.endswith("odd-" + kept)


def test_storable_is_what_lets_postgresql_take_those_values(pg):
    # The values the test above stores are refused as they are written.
    for refused in (float("inf"), "lone \ud800 surrogate"):
        row = Event(
            timestamp=datetime(2026, 10, 1, 9, 0),
            source="race-test",
            summary="as written",
            raw_pointer={},
            normalized_payload={"value": refused},
            external_id=uuid.uuid4().hex,
        )
        with pytest.raises((DataError, UnicodeEncodeError)):
            with pg.db.begin_nested():
                pg.db.add(row)
                pg.db.flush()
        pg.db.expunge_all()


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


def test_an_events_mappings_are_stored_in_control_order(contract_db, monkeypatch):
    # Each mapping locks its control's evidence-count row until the transaction
    # ends. Two events stored at once for the same controls would each wait for
    # the other if their mappings went in different orders, so every event's
    # go in control order, whatever order the rules name them in.
    contract_db.add_all([Framework(slug="RACE"), Framework(slug="RACE2")])
    controls = [
        ControlItem(
            id=uuid.UUID(int=number),
            framework_slug=slug,
            type="annex_control",
            ref=ref,
        )
        for number, slug, ref in (
            (3, "RACE", "A.1"),
            (1, "RACE", "A.2"),
            (2, "RACE2", "B.1"),
        )
    ]
    contract_db.add_all(controls)
    contract_db.flush()
    rules = parse_rules(
        {
            "rules": [
                {
                    "id": "several",
                    "when": {"source": "race-test"},
                    "map_to": [
                        {"framework": "RACE", "ref": "A.1"},
                        {"framework": "RACE2", "ref": "B.1"},
                        {"framework": "RACE", "ref": "A.2"},
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
    inserted = []

    def record(mapper, connection, mapping):
        inserted.append(mapping.control_item_id.int)

    event.listen(Mapping, "before_insert", record)
    try:
        assert common.apply_rules(contract_db, row) == 3
    finally:
        event.remove(Mapping, "before_insert", record)

    assert inserted == [1, 2, 3]
