"""Versioned HTTP integrations and bounded run history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0080_integration_builder"
down_revision = "0079_control_crosswalk"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "integration_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False),
        sa.Column("auth_kind", sa.String(16), nullable=False),
        sa.Column("auth_name", sa.String(128), nullable=False),
        sa.Column("username", sa.String(256), nullable=False),
        sa.Column("auth_options", JSONB, nullable=False),
        sa.Column("encrypted_secret", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
    )
    op.create_table(
        "integration_collectors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "connection_id",
            sa.String(36),
            sa.ForeignKey("integration_connections.id"),
            nullable=False,
        ),
        sa.Column("draft", JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("live_revision", sa.Integer),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("failures", sa.Integer, nullable=False),
        sa.Column("cursor", sa.String(64)),
        sa.Column("next_run", sa.DateTime),
        sa.Column("last_success", sa.DateTime),
    )
    op.create_table(
        "integration_revisions",
        sa.Column(
            "collector_id",
            sa.String(36),
            sa.ForeignKey("integration_collectors.id"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer, primary_key=True),
        sa.Column("definition", JSONB, nullable=False),
        sa.Column(
            "connection_id",
            sa.String(36),
            sa.ForeignKey("integration_connections.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "integration_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "collector_id",
            sa.String(36),
            sa.ForeignKey("integration_collectors.id"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("finished_at", sa.DateTime),
        sa.Column("new_records", sa.Integer, nullable=False),
        sa.Column("duplicates", sa.Integer, nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("preview", sa.Boolean, nullable=False),
        sa.Column("definition", JSONB, nullable=False),
        sa.Column(
            "connection_id",
            sa.String(36),
            sa.ForeignKey("integration_connections.id"),
            nullable=False,
        ),
        sa.Column("result", JSONB, nullable=False),
    )
    op.create_index(
        "ix_integration_runs_collector_id", "integration_runs", ["collector_id"]
    )


def downgrade():
    for table in (
        "integration_runs",
        "integration_revisions",
        "integration_collectors",
        "integration_connections",
    ):
        op.drop_table(table)
