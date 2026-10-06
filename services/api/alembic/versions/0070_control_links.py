"""Explicit one-way cross-framework control relationships.

Revision ID: 0070_control_links
Revises: 0069_assurance_records
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision = "0070_control_links"
down_revision = "0069_assurance_records"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cross_framework_control_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_control_id", UUID(as_uuid=True), sa.ForeignKey("control_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_control_id", UUID(as_uuid=True), sa.ForeignKey("control_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source_control_id", "target_control_id", name="uq_cross_framework_link"),
        sa.CheckConstraint("source_control_id <> target_control_id", name="ck_cross_framework_distinct"),
    )
    op.create_index("ix_cross_framework_control_links_source_control_id", "cross_framework_control_links", ["source_control_id"])
    op.create_index("ix_cross_framework_control_links_target_control_id", "cross_framework_control_links", ["target_control_id"])


def downgrade():
    op.drop_index("ix_cross_framework_control_links_target_control_id", table_name="cross_framework_control_links")
    op.drop_index("ix_cross_framework_control_links_source_control_id", table_name="cross_framework_control_links")
    op.drop_table("cross_framework_control_links")
