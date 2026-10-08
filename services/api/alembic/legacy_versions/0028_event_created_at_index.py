"""index event ingestion time for latest evidence ticker

Revision ID: 0028_event_created_at_index
Revises: 0027_audit_attendee_users
Create Date: 2026-05-21

"""

from __future__ import annotations

from alembic import op

revision = "0028_event_created_at_index"
down_revision = "0027_audit_attendee_users"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_events_created_at", "events", ["created_at"])


def downgrade():
    op.drop_index("ix_events_created_at", table_name="events")
