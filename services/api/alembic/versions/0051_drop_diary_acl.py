"""drop diary visibility acl tables

Revision ID: 0051_drop_diary_acl
Revises: 0050_framework_event_stats
Create Date: 2026-05-28
"""

from __future__ import annotations

import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0051_drop_diary_acl"
down_revision = "0050_framework_event_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove obsolete per-diary-entry visibility ACL storage."""
    op.execute("""
        DELETE FROM user_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code = 'diary.read'
        )
    """)
    op.execute("""
        DELETE FROM group_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code = 'diary.read'
        )
    """)
    op.execute("DELETE FROM permissions WHERE code = 'diary.read'")

    op.execute("DROP INDEX IF EXISTS ix_event_visible_users_user_id")
    op.execute("DROP INDEX IF EXISTS ix_event_visible_users_event_id")
    op.execute("DROP TABLE IF EXISTS event_visible_users")

    op.execute("DROP INDEX IF EXISTS ix_event_visible_groups_group_id")
    op.execute("DROP INDEX IF EXISTS ix_event_visible_groups_event_id")
    op.execute("DROP TABLE IF EXISTS event_visible_groups")


def downgrade() -> None:
    """Recreate the former diary ACL tables and permission row."""
    op.create_table(
        "event_visible_users",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_index(
        "ix_event_visible_users_event_id",
        "event_visible_users",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        "ix_event_visible_users_user_id",
        "event_visible_users",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "event_visible_groups",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_index(
        "ix_event_visible_groups_event_id",
        "event_visible_groups",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        "ix_event_visible_groups_group_id",
        "event_visible_groups",
        ["group_id"],
        unique=False,
    )

    permissions = sa.table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True)),
        sa.Column("code", sa.String(length=128)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
    )
    bind = op.get_bind()
    exists = bind.execute(
        sa.select(permissions.c.id).where(permissions.c.code == "diary.read")
    ).scalar()
    if not exists:
        op.bulk_insert(
            permissions,
            [
                {
                    "id": uuid.uuid4(),
                    "code": "diary.read",
                    "description": "Can view diary (manual evidence) events.",
                    "created_at": datetime.utcnow(),
                }
            ],
        )
