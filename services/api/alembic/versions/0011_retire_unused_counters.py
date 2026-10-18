"""Drop the unused event counter tables and the mode-only visualisation preference.

Event counts come from the Valkey counter cache, and the saved default
visualisation records the mode a user prefers.
"""

from alembic import op

revision = "0011_retire_unused_counters"
down_revision = "0010_swot_planning"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP TABLE global_event_stats")
    op.execute("DROP TABLE framework_event_stats")
    op.execute("ALTER TABLE users DROP COLUMN pref_viz_mode")


def downgrade():
    op.execute(
        "ALTER TABLE users ADD COLUMN pref_viz_mode varchar(32) NOT NULL DEFAULT 'graph'"
    )
    op.execute(
        """UPDATE users SET pref_viz_mode = pref_default_visualisation->>'mode'
        WHERE pref_default_visualisation->>'mode' IN
            ('graph','heatmap','sunburst_source','sunburst_control','histogram')"""
    )
    op.execute("""CREATE TABLE framework_event_stats (
        framework_slug varchar(64) PRIMARY KEY,
        mapped_event_count integer NOT NULL DEFAULT 0,
        updated_at timestamp NOT NULL DEFAULT now(),
        CONSTRAINT ck_framework_event_stats_count_nonnegative
            CHECK (mapped_event_count >= 0)
    )""")
    op.execute("""CREATE TABLE global_event_stats (
        stats_key varchar(32) PRIMARY KEY DEFAULT 'events',
        total_events integer NOT NULL DEFAULT 0,
        updated_at timestamp NOT NULL DEFAULT now(),
        CONSTRAINT ck_global_event_stats_total_nonnegative CHECK (total_events >= 0)
    )""")
    op.execute(
        "INSERT INTO global_event_stats (stats_key, total_events) "
        "SELECT 'events', count(*) FROM events"
    )
