"""Extend questions to risks and PESTLE entities

Revision ID: 0038_pestle_questions_changes
Revises: 0037_pestle_assessments
Create Date: 2026-05-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0038_pestle_questions_changes"
down_revision = "0037_pestle_assessments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "event_question_threads",
        sa.Column("target_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "event_question_threads",
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "event_question_threads",
        sa.Column("target_ref", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "event_question_threads",
        sa.Column("target_title", sa.String(length=512), nullable=True),
    )

    # Existing question threads are evidence/event questions. Keep their historical
    # event_id relationship but also populate the generic target fields so the
    # queue, bell and account views can render all question types uniformly.
    op.execute("""
        UPDATE event_question_threads t
        SET target_type = 'event',
            target_id = t.event_id,
            target_ref = e.source,
            target_title = LEFT(e.summary, 512)
        FROM events e
        WHERE t.event_id = e.id
          AND t.target_type IS NULL
        """)
    op.alter_column("event_question_threads", "target_type", nullable=False)
    op.alter_column("event_question_threads", "event_id", nullable=True)
    op.create_index(
        "ix_event_question_threads_target",
        "event_question_threads",
        ["target_type", "target_id"],
    )
    op.create_index(
        "ix_event_question_threads_target_type",
        "event_question_threads",
        ["target_type"],
    )


def downgrade() -> None:
    # Non-event question threads cannot be represented by the old schema.
    op.execute(
        "DELETE FROM event_question_threads WHERE COALESCE(target_type, 'event') <> 'event'"
    )
    op.alter_column("event_question_threads", "event_id", nullable=False)
    op.drop_index(
        "ix_event_question_threads_target_type", table_name="event_question_threads"
    )
    op.drop_index(
        "ix_event_question_threads_target", table_name="event_question_threads"
    )
    op.drop_column("event_question_threads", "target_title")
    op.drop_column("event_question_threads", "target_ref")
    op.drop_column("event_question_threads", "target_id")
    op.drop_column("event_question_threads", "target_type")
