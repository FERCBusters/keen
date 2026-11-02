"""Owners of controls and clauses."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_control_owners"
down_revision = "0035_notification_delivery"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "control_owners",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "control_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control_items.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "clause_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("framework_clauses.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(control_item_id IS NULL) <> (clause_id IS NULL)",
            name="ck_control_owners_target",
        ),
        sa.UniqueConstraint(
            "control_item_id", "user_id", name="uq_control_owners_control"
        ),
        sa.UniqueConstraint("clause_id", "user_id", name="uq_control_owners_clause"),
    )
    op.create_index("ix_control_owners_user", "control_owners", ["user_id"])


def downgrade():
    op.drop_index("ix_control_owners_user", table_name="control_owners")
    op.drop_table("control_owners")
