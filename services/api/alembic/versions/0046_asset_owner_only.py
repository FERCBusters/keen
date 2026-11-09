"""The asset owner is the asset register's only organisational relationship.

Remove the separate "Register held by" field.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0046_asset_owner_only"
down_revision = "0045_mcp_oauth"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("risk_assets", "register_held_by_org_node_id")


def downgrade():
    op.add_column(
        "risk_assets",
        sa.Column(
            "register_held_by_org_node_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_risk_assets_register_held_by_org_node_id_isms_org_nodes",
        "risk_assets",
        "isms_org_nodes",
        ["register_held_by_org_node_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_risk_assets_register_held_by_org_node_id",
        "risk_assets",
        ["register_held_by_org_node_id"],
    )
