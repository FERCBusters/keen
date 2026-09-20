"""allow longer ISMS objective goal and metric fields

Revision ID: 0045_isms_goal_metric
Revises: 0044_isms_objective
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa

revision = "0045_isms_goal_metric"
down_revision = "0044_isms_objective"
branch_labels = None
depends_on = None


OBJECTIVE_TABLE = "isms_objectives"


def upgrade() -> None:
    op.alter_column(
        OBJECTIVE_TABLE,
        "goal",
        existing_type=sa.String(length=8000),
        type_=sa.Text(),
        existing_nullable=False,
        existing_server_default="",
    )
    op.alter_column(
        OBJECTIVE_TABLE,
        "metric",
        existing_type=sa.String(length=8000),
        type_=sa.Text(),
        existing_nullable=False,
        existing_server_default="",
    )


def downgrade() -> None:
    op.alter_column(
        OBJECTIVE_TABLE,
        "metric",
        existing_type=sa.Text(),
        type_=sa.String(length=8000),
        existing_nullable=False,
        existing_server_default="",
    )
    op.alter_column(
        OBJECTIVE_TABLE,
        "goal",
        existing_type=sa.Text(),
        type_=sa.String(length=8000),
        existing_nullable=False,
        existing_server_default="",
    )
