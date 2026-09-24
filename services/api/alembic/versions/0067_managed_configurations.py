"""Store managed connector configurations and mapping rules.

Revision ID: 0067_managed_configurations
Revises: 0066_framework_editor
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0067_managed_configurations"
down_revision = "0066_framework_editor"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "managed_configurations",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("document", JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("updated_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "managed_configuration_revisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("document", JSONB, nullable=False),
        sa.Column("updated_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "rule_backfill_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("rule_id", sa.String(96), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("rule_document", JSONB, nullable=False),
        sa.Column("rules_version", sa.Integer, nullable=False),
        sa.Column("total_estimate", sa.Integer, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("cutoff", sa.DateTime, nullable=False),
        sa.Column("cursor_timestamp", sa.DateTime),
        sa.Column("cursor_id", UUID(as_uuid=True)),
        sa.Column("examined", sa.Integer, nullable=False),
        sa.Column("matched", sa.Integer, nullable=False),
        sa.Column("created_mappings", sa.Integer, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_rule_backfill_jobs_rule_id", "rule_backfill_jobs", ["rule_id"])
    op.create_index("ix_rule_backfill_jobs_source", "rule_backfill_jobs", ["source"])
    op.create_index("ix_events_source_timestamp_id_backfill", "events", ["source", "timestamp", "id"])


def downgrade():
    op.drop_index("ix_events_source_timestamp_id_backfill", table_name="events")
    op.drop_table("rule_backfill_jobs")
    op.drop_table("managed_configuration_revisions")
    op.drop_table("managed_configurations")
