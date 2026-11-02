"""Mapping rules suggested from unmapped events, and the job's finished runs.

A suggestion keeps the [source, activity] pairs it covers, the proposed rule,
its candidate controls with their reasons, and sample event identifiers. Event
content stays with the events. A created or dismissed suggestion keeps its
pairs from being suggested again in its framework.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032_rule_suggestions"
down_revision = "0031_theme_ink_default"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rule_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("framework_slug", sa.String(64), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("keys", postgresql.JSONB(), nullable=False),
        sa.Column("rule", postgresql.JSONB(), nullable=False),
        sa.Column("controls", postgresql.JSONB(), nullable=False),
        sa.Column("samples", postgresql.JSONB(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("rule_id", sa.String(96), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "framework_slug", "signature", name="uq_rule_suggestions_signature"
        ),
        sa.CheckConstraint(
            "status IN ('open','applied','dismissed')",
            name="ck_rule_suggestions_status",
        ),
    )
    op.create_index(
        "ix_rule_suggestions_framework_status",
        "rule_suggestions",
        ["framework_slug", "status"],
    )
    op.create_table(
        "rule_suggestion_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("framework_slug", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("examined", sa.Integer(), nullable=False),
        sa.Column("unmapped", sa.Integer(), nullable=False),
        sa.Column("suggested", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('completed','failed')", name="ck_rule_suggestion_runs_status"
        ),
    )
    op.create_index(
        "ix_rule_suggestion_runs_finished", "rule_suggestion_runs", ["finished_at"]
    )


def downgrade():
    op.drop_index("ix_rule_suggestion_runs_finished", table_name="rule_suggestion_runs")
    op.drop_table("rule_suggestion_runs")
    op.drop_index("ix_rule_suggestions_framework_status", table_name="rule_suggestions")
    op.drop_table("rule_suggestions")
