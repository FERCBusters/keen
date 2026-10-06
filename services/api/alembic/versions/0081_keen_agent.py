"""Scoped KEEN Agent identities; credentials stored as hashes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0081_keen_agent"
down_revision = "0080_integration_builder"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "keen_agents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("last_seen", sa.DateTime),
        sa.Column("health", JSONB, nullable=False),
    )


def downgrade():
    op.drop_table("keen_agents")
