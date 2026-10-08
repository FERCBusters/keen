"""event search performance indexes

Revision ID: 0029_event_search_performance
Revises: 0028_event_created_at_index
Create Date: 2026-05-21

"""

from __future__ import annotations

from alembic import op

revision = "0029_event_search_performance"
down_revision = "0028_event_created_at_index"
branch_labels = None
depends_on = None


def upgrade():
    # pg_trgm is already created by 0017, but keep this migration self-contained
    # for deployments that may have been partially migrated or restored.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # These indexes may be built on a large events table, so create them
    # concurrently to avoid blocking ingestion writes for the duration of the
    # index build. CREATE INDEX CONCURRENTLY must run outside Alembic's normal
    # transaction.
    with op.get_context().autocommit_block():
        # The Events search box uses substring semantics via ILIKE '%term%'. 0017
        # covered summary/system/actor/action; these additional trigram indexes cover
        # the remaining fields now searched by q=, avoiding a sequential scan when
        # users search source names, outcomes, or source-native event IDs.
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_events_source_trgm "
            "ON events USING gin (source gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_events_outcome_trgm "
            "ON events USING gin (outcome gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_events_external_id_trgm "
            "ON events USING gin (external_id gin_trgm_ops)"
        )

        # A combined PostgreSQL FTS document gives the planner a single GIN index for
        # normal evidence lookups across the event display/search fields. This is
        # deliberately limited to compact columns rather than raw JSON payloads, since
        # payload-wide trigram/FTS indexes can become very large on high-ingest systems.
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_events_search_document_fts "
            "ON events USING gin ("
            "to_tsvector('simple', "
            "coalesce(summary, '') || ' ' || "
            "coalesce(action, '') || ' ' || "
            "coalesce(system, '') || ' ' || "
            "coalesce(actor, '') || ' ' || "
            "coalesce(source, '') || ' ' || "
            "coalesce(outcome, '') || ' ' || "
            "coalesce(external_id, '')"
            ")"
            ")"
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_events_search_document_fts")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_events_external_id_trgm")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_events_outcome_trgm")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_events_source_trgm")
