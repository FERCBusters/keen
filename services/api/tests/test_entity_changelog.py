"""The semantic change log: what it records, what it refuses and how it
describes entities that no longer exist."""

import uuid
from datetime import datetime

import pytest
from app.db.models import (
    EntityChangelog,
    PestleBusinessProcess,
    PestleBusinessProcessRelevance,
    PestleItem,
    PestleRelevanceLevel,
)
from app.services import entity_changelog as log


def test_an_unknown_entity_type_is_refused(contract_db):
    with pytest.raises(ValueError, match="Unsupported changelog entity_type: widget"):
        log.record_entity_changelog(
            contract_db,
            entity_type=" Widget ",
            entity_id=uuid.uuid4(),
            action="created",
            before=None,
            after={"title": "x"},
        )
    assert contract_db.query(EntityChangelog).count() == 0


@pytest.mark.parametrize("action", ["updated", "relinked"])
def test_a_save_that_changes_nothing_records_nothing(contract_db, action):
    # Blank strings and missing keys are the same value, so neither is a change.
    state = {"title": "Backups", "note": ""}
    entry = log.record_entity_changelog(
        contract_db,
        entity_type="risk",
        entity_id=str(uuid.uuid4()),
        action=action,
        before=state,
        after={"title": "Backups", "updated_at": "later"},
    )
    assert entry is None
    assert contract_db.query(EntityChangelog).count() == 0


def test_identifiers_and_times_are_stored_as_text(contract_db):
    owner = uuid.uuid4()
    when = datetime(2026, 10, 1, 9, 30)
    entry = log.record_entity_changelog(
        contract_db,
        entity_type="risk",
        entity_id=uuid.uuid4(),
        action="updated",
        before={"owner": None, "due": None, "tags": ["a"]},
        after={"owner": owner, "due": when, "tags": ("a", "b")},
    )
    assert entry.after_state == {
        "owner": str(owner),
        "due": "2026-10-01T09:30:00",
        "tags": ["a", "b"],
    }
    assert {c["field"] for c in entry.changes} == {"owner", "due", "tags"}


def test_long_titles_are_shortened_and_blank_ones_are_left_empty(contract_db):
    long_title = "word " * 200
    entry = log.record_entity_changelog(
        contract_db,
        entity_type="risk",
        entity_id=uuid.uuid4(),
        action="created",
        before=None,
        after={"title": long_title},
    )
    assert len(entry.entity_title) == 512
    assert entry.entity_title.endswith("…")
    assert entry.summary == "CIA Triad Risk created"

    blank = log.record_entity_changelog(
        contract_db,
        entity_type="risk",
        entity_id=uuid.uuid4(),
        action="created",
        before=None,
        after={"title": "   ", "note": "kept"},
    )
    assert blank.entity_title is None


def test_a_summary_names_the_first_four_changes_and_counts_the_rest(contract_db):
    before = {f"field_{n}": "old" for n in range(6)}
    after = {f"field_{n}": "new" for n in range(6)}
    entry = log.record_entity_changelog(
        contract_db,
        entity_type="control",
        entity_id=uuid.uuid4(),
        action="updated",
        before=before,
        after=after,
    )
    assert entry.summary == ("Updated Field 0, Field 1, Field 2, Field 3 and 2 more")


@pytest.mark.parametrize(
    "state",
    [
        log.control_changelog_state,
        log.clause_changelog_state,
        log.risk_changelog_state,
        log.pestle_item_changelog_state,
        log.pestle_business_process_changelog_state,
    ],
)
def test_the_state_of_a_missing_entity_is_empty(contract_db, state):
    assert state(contract_db, uuid.uuid4()) is None
    assert state(contract_db, str(uuid.uuid4())) is None


def test_a_business_process_state_lists_its_factors_in_order(contract_db):
    high = PestleRelevanceLevel(code="high", label="1 High", sort_order=1)
    low = PestleRelevanceLevel(code="low", label="3 Low", sort_order=3)
    contract_db.add_all([high, low])
    contract_db.flush()
    process = PestleBusinessProcess(name="Payroll run")
    legal = PestleItem(
        framework_slug="A",
        type="Legal",
        lens="External",
        item="Privacy law",
        overall_relevance_id=high.id,
    )
    economic = PestleItem(
        framework_slug="A",
        type="Economical",
        lens="External",
        item="Interest rates",
        overall_relevance_id=low.id,
    )
    contract_db.add_all([process, legal, economic])
    contract_db.flush()
    contract_db.add_all(
        [
            PestleBusinessProcessRelevance(
                pestle_item_id=legal.id,
                business_process_id=process.id,
                relevance_id=high.id,
            ),
            PestleBusinessProcessRelevance(
                pestle_item_id=economic.id,
                business_process_id=process.id,
                relevance_id=low.id,
            ),
        ]
    )
    contract_db.flush()
    contract_db.expire_all()

    state = log.pestle_business_process_changelog_state(contract_db, process.id)

    assert state["name"] == "Payroll run"
    assert [
        (link["type"], link["item"], link["relevance"])
        for link in state["business_processes"]
    ] == [
        ("Economical", "Interest rates", "3 Low"),
        ("Legal", "Privacy law", "1 High"),
    ]
    # The same state is produced from the record itself.
    assert (
        log.pestle_business_process_changelog_state(
            contract_db, contract_db.get(PestleBusinessProcess, process.id)
        )
        == state
    )
