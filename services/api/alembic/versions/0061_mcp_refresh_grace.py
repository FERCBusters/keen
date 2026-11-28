"""A used MCP refresh token keeps a salt for its grace period.

A client that refreshes twice with one token, from requests sent together or
because an answer was lost, is given the same tokens again for a short period
instead of losing its grant. The tokens are derived from the refresh token and
this salt, so nothing that could be presented is stored.

Revision ID: 0061_mcp_refresh_grace
Revises: 0060_story_controls_legal
"""

import sqlalchemy as sa
from alembic import op

revision = "0061_mcp_refresh_grace"
down_revision = "0060_story_controls_legal"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "mcp_tokens", sa.Column("rotation_salt", sa.String(64), nullable=True)
    )


def downgrade():
    op.drop_column("mcp_tokens", "rotation_salt")
