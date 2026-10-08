"""allow ISMS objective target to be periodic text

Revision ID: 0044_isms_objective
Revises: 0043_isms_module
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa

revision = "0044_isms_objective"
down_revision = "0043_isms_module"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "isms_objectives",
        "completion_target_date",
        existing_type=sa.Date(),
        type_=sa.String(length=64),
        existing_nullable=True,
        postgresql_using="completion_target_date::text",
    )


def downgrade() -> None:
    op.alter_column(
        "isms_objectives",
        "completion_target_date",
        existing_type=sa.String(length=64),
        type_=sa.Date(),
        existing_nullable=True,
        postgresql_using="CASE WHEN completion_target_date ~ '^\\d{4}-\\d{2}-\\d{2}$' THEN completion_target_date::date ELSE NULL END",
    )
