"""Callers that name a control by reference get that control.

Framework clauses are also kept in the control catalogue as rows of type
"clause". Linking, scoping and filtering by a control reference skip those
rows, so a clause and a control that share a reference stay distinct. A
reference that two controls share is refused with a request for the id.
Requests go through the assembled application as an administrator.
"""

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.db.models import (
    Audit,
    AuditFinding,
    AuditScopedControl,
    ControlClauseLink,
    ControlItem,
    Event,
    Framework,
    FrameworkClause,
    InterestedParty,
    IsmsObjective,
    Mapping,
    Risk,
)

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

HOSTILE = '<img src=x onerror="alert(1)">'
AMBIGUOUS = "More than one control in A has the reference 7.1; use the control id"


def _send(client, method, path, body=None, status=200, **params):
    response = client.request(
        method,
        path,
        params=params,
        headers=unsafe(client) if method != "GET" else {},
        **({"json": body} if body is not None else {}),
    )
    assert response.status_code == status, response.text
    return response.json() if response.content else None


@pytest.fixture
def catalogue(keen):  # noqa: F811 - fixture imported above
    db = keen.db
    db.add(Framework(slug="A"))
    rows = {
        "clause_row": ControlItem(
            framework_slug="A", type="clause", ref="5.1", title="Leadership"
        ),
        "control": ControlItem(
            framework_slug="A", type="annex_control", ref="5.1", title="Policies"
        ),
        "clause_only": ControlItem(
            framework_slug="A", type="clause", ref="4.1", title="Context"
        ),
        "annex_7": ControlItem(
            framework_slug="A", type="annex_control", ref="7.1", title="Perimeters"
        ),
        "custom_7": ControlItem(
            framework_slug="A", type="custom", ref="7.1", title=HOSTILE
        ),
    }
    clause = FrameworkClause(framework_slug="A", ref="5.1", title="Leadership")
    db.add_all([*rows.values(), clause])
    db.commit()
    admin = keen.client(keen.user(role="admin"))
    return SimpleNamespace(
        db=db,
        rows=rows,
        ids={name: str(row.id) for name, row in rows.items()},
        clause=clause,
        call=lambda *args, **kwargs: _send(admin, *args, **kwargs),
    )


def _risk(catalogue, controls, status=200):
    body = {
        "asset_name": "Payroll database",
        "category_name": "Data",
        "subcategory_name": "Databases",
        "risk_types": ["Confidentiality"],
        "threat_summary": "Unauthorised export of salary records",
        "register_likelihood": 3,
        "register_impact": 4,
        "register_residual_likelihood": 2,
        "register_residual_impact": 2,
        "framework": "A",
        "controls": controls,
    }
    return catalogue.call("POST", "/v1/risks", body, status=status)


def _objective(catalogue, controls, status=200):
    body = {"requirement": "Protect records", "goal": "Annual", "controls": controls}
    return catalogue.call(
        "POST", "/v1/isms/objectives", body, status=status, framework="A"
    )


def _party(catalogue, controls, status=200):
    body = {
        "framework": "A",
        "name": "Customers",
        "nature": "Data subjects",
        "controls": controls,
    }
    return catalogue.call("POST", "/v1/interested-parties", body, status=status)


def _audit(catalogue, controls, status=200):
    body = {"title": "Access review", "framework_slug": "A", "controls": controls}
    return catalogue.call("POST", "/v1/audits", body, status=status)


def _scoped_control_ids(db, audit_id):
    db.expire_all()
    rows = db.query(AuditScopedControl).filter_by(audit_id=uuid.UUID(audit_id)).all()
    return sorted(str(row.control_item_id) for row in rows)


def _event(db, summary, control):
    row = Event(
        source="jenkins",
        system="ci",
        action="deploy",
        outcome="success",
        severity=2,
        summary=summary,
        timestamp=datetime(2026, 3, 1, 12, 0),
        external_id=uuid.uuid4().hex,
        raw_pointer={},
        normalized_payload={},
    )
    db.add(row)
    db.flush()
    db.add(Mapping(event_id=row.id, control_item_id=control.id, method="rule"))
    db.commit()
    return row


def test_risks_link_the_control_that_shares_a_reference_with_a_clause(catalogue):
    control = catalogue.ids["control"]
    created = _risk(catalogue, ["5.1"])
    assert [c["id"] for c in created["controls"]] == [control]
    replaced = catalogue.call(
        "PATCH",
        f"/v1/risks/{created['id']}/controls",
        {"controls": [" 5.1 "]},
        framework="A",
    )
    assert [c["id"] for c in replaced["controls"]] == [control]


def test_isms_records_and_parties_link_the_control_by_reference(catalogue):
    control = catalogue.ids["control"]
    objective = _objective(catalogue, ["5.1"])
    assert [c["id"] for c in objective["controls"]] == [control]
    # These registers match references without regard to case.
    party = _party(catalogue, ["5.1"])
    assert [c["id"] for c in party["controls"]] == [control]
    relinked = catalogue.call(
        "PATCH",
        f"/v1/interested-parties/{party['id']}/controls",
        {"controls": ["5.1"]},
    )
    assert [c["id"] for c in relinked["controls"]] == [control]


def test_audit_scope_and_findings_use_the_control_by_reference(catalogue):
    control = catalogue.ids["control"]
    audit = _audit(catalogue, ["5.1"])
    assert _scoped_control_ids(catalogue.db, audit["id"]) == [control]
    catalogue.call("PUT", f"/v1/audits/{audit['id']}/controls", {"controls": ["5.1"]})
    assert _scoped_control_ids(catalogue.db, audit["id"]) == [control]
    finding = catalogue.call(
        "POST",
        f"/v1/audits/{audit['id']}/findings",
        {"kind": "ofi", "title": "Policy review overdue", "control": "5.1"},
    )
    catalogue.db.expire_all()
    stored = catalogue.db.get(AuditFinding, uuid.UUID(finding["id"]))
    assert str(stored.control_item_id) == control


def test_clause_control_links_use_the_control_by_reference(catalogue):
    clause = catalogue.clause
    catalogue.call(
        "PATCH",
        f"/v1/admin/clauses/{clause.id}/controls",
        {"controls": [{"ref": "5.1", "applicability": "applicable"}]},
    )
    catalogue.db.expire_all()
    links = catalogue.db.query(ControlClauseLink).filter_by(clause_id=clause.id).all()
    assert [str(link.control_item_id) for link in links] == [catalogue.ids["control"]]


def test_event_filters_by_control_reference_skip_the_clause_row(catalogue):
    rows = catalogue.rows
    kept = _event(catalogue.db, "Policy published", rows["control"])
    _event(catalogue.db, "Leadership review", rows["clause_row"])

    listed = catalogue.call("GET", "/v1/events", framework="A", control="5.1")
    assert [item["id"] for item in listed["items"]] == [str(kept.id)]
    facets = catalogue.call("GET", "/v1/events/facets", framework="A", control="5.1")
    assert [row["count"] for row in facets["sources"]] == [1]
    exported = catalogue.call(
        "GET", "/v1/events/export", framework="A", control="5.1", format="json"
    )
    assert [item["id"] for item in exported["items"]] == [str(kept.id)]

    # A reference that only a clause row carries matches no events.
    none = catalogue.call("GET", "/v1/events", framework="A", control="4.1")
    assert none["items"] == []


@pytest.mark.parametrize(
    "create,detail",
    [
        (_risk, "Unknown control for A: 4.1"),
        (_objective, "Unknown control: 4.1"),
        (_party, "Unknown control: 4.1"),
    ],
)
def test_a_reference_that_only_a_clause_carries_is_an_unknown_control(
    catalogue, create, detail
):
    assert create(catalogue, ["4.1"], status=400)["detail"] == detail


def test_audits_and_clause_links_refuse_a_clause_only_reference(catalogue):
    response = _audit(catalogue, ["4.1"], status=404)
    assert response["detail"] == "Control not found: 4.1"
    catalogue.db.expire_all()
    assert catalogue.db.query(Audit).count() == 0
    response = catalogue.call(
        "PATCH",
        f"/v1/admin/clauses/{catalogue.clause.id}/controls",
        {"controls": [{"ref": "4.1", "applicability": "applicable"}]},
        status=400,
    )
    assert response["detail"] == "Unknown control for framework"


@pytest.mark.parametrize(
    "create,model",
    [
        (_risk, Risk),
        (_objective, IsmsObjective),
        (_party, InterestedParty),
        (_audit, Audit),
    ],
)
def test_a_reference_two_controls_share_asks_for_the_control_id(
    catalogue, create, model
):
    assert create(catalogue, ["7.1"], status=400)["detail"] == AMBIGUOUS
    catalogue.db.expire_all()
    assert catalogue.db.query(model).count() == 0

    # The id names exactly one of them.
    chosen = catalogue.ids["custom_7"]
    created = create(catalogue, [chosen])
    if model is Audit:
        assert _scoped_control_ids(catalogue.db, created["id"]) == [chosen]
    else:
        assert [c["id"] for c in created["controls"]] == [chosen]
        assert created["controls"][0]["title"] == HOSTILE


def test_ambiguous_references_are_refused_by_every_other_lookup(catalogue):
    audit = _audit(catalogue, [])
    response = catalogue.call(
        "POST",
        f"/v1/audits/{audit['id']}/findings",
        {"kind": "ofi", "title": "Perimeter", "control": "7.1"},
        status=400,
    )
    assert response["detail"] == AMBIGUOUS
    response = catalogue.call(
        "PATCH",
        f"/v1/admin/clauses/{catalogue.clause.id}/controls",
        {"controls": [{"ref": "7.1", "applicability": "applicable"}]},
        status=400,
    )
    assert response["detail"] == AMBIGUOUS
    for path in ("/v1/events", "/v1/events/facets", "/v1/events/export"):
        response = catalogue.call("GET", path, status=400, framework="A", control="7.1")
        assert response["detail"] == AMBIGUOUS


def test_case_insensitive_registers_prefer_the_exact_reference(catalogue):
    db = catalogue.db
    upper = ControlItem(framework_slug="A", type="custom", ref="X.1", title="Upper")
    lower = ControlItem(framework_slug="A", type="custom", ref="x.1", title="Lower")
    mixed = ControlItem(framework_slug="A", type="custom", ref="Y.a", title="Mixed")
    other = ControlItem(framework_slug="A", type="custom", ref="y.A", title="Other")
    db.add_all([upper, lower, mixed, other])
    db.commit()

    party = _party(catalogue, ["x.1", "X.1"])
    assert sorted(c["id"] for c in party["controls"]) == sorted(
        [str(upper.id), str(lower.id)]
    )
    objective = _objective(catalogue, ["x.1"])
    assert [c["id"] for c in objective["controls"]] == [str(lower.id)]
    # Neither spelling matches exactly, so the reference names two controls.
    response = _objective(catalogue, ["Y.A"], status=400)
    assert response["detail"].startswith("More than one control in A has")
