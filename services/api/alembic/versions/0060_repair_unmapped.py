"""repair unmapped event counters and cache-era stats

Revision ID: 0060_repair_unmapped
Revises: 0059_eff_src
Create Date: 2026-06-02
"""

from __future__ import annotations

from alembic import op

revision = "0060_repair_unmapped"
down_revision = "0059_eff_src"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Rebuild durable event counters from source-of-truth tables.

    Controls and the Events page should use the same definition of unmapped:
    total visible events minus distinct events mapped to the selected framework.
    If older cached/durable counters drifted during remapping or earlier trigger
    iterations, the Controls page could show a higher unmapped count than
    /events.html?unmapped=true. Rebuilding here makes the durable counters match
    the live Events query again.
    """

    op.execute("TRUNCATE framework_event_stats")
    op.execute("TRUNCATE global_event_stats")

    op.execute("""
        INSERT INTO global_event_stats (stats_key, total_events, updated_at)
        SELECT 'events', COUNT(*)::integer, NOW()
        FROM events
        """)
    op.execute("""
        INSERT INTO framework_event_stats (
            framework_slug,
            mapped_event_count,
            updated_at
        )
        SELECT
            c.framework_slug,
            COUNT(DISTINCT m.event_id)::integer AS mapped_event_count,
            NOW() AS updated_at
        FROM mappings m
        JOIN control_items c ON c.id = m.control_item_id
        GROUP BY c.framework_slug
        """)


def downgrade() -> None:
    # Data repair only; there is no meaningful stale state to restore.
    pass
