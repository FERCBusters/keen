"""user_pref_default_vis

Revision ID: 0010_user_pref_default_vis
Revises: 0009_user_pref_landing_len
Create Date: 2026-01-16

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_user_pref_default_vis"
down_revision = "0009_user_pref_landing_len"
branch_labels = None
depends_on = None


def upgrade():
    # Store the user's default Visualisation page state:
    # - mode (graph/heatmap/sunburst/histogram)
    # - date range (relative days or absolute start/end)
    # - optional sunburst focus (zoomed top-level segment)
    op.add_column(
        "users",
        sa.Column(
            "pref_default_visualisation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("users", "pref_default_visualisation")
