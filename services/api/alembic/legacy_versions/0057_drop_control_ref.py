"""drop free-text ISMS effectiveness measure control ref

Revision ID: 0057_drop_eff_control_ref
Revises: 0056_isms_effectiveness
Create Date: 2026-06-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0057_drop_eff_control_ref"
down_revision = "0056_isms_effectiveness"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(
        col.get("name") == column_name for col in inspector.get_columns(table_name)
    )


def upgrade() -> None:
    if _has_column("isms_effectiveness_measures", "control_ref"):
        op.drop_column("isms_effectiveness_measures", "control_ref")


def downgrade() -> None:
    if not _has_column("isms_effectiveness_measures", "control_ref"):
        op.add_column(
            "isms_effectiveness_measures",
            sa.Column("control_ref", sa.Text(), nullable=False, server_default=""),
        )
