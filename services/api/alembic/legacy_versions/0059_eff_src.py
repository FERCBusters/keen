"""default effectiveness metric source to other

Revision ID: 0059_eff_src
Revises: 0058_eff_desc
Create Date: 2026-06-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0059_eff_src"
down_revision = "0058_eff_desc"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(
        col.get("name") == column_name for col in inspector.get_columns(table_name)
    )


def upgrade() -> None:
    if _has_column("isms_effectiveness_metric_entries", "source_type"):
        op.alter_column(
            "isms_effectiveness_metric_entries",
            "source_type",
            existing_type=sa.String(length=64),
            server_default="other",
            existing_nullable=False,
        )


def downgrade() -> None:
    if _has_column("isms_effectiveness_metric_entries", "source_type"):
        op.alter_column(
            "isms_effectiveness_metric_entries",
            "source_type",
            existing_type=sa.String(length=64),
            server_default="manual",
            existing_nullable=False,
        )
