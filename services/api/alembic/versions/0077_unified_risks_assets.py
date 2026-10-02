"""Unify risk ratings, supply reusable asset choices, retire application configuration.

Revision ID: 0077_unified_risks_assets
Revises: 0076_assurance_risks
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0077_unified_risks_assets"
down_revision = "0076_assurance_risks"
branch_labels = None
depends_on = None


ASSETS = (
    ("All", "Organisation"),
    ("All company policies and procedures", "Organisation"),
    ("Internal systems", "Technology"),
    ("Supplier systems", "Third parties"),
    ("Public-facing systems", "Technology"),
    ("Electronic information", "Information"),
    ("Paper information", "Information"),
    ("Devices", "Technology"),
    ("Staff", "People"),
    ("Contractors", "People"),
    ("Premises", "Facilities"),
    ("Home offices", "Facilities"),
)


def upgrade():
    db = op.get_bind()
    # Existing register ratings always win. For unrated legacy rows, the
    # greater of Threat and Vulnerability is a conservative likelihood estimate.
    # Retain the old factors in mitigator_context for audit/review.
    db.execute(
        sa.text(
            """
        UPDATE risks SET mitigator_context = COALESCE(mitigator_context, '{}'::jsonb) ||
          jsonb_build_object('legacy_rating', jsonb_build_object(
            'threat', threat_score, 'vulnerability', vulnerability_score,
            'impact', impact_score, 'residual_vulnerability', residual_vulnerability_score,
            'residual_impact', residual_impact_score))
        WHERE (register_likelihood IS NULL OR register_impact IS NULL OR
               register_residual_likelihood IS NULL OR register_residual_impact IS NULL)
          AND NOT (COALESCE(mitigator_context, '{}'::jsonb) ? 'legacy_rating')
    """
        )
    )
    db.execute(
        sa.text(
            """
        UPDATE risks SET
          register_likelihood = COALESCE(register_likelihood, GREATEST(1, threat_score, vulnerability_score)),
          register_impact = COALESCE(register_impact, GREATEST(1, impact_score)),
          register_residual_likelihood = COALESCE(register_residual_likelihood, GREATEST(1, residual_vulnerability_score)),
          register_residual_impact = COALESCE(register_residual_impact, GREATEST(1, residual_impact_score))
        WHERE register_likelihood IS NULL OR register_impact IS NULL OR
              register_residual_likelihood IS NULL OR register_residual_impact IS NULL
    """
        )
    )
    db.execute(
        sa.text(
            """
        UPDATE risks SET threat_score = 1, vulnerability_score = register_likelihood,
          impact_score = register_impact, residual_vulnerability_score = register_residual_likelihood,
          residual_impact_score = register_residual_impact,
          risk_score = register_likelihood * register_impact,
          residual_risk_score = register_residual_likelihood * register_residual_impact
    """
        )
    )

    for name, category_name in ASSETS:
        category = db.execute(
            sa.text(
                "SELECT id FROM risk_categories WHERE lower(name)=lower(:name) LIMIT 1"
            ),
            {"name": category_name},
        ).scalar()
        if category is None:
            category = uuid.uuid4()
            db.execute(
                sa.text(
                    "INSERT INTO risk_categories (id,name,created_at,updated_at) VALUES (:id,:name,NOW(),NOW())"
                ),
                {"id": category, "name": category_name},
            )
        sub = db.execute(
            sa.text(
                "SELECT id FROM risk_asset_subcategories WHERE category_id=:cat AND lower(name)=lower(:name) LIMIT 1"
            ),
            {"cat": category, "name": "General"},
        ).scalar()
        if sub is None:
            sub = uuid.uuid4()
            db.execute(
                sa.text(
                    "INSERT INTO risk_asset_subcategories (id,category_id,name,created_at,updated_at) VALUES (:id,:cat,:name,NOW(),NOW())"
                ),
                {"id": sub, "cat": category, "name": "General"},
            )
        if not db.execute(
            sa.text("SELECT 1 FROM risk_assets WHERE lower(name)=lower(:name) LIMIT 1"),
            {"name": name},
        ).first():
            descriptions = {
                "All": "Scope applies across all assets in this register; review applicability as your asset inventory changes.",
                "Supplier systems": "Scope includes third-party software and supplier-managed systems.",
                "Devices": "Scope includes laptops, desktop computers, phones and other endpoint devices.",
            }
            db.execute(
                sa.text(
                    "INSERT INTO risk_assets (id,name,category_id,subcategory_id,license,description,created_at,updated_at) VALUES (:id,:name,:cat,:sub,'',:description,NOW(),NOW())"
                ),
                {
                    "id": uuid.uuid4(),
                    "name": name,
                    "cat": category,
                    "sub": sub,
                    "description": descriptions.get(
                        name,
                        "Starter risk scope; review which assets apply to your organisation.",
                    ),
                },
            )

    # This matrix duplicated relationships available in the asset, personnel,
    # policy and business process models. Remove its rows and orphaned links.
    for table in ("isms_entity_control_links", "isms_entity_clause_links"):
        db.execute(
            sa.text(
                f"DELETE FROM {table} WHERE entity_type = 'application_configuration'"
            )
        )
    db.execute(sa.text("DELETE FROM isms_application_configuration_entries"))


def downgrade():
    # Ratings and removed matrix rows cannot be reconstructed reliably.
    pass
