"""Named built-in source connections and persistent event provenance."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_source_connections"
down_revision = "0002_release_seed"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "source_connections",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("configuration", JSONB(), nullable=False),
        sa.Column("encrypted_credentials", sa.Text()),
        sa.Column("inputs", JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_source_connections_source", "source_connections", ["source"])
    op.add_column("events", sa.Column("connection_id", sa.String(96)))
    op.add_column("events", sa.Column("connection_name", sa.String(128)))
    op.create_index("ix_events_connection_id", "events", ["connection_id"])


def downgrade():
    op.drop_index("ix_events_connection_id", table_name="events")
    op.drop_column("events", "connection_name")
    op.drop_column("events", "connection_id")
    op.drop_table("source_connections")
