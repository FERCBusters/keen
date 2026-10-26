"""Index manual purge jobs; give the planner statistics on event time buckets.

Every evidence write checks for an active or cooling-down manual purge job.
Automatic retention records a completed job every minute and keeps thirty days
of them, so the check reads the few manual jobs through a partial index.

Event histograms group events by day or hour. Statistics on those expressions
tell the planner how many buckets a window holds, so it aggregates them by
hash instead of sorting every event in the window.
"""

from alembic import op

revision = "0021_dashboard_query_support"
down_revision = "0020_theme_ink"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX ix_evidence_purge_jobs_manual_status "
        "ON evidence_purge_jobs (status, updated_at) WHERE NOT automatic"
    )
    op.execute(
        "CREATE STATISTICS st_events_day_bucket "
        "ON (date_trunc('day', \"timestamp\")) FROM events"
    )
    op.execute(
        "CREATE STATISTICS st_events_hour_bucket "
        "ON (date_trunc('hour', \"timestamp\")) FROM events"
    )
    # Populate the statistics now; autovacuum would otherwise defer them.
    op.execute("ANALYZE events")
    op.execute("ANALYZE evidence_purge_jobs")


def downgrade():
    op.execute("DROP STATISTICS IF EXISTS st_events_hour_bucket")
    op.execute("DROP STATISTICS IF EXISTS st_events_day_bucket")
    op.drop_index(
        "ix_evidence_purge_jobs_manual_status", table_name="evidence_purge_jobs"
    )
