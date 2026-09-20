"""audit scoped clauses

Revision ID: 0022_audit_scoped_clauses
Revises: 0021_clause_graph_cache
Create Date: 2026-05-20

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0022_audit_scoped_clauses"
down_revision = "0021_clause_graph_cache"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "audit_scoped_clauses",
        sa.Column(
            "audit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audits.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "clause_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("framework_clauses.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_audit_scoped_clauses_audit_id", "audit_scoped_clauses", ["audit_id"]
    )
    op.create_index(
        "ix_audit_scoped_clauses_clause_id", "audit_scoped_clauses", ["clause_id"]
    )


def downgrade():
    op.drop_index(
        "ix_audit_scoped_clauses_clause_id", table_name="audit_scoped_clauses"
    )
    op.drop_index("ix_audit_scoped_clauses_audit_id", table_name="audit_scoped_clauses")
    op.drop_table("audit_scoped_clauses")
