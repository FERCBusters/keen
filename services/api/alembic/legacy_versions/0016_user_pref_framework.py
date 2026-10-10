"""user_pref_framework

Revision ID: 0016_user_pref_framework
Revises: 0015_event_question_notif
Create Date: 2026-02-16

"""

import sqlalchemy as sa
from alembic import op

revision = "0016_user_pref_framework"
down_revision = "0015_event_question_notif"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("pref_default_framework", sa.String(length=64), nullable=True),
    )


def downgrade():
    op.drop_column("users", "pref_default_framework")
