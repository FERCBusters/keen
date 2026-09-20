"""Stop maintaining global mapped/unmapped event counters with triggers.

Revision ID: 0065_drop_triggers
Revises: 0064_repair_triggers
Create Date: 2026-06-15
"""

from alembic import op

revision = "0065_drop_triggers"
down_revision = "0064_repair_triggers"
branch_labels = None
depends_on = None


_TRIGGER_DROPS = [
    "DROP TRIGGER IF EXISTS trg_global_event_stats_events_insert ON events",
    "DROP TRIGGER IF EXISTS trg_global_event_stats_events_delete ON events",
    "DROP TRIGGER IF EXISTS trg_framework_event_stats_mappings_insert ON mappings",
    "DROP TRIGGER IF EXISTS trg_framework_event_stats_mappings_delete ON mappings",
    "DROP TRIGGER IF EXISTS trg_framework_event_stats_mappings_update ON mappings",
    "DROP TRIGGER IF EXISTS trg_framework_event_stats_control_items_framework ON control_items",
]

_FUNCTION_DROPS = [
    "DROP FUNCTION IF EXISTS keen_framework_event_stats_from_control_item()",
    "DROP FUNCTION IF EXISTS keen_framework_event_stats_from_mapping()",
    "DROP FUNCTION IF EXISTS keen_rebuild_framework_event_stats()",
    "DROP FUNCTION IF EXISTS keen_decrement_framework_event_stats(text)",
    "DROP FUNCTION IF EXISTS keen_increment_framework_event_stats(text)",
    "DROP FUNCTION IF EXISTS keen_bump_global_event_stats()",
]


def upgrade() -> None:
    for stmt in _TRIGGER_DROPS:
        op.execute(stmt)
    for stmt in _FUNCTION_DROPS:
        op.execute(stmt)


def downgrade() -> None:
    # These trigger-maintained counters were replaced by application-level
    # cached counts. Downgrade does not recreate the old trigger functions; roll
    # back to revision 0064 to restore the previous definitions if required.
    pass
