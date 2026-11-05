"""Framework node edits for frameworks known only from their controls, clauses
saved again, and imported clause hierarchies, through the assembled app."""

from app.db.models import ControlItem, Framework, FrameworkClause

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_framework_api_workflows import _node
from tests.test_framework_api_workflows import admin as admin

BASE = "/v1/admin/frameworks/A/nodes"


def test_a_framework_known_only_from_its_controls_gains_its_row_on_edit(keen, admin):
    keen.db.add(ControlItem(framework_slug="legacy", type="annex_control", ref="L.1"))
    keen.db.commit()
    assert keen.db.query(Framework).filter_by(slug="legacy").count() == 0

    saved = admin(
        "PUT",
        "/v1/admin/frameworks/legacy/nodes/annex_control/L.2",
        _node(ref="L.2", title="Legacy logging"),
    )
    assert saved["created"] is True
    keen.db.expire_all()
    row = keen.db.query(Framework).filter_by(slug="legacy").one()
    assert (row.slug, row.name) == ("legacy", "legacy")
    refs = {
        c.ref for c in keen.db.query(ControlItem).filter_by(framework_slug="legacy")
    }
    assert refs == {"L.1", "L.2"}
    # A framework with its row keeps it, and gains no second one.
    admin(
        "PUT",
        "/v1/admin/frameworks/legacy/nodes/annex_control/L.3",
        _node(ref="L.3", title="Legacy alerts"),
    )
    assert keen.db.query(Framework).filter_by(slug="legacy").count() == 1


def test_saving_a_clause_again_updates_its_one_clause_record(keen, admin):
    admin("PUT", f"{BASE}/clause/4", _node("clause", "4", "Context", sort_order=1))
    admin("PUT", f"{BASE}/clause/5", _node("clause", "5", "Leadership"))
    again = admin(
        "PUT",
        f"{BASE}/clause/5",
        _node("clause", "5", "Leadership and commitment", parent_ref="4", sort_order=3),
    )
    assert again["created"] is False
    keen.db.expire_all()
    (clause,) = keen.db.query(FrameworkClause).filter_by(framework_slug="A", ref="5")
    assert (clause.title, clause.sort_order, clause.parent.ref) == (
        "Leadership and commitment",
        3,
        "4",
    )
    assert clause.meta["parent_ref"] == "4"


def test_an_imported_clause_with_child_clauses_cannot_be_deleted(keen, admin):
    # Imported catalogues record the clause hierarchy on the clause records;
    # their catalogue rows carry no parent reference.
    db = keen.db
    parent = FrameworkClause(framework_slug="A", ref="6", title="Planning")
    db.add(parent)
    db.flush()
    db.add(
        FrameworkClause(
            framework_slug="A", ref="6.1", title="Risks", parent_clause_id=parent.id
        )
    )
    db.add_all(
        [
            ControlItem(framework_slug="A", type="clause", ref="6", title="Planning"),
            ControlItem(framework_slug="A", type="clause", ref="6.1", title="Risks"),
        ]
    )
    db.commit()

    refused = admin("DELETE", f"{BASE}/clause/6", status=409)
    assert refused["detail"] == "Move or delete child clauses first"
    db.expire_all()
    assert {c.ref for c in db.query(FrameworkClause).filter_by(framework_slug="A")} == {
        "6",
        "6.1",
    }
    assert db.query(ControlItem).filter_by(framework_slug="A", ref="6").count() == 1
