"""Link every kind of risk to both controls and clauses.

A register risk linked to controls and a PESTLE(E) factor to clauses, so a
risk could say nothing about a clause and a factor nothing in the 90 shipped
frameworks that have controls and no clauses. Each gains the other link, in a
table shaped like the one it already had: risk_clause_links beside
risk_control_links, and pestle_control_relevance beside
pestle_clause_relevance. Both start empty; existing links are unchanged.
"""

import sqlalchemy as sa
from alembic import op

revision = "0053_risk_and_factor_links"
down_revision = "0052_remove_business_processes"
branch_labels = None
depends_on = None

RISK_CLAUSES = "risk_clause_links"
FACTOR_CONTROLS = "pestle_control_relevance"


def upgrade():
    now = sa.text("now()")
    op.create_table(
        RISK_CLAUSES,
        sa.Column("risk_id", sa.UUID(), nullable=False),
        sa.Column("framework_slug", sa.String(64), nullable=False),
        sa.Column("clause_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=now, nullable=False),
        sa.PrimaryKeyConstraint(
            "risk_id", "framework_slug", "clause_id", name="risk_clause_links_pkey"
        ),
        sa.ForeignKeyConstraint(
            ["risk_id"],
            ["risks.id"],
            name="risk_clause_links_risk_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["clause_id"],
            ["framework_clauses.id"],
            name="risk_clause_links_clause_id_fkey",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_risk_clause_links_framework_slug", RISK_CLAUSES, ["framework_slug"]
    )
    op.create_index("ix_risk_clause_links_clause_id", RISK_CLAUSES, ["clause_id"])
    op.create_table(
        FACTOR_CONTROLS,
        sa.Column("pestle_item_id", sa.UUID(), nullable=False),
        sa.Column("control_item_id", sa.UUID(), nullable=False),
        sa.Column("relevance_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=now, nullable=False),
        sa.PrimaryKeyConstraint(
            "pestle_item_id", "control_item_id", name="pestle_control_relevance_pkey"
        ),
        sa.ForeignKeyConstraint(
            ["pestle_item_id"],
            ["pestle_items.id"],
            name="pestle_control_relevance_pestle_item_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["control_item_id"],
            ["control_items.id"],
            name="pestle_control_relevance_control_item_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["relevance_id"],
            ["pestle_relevance_levels.id"],
            name="pestle_control_relevance_relevance_id_fkey",
        ),
    )
    op.create_index(
        "ix_pestle_control_relevance_control_item_id",
        FACTOR_CONTROLS,
        ["control_item_id"],
    )
    op.create_index(
        "ix_pestle_control_relevance_relevance_id", FACTOR_CONTROLS, ["relevance_id"]
    )


def downgrade():
    op.drop_table(FACTOR_CONTROLS)
    op.drop_table(RISK_CLAUSES)
