"""What the entity changelog records as a record's state.

An imported clause can carry evidence links in its metadata that were never
checked as an administrator's edit would be; the recorded state lists only
the links that name an address. A PESTLE(E) item's state reads the same from
its loaded row as from its identifier.
"""

import uuid

from app.db.models import (
    EntityChangelog,
    FrameworkClause,
    PestleItem,
    PestleRelevanceLevel,
)
from app.services.entity_changelog import pestle_item_changelog_state

# Imported fixture: administrator requests through the assembled application.
from tests.test_admin_api_workflows import admin  # noqa: F401
from tests.test_assembled_authority_regressions import keen  # noqa: F401


def test_imported_clause_links_without_an_address_are_left_out_of_history(admin):  # noqa: F811
    imported = admin.call(
        "POST",
        "/v1/admin/controls/import",
        {
            "framework": "A",
            "items": [
                {
                    "type": "clause",
                    "ref": "9.1",
                    "title": "Monitoring",
                    "metadata": {
                        "evidence_mappings": [
                            "https://not-an-object.example",
                            {"title": "No address", "url": "  "},
                            {"title": "", "url": "https://wiki.example/monitoring"},
                            {"title": "Runbook", "url": "/events.html?q=monitoring"},
                        ]
                    },
                }
            ],
        },
    )
    assert imported["created"] == 1
    clause = (
        admin.db.query(FrameworkClause).filter_by(framework_slug="A", ref="9.1").one()
    )

    admin.call(
        "PATCH",
        f"/v1/admin/clauses/{clause.id}/evidence-urls",
        {"evidence_mappings": [{"title": "Dashboard", "url": "/dashboard.html"}]},
    )

    (entry,) = admin.db.query(EntityChangelog).filter_by(
        entity_type="clause", entity_id=clause.id
    )
    # Untitled links take their address as the title, sorted by title.
    assert entry.before_state["evidence_mappings"] == [
        {"title": "Runbook", "url": "/events.html?q=monitoring"},
        {
            "title": "https://wiki.example/monitoring",
            "url": "https://wiki.example/monitoring",
        },
    ]
    assert entry.after_state["evidence_mappings"] == [
        {"title": "Dashboard", "url": "/dashboard.html"}
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
