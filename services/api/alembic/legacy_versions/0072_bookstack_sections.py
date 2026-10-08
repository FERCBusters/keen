"""Keep policy section permalinks and immutable page-version snapshots.

Revision ID: 0072_bookstack_sections
Revises: 0071_risk_register
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision = "0072_bookstack_sections"
down_revision = "0071_risk_register"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bookstack_section_evidence",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("isms_documents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_control_id", UUID(as_uuid=True), sa.ForeignKey("control_items.id", ondelete="SET NULL")),
        sa.Column("target_clause_id", UUID(as_uuid=True), sa.ForeignKey("framework_clauses.id", ondelete="SET NULL")),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("anchor", sa.String(256), nullable=False),
        sa.Column("permalink", sa.String(2048), nullable=False),
        sa.Column("page_title", sa.String(256), nullable=False),
        sa.Column("revision_count", sa.Integer()),
        sa.Column("page_updated_at", sa.String(128), nullable=False),
        sa.Column("storage_uri", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("captured_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.CheckConstraint("target_control_id IS NOT NULL OR target_clause_id IS NOT NULL", name="ck_bookstack_section_target"),
    )
    op.create_index("ix_bookstack_section_evidence_document_id", "bookstack_section_evidence", ["document_id"])


def downgrade():
    op.drop_index("ix_bookstack_section_evidence_document_id", table_name="bookstack_section_evidence")
    op.drop_table("bookstack_section_evidence")
