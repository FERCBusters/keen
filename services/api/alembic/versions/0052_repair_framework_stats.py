"""repair durable framework event aggregate stats

Revision ID: 0052_repair_framework_stats
Revises: 0051_drop_diary_acl
Create Date: 2026-05-29
"""

from __future__ import annotations

from alembic import op

revision = "0052_repair_framework_stats"
down_revision = "0051_drop_diary_acl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Rebuild event summary counters from source-of-truth tables.

    ``controls.html`` reports Events unmapped as total events minus distinct
    events mapped to the selected framework. The source of truth is still
    ``events`` + ``mappings`` + ``control_items``; this migration repairs any
    drift in the durable summary tables introduced before the trigger-backed
    counter path was stable or by administrative remapping/cleanup operations.
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
    # Data repair only; there is no safe/meaningful way to restore stale counts.
    pass
