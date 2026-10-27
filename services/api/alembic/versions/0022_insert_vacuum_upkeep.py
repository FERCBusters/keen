"""Vacuum events and mappings after fewer inserts.

Evidence arrives continuously and is rarely updated, so autovacuum visits these
tables only after inserts reach a fifth of their size. Until a page is vacuumed
it is not marked all-visible, and index-only scans of recent evidence (the
dashboard histograms, source lists and control evidence counts) read the table
as well. Vacuuming after inserts reach two per cent keeps that work small.
"""

from alembic import op

revision = "0022_insert_vacuum_upkeep"
down_revision = "0021_dashboard_query_support"
branch_labels = None
depends_on = None

TABLES = ("events", "mappings")


def upgrade():
    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} SET (autovacuum_vacuum_insert_scale_factor = 0.02)"
        )


def downgrade():
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} RESET (autovacuum_vacuum_insert_scale_factor)")
