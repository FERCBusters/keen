"""event_question_notifications

Revision ID: 0015_event_question_notif
Revises: 0014_event_questions
Create Date: 2026-01-21

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0015_event_question_notif"
down_revision = "0014_event_questions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "event_question_threads",
        sa.Column("last_admin_reply_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "event_question_threads",
        sa.Column("author_last_seen_at", sa.DateTime(), nullable=True),
    )

    op.create_index(
        "ix_event_question_threads_created_by_user_id",
        "event_question_threads",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_event_question_threads_last_admin_reply_at",
        "event_question_threads",
        ["last_admin_reply_at"],
    )
    op.create_index(
        "ix_event_question_threads_author_last_seen_at",
        "event_question_threads",
        ["author_last_seen_at"],
    )


def downgrade():
    op.drop_index(
        "ix_event_question_threads_author_last_seen_at",
        table_name="event_question_threads",
    )
    op.drop_index(
        "ix_event_question_threads_last_admin_reply_at",
        table_name="event_question_threads",
    )
    op.drop_index(
        "ix_event_question_threads_created_by_user_id",
        table_name="event_question_threads",
    )

    op.drop_column("event_question_threads", "author_last_seen_at")
    op.drop_column("event_question_threads", "last_admin_reply_at")
