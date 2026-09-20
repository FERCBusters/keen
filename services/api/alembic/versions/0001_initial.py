"""initial

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-06

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "frameworks",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("slug", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "control_items",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("framework_slug", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("ref", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column(
            "in_scope", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("framework_slug", "type", "ref", name="uq_controlitem_ref"),
    )
    op.create_index(
        "ix_control_items_framework_slug", "control_items", ["framework_slug"]
    )
    op.create_index("ix_control_items_ref", "control_items", ["ref"])

    op.create_table(
        "events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("system", sa.String(length=128), nullable=True),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=True),
        sa.Column("severity", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "raw_pointer",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "normalized_payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_event_source_external"),
    )
    op.create_index("ix_events_timestamp", "events", ["timestamp"])
    op.create_index("ix_events_source", "events", ["source"])
    op.create_index("ix_events_system", "events", ["system"])
    op.create_index("ix_events_actor", "events", ["actor"])
    op.create_index("ix_events_action", "events", ["action"])
    op.create_index("ix_events_outcome", "events", ["outcome"])
    op.create_index("ix_events_severity", "events", ["severity"])

    op.create_table(
        "artifacts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("storage_uri", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "content_type",
            sa.String(length=128),
            nullable=False,
            server_default=sa.text("'application/octet-stream'"),
        ),
        sa.Column(
            "size_bytes", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("captured_by", sa.String(length=128), nullable=True),
        sa.Column(
            "redaction_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        sa.Column(
            "pii_flags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "retention_class",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'default'"),
        ),
    )
    op.create_index("ix_artifacts_event_id", "artifacts", ["event_id"])

    op.create_table(
        "mappings",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id"),
            nullable=False,
        ),
        sa.Column(
            "control_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control_items.id"),
            nullable=False,
        ),
        sa.Column(
            "confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")
        ),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("mapped_by", sa.String(length=128), nullable=True),
        sa.Column("mapped_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("event_id", "control_item_id", name="uq_mapping_unique"),
    )
    op.create_index("ix_mappings_event_id", "mappings", ["event_id"])
    op.create_index("ix_mappings_control_item_id", "mappings", ["control_item_id"])

    op.create_table(
        "ingestion_cursors",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("last_ts", sa.DateTime(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("ingestion_cursors")
    op.drop_table("mappings")
    op.drop_table("artifacts")
    op.drop_table("events")
    op.drop_table("control_items")
    op.drop_table("frameworks")
