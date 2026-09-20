"""user_pref_source_colors

Revision ID: 0007_user_pref_source_colors
Revises: 0006_user_pref_auto_apply
Create Date: 2026-01-16

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_user_pref_source_colors"
down_revision = "0006_user_pref_auto_apply"
branch_labels = None
depends_on = None


def upgrade():
    # Store per-user overrides for source badge colors.
    # Keys: source id (e.g. "loki", "webhook:github")
    # Values: normalized hex string ("#RRGGBB").
    op.add_column(
        "users",
        sa.Column(
            "pref_source_colors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("users", "pref_source_colors", server_default=None)


def downgrade():
    op.drop_column("users", "pref_source_colors")
