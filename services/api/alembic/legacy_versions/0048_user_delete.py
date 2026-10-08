"""user deletion and audit scoped ISMS documents

Revision ID: 0048_user_delete
Revises: 0047_isms_licenses
Create Date: 2026-05-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0048_user_delete"
down_revision = "0047_isms_licenses"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("select to_regclass(:name)"), {"name": table_name}
        ).scalar()
    )


def _constraint_exists(bind, table_name: str, constraint_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("""
        select 1 from information_schema.table_constraints
        where table_name = :table_name and constraint_name = :constraint_name
        """),
            {"table_name": table_name, "constraint_name": constraint_name},
        ).scalar()
    )


def _drop_fk_if_exists(bind, table_name: str, constraint_name: str) -> None:
    if _constraint_exists(bind, table_name, constraint_name):
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")


def _replace_user_fk(
    bind, table_name: str, column_name: str, constraint_name: str
) -> None:
    _drop_fk_if_exists(bind, table_name, constraint_name)
    op.alter_column(
        table_name,
        column_name,
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.create_foreign_key(
        constraint_name, table_name, "users", [column_name], ["id"], ondelete="SET NULL"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "audit_scoped_isms_documents"):
        op.create_table(
            "audit_scoped_isms_documents",
            sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["audit_id"], ["audits.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["document_id"], ["isms_documents.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("audit_id", "document_id"),
        )
        op.create_index(
            "ix_audit_scoped_isms_documents_audit_id",
            "audit_scoped_isms_documents",
            ["audit_id"],
        )
        op.create_index(
            "ix_audit_scoped_isms_documents_document_id",
            "audit_scoped_isms_documents",
            ["document_id"],
        )

    for table_name, column_name, constraint_name in [
        ("audits", "created_by_user_id", "audits_created_by_user_id_fkey"),
        (
            "audits",
            "final_report_uploaded_by_user_id",
            "audits_final_report_uploaded_by_user_id_fkey",
        ),
        ("audit_evidence", "added_by_user_id", "audit_evidence_added_by_user_id_fkey"),
        (
            "audit_findings",
            "created_by_user_id",
            "audit_findings_created_by_user_id_fkey",
        ),
        (
            "event_question_threads",
            "created_by_user_id",
            "event_question_threads_created_by_user_id_fkey",
        ),
        (
            "event_question_posts",
            "author_user_id",
            "event_question_posts_author_user_id_fkey",
        ),
    ]:
        _replace_user_fk(bind, table_name, column_name, constraint_name)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name, column_name, constraint_name in [
        ("audits", "created_by_user_id", "audits_created_by_user_id_fkey"),
        (
            "audits",
            "final_report_uploaded_by_user_id",
            "audits_final_report_uploaded_by_user_id_fkey",
        ),
        ("audit_evidence", "added_by_user_id", "audit_evidence_added_by_user_id_fkey"),
        (
            "audit_findings",
            "created_by_user_id",
            "audit_findings_created_by_user_id_fkey",
        ),
        (
            "event_question_threads",
            "created_by_user_id",
            "event_question_threads_created_by_user_id_fkey",
        ),
        (
            "event_question_posts",
            "author_user_id",
            "event_question_posts_author_user_id_fkey",
        ),
    ]:
        _drop_fk_if_exists(bind, table_name, constraint_name)
        # Keep the columns nullable on downgrade: once users have been deleted,
        # historical rows may legitimately contain NULL actor references.
        op.create_foreign_key(
            constraint_name, table_name, "users", [column_name], ["id"]
        )
    if _table_exists(bind, "audit_scoped_isms_documents"):
        op.drop_index(
            "ix_audit_scoped_isms_documents_document_id",
            table_name="audit_scoped_isms_documents",
        )
        op.drop_index(
            "ix_audit_scoped_isms_documents_audit_id",
            table_name="audit_scoped_isms_documents",
        )
        op.drop_table("audit_scoped_isms_documents")
