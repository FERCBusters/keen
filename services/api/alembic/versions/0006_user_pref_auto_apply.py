"""user_pref_auto_apply_filters

Revision ID: 0006_user_pref_auto_apply
Revises: 0005_saved_searches
Create Date: 2026-01-16

"""

from alembic import op
import sqlalchemy as sa

revision = "0006_user_pref_auto_apply"
down_revision = "0005_saved_searches"
branch_labels = None
depends_on = None


def upgrade():
    # Default to False to preserve existing explicit-Apply workflow.
    op.add_column(
        "users",
        sa.Column(
            "pref_auto_apply_filters",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Remove server default after backfilling existing rows.
    op.alter_column("users", "pref_auto_apply_filters", server_default=None)


def downgrade():
    op.drop_column("users", "pref_auto_apply_filters")
