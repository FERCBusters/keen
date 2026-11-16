"""A risk links to the clauses of a framework as it does to its controls.

Requests use real password sign-in, CSRF and permission resolution against
persisted PostgreSQL state.
"""

import csv
import io
import uuid

from app.db.models import (
    EntityChangelog,
    Event,
    FrameworkClause,
    Risk,
    RiskClauseLink,
)
from app.services.control_refs import clause_by_id_or_ref

# Imported fixtures: the assembled application and its risk managers.
from tests.test_assembled_authority_regressions import keen  # noqa: F401
from tests.test_risk_api_workflows import (
    _control,
    _create,
    _risk_body,
    reg,  # noqa: F401
)


def _clause(db, ref, *, framework="A", title=None, sort_order=0):
    row = FrameworkClause(
        framework_slug=framework,
        ref=ref,
        title=title or f"Clause {ref}",
        sort_order=sort_order,
    )
    db.add(row)
    db.commit()
    return row


def _refs(risk):
    return [clause["ref"] for clause in risk["clauses"]]


def test_a_clause_is_found_by_id_or_by_reference_in_its_own_framework(reg):  # noqa: F811
    db = reg.db
    planning = _clause(db, "6.1")
    other = _clause(db, "6.1", framework="B")
    assert clause_by_id_or_ref(db, "A", str(planning.id)).id == planning.id
    assert clause_by_id_or_ref(db, "A", " 6.1 ").id == planning.id
    assert clause_by_id_or_ref(db, "B", "6.1").id == other.id
    # Another framework's clause, an unknown reference and nothing at all.
    assert clause_by_id_or_ref(db, "A", str(other.id)) is None
    assert clause_by_id_or_ref(db, "A", "9.9") is None
    assert clause_by_id_or_ref(db, "A", None) is None


def test_a_risk_is_created_with_clauses_named_by_reference_or_id(reg):  # noqa: F811
    db = reg.db
    context = _clause(db, "4.1", sort_order=1)
    planning = _clause(db, "6.1", sort_order=2)
    control = _control(db, "A.5.1")
    risk = _create(
        reg,
        controls=["A.5.1"],
        # A reference in any case, an id, and the same clause twice.
        clauses=["6.1", str(context.id), " 6.1 "],
    )
    assert _refs(risk) == ["4.1", "6.1"]
    assert risk["clause_count"] == 2
    assert risk["clauses"][0] == {
        "id": str(context.id),
        "framework": "A",
        "ref": "4.1",
        "title": "Clause 4.1",
        "parent_id": None,
        "parent_ref": None,
        "sort_order": 1,
    }
    assert [c["id"] for c in risk["controls"]] == [str(control.id)]
    assert {link.clause_id for link in db.query(RiskClauseLink).all()} == {
        context.id,
        planning.id,
    }

    # The history of the new risk records its clauses.
    created = (
        db.query(EntityChangelog)
        .filter_by(entity_type="risk", entity_id=uuid.UUID(risk["id"]))
        .one()
    )
    assert [c["ref"] for c in created.after_state["clauses"]] == ["4.1", "6.1"]


def test_unknown_clauses_and_other_frameworks_clauses_are_refused(reg):  # noqa: F811
    db = reg.db
    foreign = _clause(db, "7.1", framework="B")
    for value in ("9.9", str(foreign.id), str(uuid.uuid4())):
        refused = reg.manage(
            "POST", "/v1/risks", _risk_body(clauses=[value]), status=400
        )
        assert refused["detail"] == f"Unknown clause for A: {value}"
    assert db.query(Risk).count() == 0
    assert db.query(RiskClauseLink).count() == 0


def test_editing_replaces_keeps_or_clears_a_risks_clauses(reg):  # noqa: F811
    db = reg.db
    _clause(db, "4.1", sort_order=1)
    _clause(db, "6.1", sort_order=2)
    _clause(db, "8.1", framework="B")
    _control(db, "A.5.1")
    risk = _create(reg, clauses=["4.1"])
    path = f"/v1/risks/{risk['id']}"

    # A request that names no clauses keeps them.
    kept = reg.manage("PATCH", path, {"framework": "A", "note": "Reviewed"})
    assert _refs(kept) == ["4.1"]

    # The same risk holds clauses in another framework, which an edit leaves alone.
    reg.manage("PATCH", path, {"framework": "B", "clauses": ["8.1"]})
    replaced = reg.manage("PATCH", path, {"framework": "A", "clauses": ["6.1"]})
    assert _refs(replaced) == ["6.1"]
    assert _refs(reg.read("GET", path, params={"framework": "B"})) == ["8.1"]

    # Controls and clauses change together in one request.
    both = reg.manage(
        "PATCH", path, {"framework": "A", "clauses": ["4.1"], "controls": ["A.5.1"]}
    )
    assert _refs(both) == ["4.1"] and both["control_count"] == 1

    cleared = reg.manage("PATCH", path, {"framework": "A", "clauses": []})
    assert cleared["clauses"] == [] and cleared["clause_count"] == 0
    assert [link.framework_slug for link in db.query(RiskClauseLink).all()] == ["B"]

    changed = [
        [change["field"] for change in row.changes]
        for row in db.query(EntityChangelog)
        .filter_by(entity_type="risk", action="updated")
        .order_by(EntityChangelog.changed_at)
        .all()
    ]
    assert ["clauses"] in changed

    # A refused clause leaves the risk as it was.
    reg.manage("PATCH", path, {"framework": "A", "clauses": ["9.9"]}, status=400)
    assert reg.read("GET", path, params={"framework": "A"})["clauses"] == []


def test_linking_clauses_alone_is_written_to_the_diary(reg):  # noqa: F811
    db = reg.db
    _clause(db, "4.1")
    risk = _create(reg)
    before = db.query(Event).count()
    reg.manage(
        "PATCH", f"/v1/risks/{risk['id']}", {"framework": "A", "clauses": ["4.1"]}
    )
    entries = db.query(Event).order_by(Event.timestamp.desc()).all()
    assert len(entries) == before + 1
    assert "4.1" in str(entries[0].normalized_payload)


def test_deleting_a_risk_removes_its_clause_links(reg):  # noqa: F811
    db = reg.db
    _clause(db, "4.1")
    risk = _create(reg, clauses=["4.1"])
    reg.manage("DELETE", f"/v1/risks/{risk['id']}")
    assert db.query(RiskClauseLink).count() == 0
    assert db.query(FrameworkClause).count() == 1


def test_a_clause_lists_the_risks_linked_to_it(reg):  # noqa: F811
    db = reg.db
    context = _clause(db, "4.1")
    unlinked = _clause(db, "6.1")
    low = _create(reg, clauses=["4.1"], register_likelihood=1, register_impact=1)
    high = _create(
        reg,
        asset_name="Payroll server",
        clauses=["4.1"],
        register_likelihood=5,
        register_impact=5,
    )
    _create(reg, asset_name="Laptops")

    listed = reg.read("GET", f"/v1/clauses/{context.id}/risks")
    assert listed["framework"] == "A"
    assert listed["clause"]["ref"] == "4.1"
    # The highest score first.
    assert [item["id"] for item in listed["items"]] == [high["id"], low["id"]]
    assert reg.read("GET", f"/v1/clauses/{unlinked.id}/risks")["items"] == []

    assert reg.read("GET", "/v1/clauses/not-a-uuid/risks", status=400) == {
        "detail": "clause_id must be a UUID"
    }
    assert reg.read("GET", f"/v1/clauses/{uuid.uuid4()}/risks", status=404) == {
        "detail": "Clause not found"
    }
    # Reading the risks of a clause needs the permission that reads risks.
    path = f"/v1/clauses/{context.id}/risks"
    assert reg.keen.client().get(path).status_code == 401
    assert reg.keen.client(reg.keen.user()).get(path).status_code == 403


def test_an_import_links_each_risk_to_its_clauses(reg):  # noqa: F811
    db = reg.db
    _clause(db, "4.1")
    row = _risk_body(clauses=["4.1"])
    row.pop("framework")
    saved = reg.manage(
        "POST",
        "/v1/risks/import",
        {"rows": [row], "dry_run": False},
        params={"framework": "A"},
    )
    assert saved["rows"][0]["clauses"] == ["4.1"]
    assert db.query(RiskClauseLink).count() == 1

    refused = reg.manage(
        "POST",
        "/v1/risks/import",
        {"rows": [row | {"clauses": ["9.9"]}], "dry_run": False},
        params={"framework": "A"},
    )
    assert [r["ok"] for r in refused["rows"]] == [False]
    assert db.query(RiskClauseLink).count() == 1


def test_the_register_csv_carries_clauses_out_and_back_in(reg):  # noqa: F811
    db = reg.db
    _clause(db, "4.1", sort_order=1)
    _clause(db, "6.1", sort_order=2)
    _control(db, "A.5.1")
    _create(reg, clauses=["6.1", "4.1"], controls=["A.5.1"])

    exported = reg.keen.client(reg.reader).get(
        "/v1/risks/register/export.csv", params={"framework": "A"}
    )
    assert exported.status_code == 200, exported.text
    (row,) = csv.DictReader(io.StringIO(exported.text))
    assert row["clauses"] == "4.1;6.1" and row["controls"] == "A.5.1"

    def upload(text, status=200):
        return reg.manage(
            "POST",
            "/v1/risks/register/import.csv",
            status=status,
            params={"framework": "A"},
            files={"file": ("risks.csv", text.encode(), "text/csv")},
        )

    header = "asset,category,subcategory,threat_summary,risk_types,controls,clauses"
    # Clauses alone, controls alone, and a row that names neither.
    upload(
        "\n".join(
            [
                header,
                "Mail,Data,Mailboxes,Phishing,Integrity,, 4.1 ; 6.1 ",
                "Wiki,Data,Pages,Defacement,Integrity,A.5.1,",
                "Printer,Kit,Printers,Jam,Availability,,",
            ]
        )
    )
    by_asset = {
        risk.asset.name: sorted(link.clause.ref for link in risk.clause_links)
        for risk in db.query(Risk).all()
    }
    assert by_asset["Mail"] == ["4.1", "6.1"]
    assert by_asset["Wiki"] == [] and by_asset["Printer"] == []

    # An unknown clause refuses the whole file.
    count = db.query(Risk).count()
    upload(
        "\n".join([header, "Fax,Kit,Fax,Misdial,Integrity,,9.9"]),
        status=400,
    )
    assert db.query(Risk).count() == count
