"""clause graph performance and parent link cleanup

Revision ID: 0021_clause_graph_cache
Revises: 0020_framework_clauses
Create Date: 2026-05-19

"""

from __future__ import annotations

from alembic import op

revision = "0021_clause_graph_cache"
down_revision = "0020_framework_clauses"
branch_labels = None
depends_on = None


def upgrade():
    # The clause-source visualisation fans Event -> Mapping -> Control -> Clause.
    # These composite indexes help the extra clause join and framework scoping.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_control_clause_links_clause_control "
        "ON control_clause_links (clause_id, control_item_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_framework_clauses_framework_parent "
        "ON framework_clauses (framework_slug, parent_clause_id)"
    )

    # Parent clauses that themselves have a parent and children, for example 7.5
    # when 7.5.1/7.5.2/7.5.3 exist, are headings for more specific subclauses.
    # Remove direct control mappings to those intermediate parents; keep mappings
    # on leaf subclauses and top-level clauses.
    op.execute("""
        DELETE FROM control_clause_links
        WHERE clause_id IN (
            SELECT parent.id
            FROM framework_clauses parent
            WHERE parent.parent_clause_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM framework_clauses child
                  WHERE child.parent_clause_id = parent.id
              )
        )
        """)


def downgrade():
    # Data cleanup is intentionally not reversible.
    op.execute("DROP INDEX IF EXISTS ix_framework_clauses_framework_parent")
    op.execute("DROP INDEX IF EXISTS ix_control_clause_links_clause_control")
