"""add ISMS effectiveness measures

Revision ID: 0056_isms_effectiveness
Revises: 0055_audit_sample_entities
Create Date: 2026-06-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0056_isms_effectiveness"
down_revision = "0055_audit_sample_entities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "isms_effectiveness_measures",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("framework_slug", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "effectiveness_measure", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("metric", sa.Text(), nullable=False, server_default=""),
        sa.Column("metric_key", sa.String(length=128), nullable=True),
        sa.Column("target_value", sa.Float(), nullable=True),
        sa.Column(
            "target_unit", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column(
            "threshold_operator",
            sa.String(length=16),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("frequency", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.CheckConstraint(
            "threshold_operator in ('','lt','lte','eq','gte','gt')",
            name="ck_isms_effectiveness_threshold_operator",
        ),
        sa.UniqueConstraint(
            "metric_key", name="uq_isms_effectiveness_measures_metric_key"
        ),
    )
    for col in [
        "framework_slug",
        "metric_key",
        "owner_user_id",
        "created_at",
        "updated_at",
    ]:
        op.create_index(
            f"ix_isms_effectiveness_measures_{col}",
            "isms_effectiveness_measures",
            [col],
        )

    op.create_table(
        "isms_effectiveness_metric_entries",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "measure_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_effectiveness_measures.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("metric_value", sa.Float(), nullable=True),
        sa.Column(
            "metric_unit", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column("qualitative_value", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "source_type", sa.String(length=64), nullable=False, server_default="other"
        ),
        sa.Column(
            "source_title", sa.String(length=256), nullable=False, server_default=""
        ),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "source_reference", sa.String(length=256), nullable=False, server_default=""
        ),
        sa.Column(
            "source_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.CheckConstraint(
            "metric_value is not null or qualitative_value <> ''",
            name="ck_isms_effectiveness_metric_entry_has_value",
        ),
    )
    for col in [
        "measure_id",
        "recorded_at",
        "period_start",
        "period_end",
        "source_type",
        "source_event_id",
        "created_at",
    ]:
        op.create_index(
            f"ix_isms_effectiveness_metric_entries_{col}",
            "isms_effectiveness_metric_entries",
            [col],
        )


def downgrade() -> None:
    for col in [
        "created_at",
        "source_event_id",
        "source_type",
        "period_end",
        "period_start",
        "recorded_at",
        "measure_id",
    ]:
        op.drop_index(
            f"ix_isms_effectiveness_metric_entries_{col}",
            table_name="isms_effectiveness_metric_entries",
        )
    op.drop_table("isms_effectiveness_metric_entries")
    for col in [
        "updated_at",
        "created_at",
        "owner_user_id",
        "metric_key",
        "framework_slug",
    ]:
        op.drop_index(
            f"ix_isms_effectiveness_measures_{col}",
            table_name="isms_effectiveness_measures",
        )
    op.drop_table("isms_effectiveness_measures")
