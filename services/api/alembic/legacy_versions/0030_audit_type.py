"""audit type and clause-linked audit controls

Revision ID: 0030_audit_type
Revises: 0029_event_search_performance
Create Date: 2026-05-25

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0030_audit_type"
down_revision = "0029_event_search_performance"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "audits",
        sa.Column(
            "audit_type",
            sa.String(length=16),
            nullable=False,
            server_default="internal",
        ),
    )
    op.create_check_constraint(
        "ck_audits_audit_type",
        "audits",
        "audit_type in ('internal','external')",
    )

    # Keep previously-created audits consistent with the new rule: any audit
    # scoped to a clause also has the controls linked to that clause in scope.
    op.execute("""
        INSERT INTO audit_scoped_controls (audit_id, control_item_id, created_at)
        SELECT DISTINCT ascopes.audit_id, links.control_item_id, CURRENT_TIMESTAMP
        FROM audit_scoped_clauses AS ascopes
        JOIN audits AS audits_row ON audits_row.id = ascopes.audit_id
        JOIN control_clause_links AS links ON links.clause_id = ascopes.clause_id
        JOIN control_items AS controls ON controls.id = links.control_item_id
        WHERE controls.framework_slug = audits_row.framework_slug
        ON CONFLICT (audit_id, control_item_id) DO NOTHING
        """)


def downgrade():
    op.drop_constraint("ck_audits_audit_type", "audits", type_="check")
    op.drop_column("audits", "audit_type")
