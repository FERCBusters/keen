"""add description to ISMS effectiveness measures

Revision ID: 0058_eff_desc
Revises: 0057_drop_eff_control_ref
Create Date: 2026-06-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0058_eff_desc"
down_revision = "0057_drop_eff_control_ref"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(
        col.get("name") == column_name for col in inspector.get_columns(table_name)
    )


def upgrade() -> None:
    if not _has_column("isms_effectiveness_measures", "description"):
        op.add_column(
            "isms_effectiveness_measures",
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    if _has_column("isms_effectiveness_measures", "description"):
        op.drop_column("isms_effectiveness_measures", "description")
