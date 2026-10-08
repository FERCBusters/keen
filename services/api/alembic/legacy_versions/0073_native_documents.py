"""Native policy editing, hierarchy, tags, comments and content revisions.

Revision ID: 0073_native_documents
Revises: 0072_bookstack_sections
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from alembic import op

revision = "0073_native_documents"
down_revision = "0072_bookstack_sections"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("isms_document_folders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("parent_id", UUID(as_uuid=True), sa.ForeignKey("isms_document_folders.id", ondelete="SET NULL")))
    op.add_column("isms_documents", sa.Column("folder_id", UUID(as_uuid=True), sa.ForeignKey("isms_document_folders.id", ondelete="SET NULL")))
    op.create_index("ix_isms_documents_folder_id", "isms_documents", ["folder_id"])
    op.add_column("isms_documents", sa.Column("tags", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("isms_documents", sa.Column("content_html", sa.Text(), nullable=False, server_default=""))
    op.add_column("isms_documents", sa.Column("content_version", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("isms_document_revisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("isms_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_html", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("document_id", "version", name="uq_isms_document_revision"))
    op.create_index("ix_isms_document_revisions_document_id", "isms_document_revisions", ["document_id"])
    op.create_table("isms_document_comments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("isms_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_isms_document_comments_document_id", "isms_document_comments", ["document_id"])


def downgrade():
    op.drop_index("ix_isms_document_comments_document_id", table_name="isms_document_comments")
    op.drop_table("isms_document_comments")
    op.drop_index("ix_isms_document_revisions_document_id", table_name="isms_document_revisions")
    op.drop_table("isms_document_revisions")
    for field in ("content_version", "content_html", "tags"):
        op.drop_column("isms_documents", field)
    op.drop_index("ix_isms_documents_folder_id", table_name="isms_documents")
    op.drop_column("isms_documents", "folder_id")
    op.drop_table("isms_document_folders")
