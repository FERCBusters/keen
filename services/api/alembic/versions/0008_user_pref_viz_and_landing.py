"""user_pref_viz_and_landing

Revision ID: 0008_user_pref_viz_and_landing
Revises: 0007_user_pref_source_colors
Create Date: 2026-01-16

"""

from alembic import op
import sqlalchemy as sa

revision = "0008_user_pref_viz_and_landing"
down_revision = "0007_user_pref_source_colors"
branch_labels = None
depends_on = None


def upgrade():
    # Preferred visualisation mode on the Visualisation page.
    op.add_column(
        "users",
        sa.Column(
            "pref_viz_mode",
            sa.String(length=32),
            nullable=False,
            server_default="graph",
        ),
    )

    # Default landing page after authentication (or when opening the app).
    op.add_column(
        "users",
        sa.Column(
            "pref_landing_page",
            sa.String(length=128),
            nullable=False,
            server_default="/",
        ),
    )

    # Remove server defaults (application owns defaults after migration).
    op.alter_column("users", "pref_viz_mode", server_default=None)
    op.alter_column("users", "pref_landing_page", server_default=None)


def downgrade():
    op.drop_column("users", "pref_landing_page")
    op.drop_column("users", "pref_viz_mode")
