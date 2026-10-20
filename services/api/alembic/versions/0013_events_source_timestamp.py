"""Index events by source and timestamp.

Source pages, source-filtered event lists and per-source activity insights read
one source over a time range; the single-column indexes on source and timestamp
each leave the other half of that predicate to a filter.
"""

from alembic import op

revision = "0013_events_source_timestamp"
down_revision = "0012_stories"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_events_source_timestamp", "events", ["source", "timestamp"])


def downgrade():
    op.drop_index("ix_events_source_timestamp", table_name="events")
