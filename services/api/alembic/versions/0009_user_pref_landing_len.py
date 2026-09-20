"""user_pref_landing_page_length

Revision ID: 0009_user_pref_landing_len
Revises: 0008_user_pref_viz_and_landing
Create Date: 2026-01-16

"""

from alembic import op
import sqlalchemy as sa

revision = "0009_user_pref_landing_len"
down_revision = "0008_user_pref_viz_and_landing"
branch_labels = None
depends_on = None


def upgrade():
    # Allow query params (e.g. saved searches) in the user's preferred landing page.
    # Use batch_alter_table for SQLite compatibility.
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "pref_landing_page",
            existing_type=sa.String(length=128),
            type_=sa.String(length=2048),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "pref_landing_page",
            existing_type=sa.String(length=2048),
            type_=sa.String(length=128),
            existing_nullable=False,
        )
