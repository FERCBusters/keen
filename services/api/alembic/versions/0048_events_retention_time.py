"""Index events by the time retention counts them from.

Retention keeps the newest events and removes those past an age limit. It
counts an event from its source's date, but never from later than the moment
Keen collected it, so an event a source dates ahead of the present ranks and
ages by when it arrived. The index lets the newest-events query read that order
without sorting every event.
"""

from alembic import op

revision = "0048_events_retention_time"
down_revision = "0047_affected_links"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX ix_events_retention_time "
        'ON events (LEAST("timestamp", created_at), id)'
    )
    op.execute("ANALYZE events")


def downgrade():
    op.drop_index("ix_events_retention_time", table_name="events")
