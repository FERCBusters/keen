"""event_questions

Revision ID: 0014_event_questions
Revises: 0013_group_roles
Create Date: 2026-01-21

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_event_questions"
down_revision = "0013_group_roles"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_question_threads",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'unanswered'"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status in ('unanswered','reviewing','answered')",
            name="ck_event_question_thread_status",
        ),
    )
    op.create_index(
        "ix_event_question_threads_event_id",
        "event_question_threads",
        ["event_id"],
    )
    op.create_index(
        "ix_event_question_threads_status",
        "event_question_threads",
        ["status"],
    )
    op.create_index(
        "ix_event_question_threads_updated_at",
        "event_question_threads",
        ["updated_at"],
    )

    op.create_table(
        "event_question_posts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_question_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_event_question_posts_thread_id",
        "event_question_posts",
        ["thread_id"],
    )
    op.create_index(
        "ix_event_question_posts_created_at",
        "event_question_posts",
        ["created_at"],
    )

    op.create_table(
        "event_question_post_attachments",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "post_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_question_posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_event_question_post_attachments_post_id",
        "event_question_post_attachments",
        ["post_id"],
    )

    # Permission for creating new event questions
    op.execute("""
        INSERT INTO permissions (id, code, description, created_at)
        SELECT gen_random_uuid(), 'event.question', 'Create auditor questions on events', NOW()
        WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code='event.question');
        """)


def downgrade():
    op.execute("DELETE FROM permissions WHERE code='event.question'")

    op.drop_index(
        "ix_event_question_post_attachments_post_id",
        table_name="event_question_post_attachments",
    )
    op.drop_table("event_question_post_attachments")

    op.drop_index(
        "ix_event_question_posts_created_at", table_name="event_question_posts"
    )
    op.drop_index(
        "ix_event_question_posts_thread_id", table_name="event_question_posts"
    )
    op.drop_table("event_question_posts")

    op.drop_index(
        "ix_event_question_threads_updated_at", table_name="event_question_threads"
    )
    op.drop_index(
        "ix_event_question_threads_status", table_name="event_question_threads"
    )
    op.drop_index(
        "ix_event_question_threads_event_id", table_name="event_question_threads"
    )
    op.drop_table("event_question_threads")
