"""query performance indexes

Revision ID: 0017_query_performance_indexes
Revises: 0016_user_pref_framework
Create Date: 2026-05-08

"""

from __future__ import annotations

from alembic import op

revision = "0017_query_performance_indexes"
down_revision = "0016_user_pref_framework"
branch_labels = None
depends_on = None


def upgrade():
    # Ordered event browsing and source-filtered event browsing.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_events_timestamp_desc_id "
        "ON events (timestamp DESC, id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_events_source_timestamp_desc_id "
        "ON events (source, timestamp DESC, id)"
    )

    # Common joins/aggregates for dashboard source/control counts.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mappings_control_event "
        "ON mappings (control_item_id, event_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mappings_control_mapped_at_desc "
        "ON mappings (control_item_id, mapped_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_control_items_framework_id "
        "ON control_items (framework_slug, id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_control_items_framework_ref "
        "ON control_items (framework_slug, ref)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_control_items_framework_scope "
        "ON control_items (framework_slug, in_scope)"
    )

    # Text search filters use ILIKE '%term%'. pg_trgm makes those searches indexable.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_events_summary_trgm "
        "ON events USING gin (summary gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_events_system_trgm "
        "ON events USING gin (system gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_events_actor_trgm "
        "ON events USING gin (actor gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_events_action_trgm "
        "ON events USING gin (action gin_trgm_ops)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_events_action_trgm")
    op.execute("DROP INDEX IF EXISTS ix_events_actor_trgm")
    op.execute("DROP INDEX IF EXISTS ix_events_system_trgm")
    op.execute("DROP INDEX IF EXISTS ix_events_summary_trgm")
    op.execute("DROP INDEX IF EXISTS ix_control_items_framework_scope")
    op.execute("DROP INDEX IF EXISTS ix_control_items_framework_ref")
    op.execute("DROP INDEX IF EXISTS ix_control_items_framework_id")
    op.execute("DROP INDEX IF EXISTS ix_mappings_control_mapped_at_desc")
    op.execute("DROP INDEX IF EXISTS ix_mappings_control_event")
    op.execute("DROP INDEX IF EXISTS ix_events_source_timestamp_desc_id")
    op.execute("DROP INDEX IF EXISTS ix_events_timestamp_desc_id")
