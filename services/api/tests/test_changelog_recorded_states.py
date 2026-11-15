"""What the entity changelog records as a record's state.

A stored clause can carry evidence links in its metadata that were never
checked as an administrator's edit would be; the recorded state lists only
the links that name an address. A PESTLE(E) item's state reads the same from
its loaded row as from its identifier.
"""

import uuid

from app.db.models import (
    FrameworkClause,
    PestleItem,
    PestleRelevanceLevel,
)
from app.services.entity_changelog import (
    clause_changelog_state,
    pestle_item_changelog_state,
)


def test_a_clauses_state_lists_only_evidence_links_that_name_an_address(contract_db):
    clause = FrameworkClause(
        framework_slug="A",
        ref="9.1",
        title="Monitoring",
        meta={
            "evidence_mappings": [
                "https://not-an-object.example",
                {"title": "No address", "url": "  "},
                {"title": "", "url": "https://wiki.example/monitoring"},
                {"title": "Runbook", "url": "/events.html?q=monitoring"},
            ]
        },
    )
    contract_db.add(clause)
    contract_db.flush()

    state = clause_changelog_state(contract_db, clause)

    # Untitled links take their address as the title, sorted by title.
    assert state["evidence_mappings"] == [
        {"title": "Runbook", "url": "/events.html?q=monitoring"},
        {
            "title": "https://wiki.example/monitoring",
            "url": "https://wiki.example/monitoring",
        },
    ]


def test_a_pestle_items_state_reads_the_same_from_its_row(contract_db):
    level = PestleRelevanceLevel(code="high", label="High", sort_order=1)
    contract_db.add(level)
    contract_db.flush()
    item = PestleItem(
        framework_slug="A",
        type="Legal",
        lens="External",
        item="Privacy law reform",
        overall_relevance_id=level.id,
    )
    contract_db.add(item)
    contract_db.flush()

    from_row = pestle_item_changelog_state(contract_db, item)

    assert from_row == pestle_item_changelog_state(contract_db, item.id)
    assert from_row == pestle_item_changelog_state(contract_db, str(item.id))
    assert (from_row["id"], from_row["item"]) == (str(item.id), "Privacy law reform")
    assert pestle_item_changelog_state(contract_db, uuid.uuid4()) is None
