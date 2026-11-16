"""The Statement of Applicability shows each control's clauses and each
clause's controls with their applicability, within the chosen framework."""

from app.db.models import (
    ControlClauseLink,
    ControlItem,
    Framework,
    FrameworkClause,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
)

from tests.test_assembled_authority_regressions import keen as keen

FW, OTHER = "soa-links", "soa-elsewhere"


def test_the_statement_shows_clause_links_and_their_applicability(keen):
    db = keen.db
    db.add_all([Framework(slug=FW), Framework(slug=OTHER)])
    logging = ControlItem(framework_slug=FW, type="annex_control", ref="A.8.15")
    backups = ControlItem(framework_slug=FW, type="annex_control", ref="A.8.13")
    unlinked = ControlItem(framework_slug=FW, type="annex_control", ref="A.8.1")
    foreign = ControlItem(framework_slug=OTHER, type="annex_control", ref="X.1")
    operation = FrameworkClause(framework_slug=FW, ref="8.1", title="Operation")
    monitoring = FrameworkClause(framework_slug=FW, ref="9.1", title="Monitoring")
    elsewhere = FrameworkClause(framework_slug=OTHER, ref="1", title="Elsewhere")
    db.add_all([logging, backups, unlinked, foreign, operation, monitoring, elsewhere])
    db.flush()
    db.add_all(
        [
            ControlClauseLink(
                control_item_id=logging.id,
                clause_id=operation.id,
                applicability="applicable",
            ),
            ControlClauseLink(
                control_item_id=logging.id,
                clause_id=monitoring.id,
                applicability="partially_applicable",
            ),
            ControlClauseLink(
                control_item_id=backups.id,
                clause_id=operation.id,
                applicability="applicable",
            ),
            ControlClauseLink(
                control_item_id=foreign.id,
                clause_id=elsewhere.id,
                applicability="applicable",
            ),
        ]
    )
    db.commit()
    reader = keen.client(keen.user())
    response = reader.get("/v1/statement-of-applicability", params={"framework": FW})
    assert response.status_code == 200, response.text
    body = response.json()

    controls = {row["ref"]: row["clauses"] for row in body["controls"]}
    assert controls == {
        "A.8.1": {},
        "A.8.13": {str(operation.id): "applicable"},
        "A.8.15": {
            str(operation.id): "applicable",
            str(monitoring.id): "partially_applicable",
        },
    }
    clauses = {row["ref"]: row["controls"] for row in body["clauses"]}
    assert clauses == {
        "8.1": {str(logging.id): "applicable", str(backups.id): "applicable"},
        "9.1": {str(logging.id): "partially_applicable"},
    }
    refused = keen.client().get("/v1/statement-of-applicability")
    assert (refused.status_code, refused.json()) == (
        401,
        {"detail": "Not authenticated"},
    )


def test_the_statement_lists_every_register_risk_with_its_kind(keen):
    """Simple and CIA risks share the register, so the statement names each."""
    db = keen.db
    db.add(Framework(slug=FW))
    category = RiskCategory(name="Data")
    db.add(category)
    db.flush()
    subcategory = RiskAssetSubcategory(category_id=category.id, name="Databases")
    db.add(subcategory)
    db.flush()
    asset = RiskAsset(
        name="Payroll", category_id=category.id, subcategory_id=subcategory.id
    )
    db.add(asset)
    db.flush()
    db.add_all(
        [
            Risk(asset_id=asset.id, threat_summary="Flood", risk_types=[]),
            Risk(
                asset_id=asset.id,
                threat_summary="Ransomware",
                risk_types=["Availability"],
            ),
        ]
    )
    db.commit()
    reader = keen.client(keen.user("risk.read"))
    body = reader.get("/v1/statement-of-applicability", params={"framework": FW}).json()
    assert {
        row["threat_summary"]: (row["method"], row["risk_types"])
        for row in body["risks"]
    } == {"Flood": ("general", []), "Ransomware": ("cia", ["Availability"])}
