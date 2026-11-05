"""How automatic link diary entries name the references and records they cover.

Covers record types the diary does not know, the cap on references listed in
an entry, clauses stored without a reference, and the page every kind of
linked record opens from its entry.
"""

import uuid

import pytest
from app.db.models import (
    ControlItem,
    Event,
    FrameworkClause,
    IsmsEntityClauseLink,
    IsmsEntityControlLink,
)
from app.services.isms import link_diary
from app.services.isms.link_diary import (
    remove_entity_link_diary,
    sync_entity_link_diary,
)

# Imported fixtures: a catalogue for framework A with in-memory artifact storage.
from tests.test_isms_link_diary import (
    FW,
    _automatic,
    _document,
    _entry,
    _user,
    db,  # noqa: F401
    storage,  # noqa: F401
)


def test_link_entries_refuse_record_types_they_do_not_know(db):  # noqa: F811
    for change in (sync_entity_link_diary, remove_entity_link_diary):
        with pytest.raises(
            ValueError, match="^Unsupported linked record type: ledger$"
        ):
            change(db, "ledger", uuid.uuid4())
    assert db.query(Event).filter(Event.source == "diary").count() == 0


def test_an_entry_lists_a_hundred_references_and_counts_the_rest(db):  # noqa: F811
    editor = _user(db, "editor")
    refs = [f"Z.{n:03d}" for n in range(101)]
    controls = [
        ControlItem(framework_slug=FW, ref=ref, type="annex_control") for ref in refs
    ]
    document = _document(db)
    db.add_all(controls)
    db.flush()
    db.add_all(
        IsmsEntityControlLink(
            entity_type="document",
            entity_id=document.id,
            framework_slug=FW,
            control_item_id=control.id,
        )
        for control in controls
    )
    db.flush()

    sync_entity_link_diary(db, "document", document.id, editor)

    entry = _entry(db, "document", document.id)
    assert entry.normalized_payload["details"]["linked"] == {
        FW: ", ".join(refs[:100]) + " (+1 more)"
    }
    # Every link still counts as evidence, listed or not.
    assert set(_automatic(db, entry.id)) == set(refs)


def test_a_clause_without_a_reference_maps_no_catalogue_item(db):  # noqa: F811
    editor = _user(db, "editor")
    unnamed = FrameworkClause(framework_slug=FW, ref="", title="Unnamed", sort_order=2)
    # A clause-kind catalogue item that also lacks a reference.
    db.add_all([unnamed, ControlItem(framework_slug=FW, ref="", type="clause")])
    document = _document(db)
    db.flush()
    db.add(
        IsmsEntityClauseLink(
            entity_type="document",
            entity_id=document.id,
            framework_slug=FW,
            clause_id=unnamed.id,
        )
    )
    db.flush()

    sync_entity_link_diary(db, "document", document.id, editor)

    entry = _entry(db, "document", document.id)
    assert entry.normalized_payload["details"]["linked"] == {FW: ""}
    assert _automatic(db, entry.id) == {}


def test_every_linked_record_type_opens_a_page_from_its_entry():
    record = uuid.uuid4()
    for entity_type, label in link_diary._LABELS.items():
        payload = link_diary._entry_payload(entity_type, record, {})
        (link,) = payload["links"]
        assert link["text"] == label, entity_type
        assert link["url"].startswith("/") and ".html?" in link["url"], entity_type
        if entity_type != "org_node":
            assert link["url"].endswith(f"id={record}"), entity_type
