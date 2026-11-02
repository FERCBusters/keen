"""Inbox items: what each person is told about.

An item names its record by kind and identifier only, and is kept once per
person and record. Read items are removed after a while.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033_inbox_items"
down_revision = "0032_rule_suggestions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inbox_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "user_id", "kind", "subject_id", name="uq_inbox_items_subject"
        ),
        sa.CheckConstraint(
            "kind IN ('audit_started','quiz_round','meeting_minutes')",
            name="ck_inbox_items_kind",
        ),
    )
    op.create_index(
        "ix_inbox_items_user_created", "inbox_items", ["user_id", "created_at"]
    )
    op.create_index("ix_inbox_items_subject", "inbox_items", ["kind", "subject_id"])


def downgrade():
    op.drop_index("ix_inbox_items_subject", table_name="inbox_items")
    op.drop_index("ix_inbox_items_user_created", table_name="inbox_items")
    op.drop_table("inbox_items")
