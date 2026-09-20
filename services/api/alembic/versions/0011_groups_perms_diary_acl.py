"""groups_perms_diary_acl

Revision ID: 0011_groups_perms_diary_acl
Revises: 0010_user_pref_default_vis
Create Date: 2026-01-20

"""

from __future__ import annotations

import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_groups_perms_diary_acl"
down_revision = "0010_user_pref_default_vis"
branch_labels = None
depends_on = None


def upgrade():
    # ------------------------------------------------------------------
    # Core RBAC tables: groups + permissions
    # ------------------------------------------------------------------

    op.create_table(
        "groups",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, default=datetime.utcnow),
        sa.UniqueConstraint("name", name="uq_groups_name"),
    )
    op.create_index("ix_groups_name", "groups", ["name"], unique=True)

    op.create_table(
        "permissions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, default=datetime.utcnow),
        sa.UniqueConstraint("code", name="uq_permissions_code"),
    )
    op.create_index("ix_permissions_code", "permissions", ["code"], unique=True)

    # ------------------------------------------------------------------
    # Link tables
    # ------------------------------------------------------------------

    op.create_table(
        "user_groups",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
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
    op.create_index("ix_user_groups_user_id", "user_groups", ["user_id"], unique=False)
    op.create_index(
        "ix_user_groups_group_id", "user_groups", ["group_id"], unique=False
    )

    op.create_table(
        "user_permissions",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "permission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_index(
        "ix_user_permissions_user_id", "user_permissions", ["user_id"], unique=False
    )
    op.create_index(
        "ix_user_permissions_permission_id",
        "user_permissions",
        ["permission_id"],
        unique=False,
    )

    op.create_table(
        "group_permissions",
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "permission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_index(
        "ix_group_permissions_group_id",
        "group_permissions",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        "ix_group_permissions_permission_id",
        "group_permissions",
        ["permission_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # Diary visibility ACL tables
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Seed default permissions
    # ------------------------------------------------------------------

    perm_table = sa.table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True)),
        sa.Column("code", sa.String(length=128)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
    )

    op.bulk_insert(
        perm_table,
        [
            {
                "id": uuid.uuid4(),
                "code": "diary.read",
                "description": "Can view diary (manual evidence) events, subject to per-entry visibility rules.",
                "created_at": datetime.utcnow(),
            }
        ],
    )


def downgrade():
    op.drop_index("ix_event_visible_groups_group_id", table_name="event_visible_groups")
    op.drop_index("ix_event_visible_groups_event_id", table_name="event_visible_groups")
    op.drop_table("event_visible_groups")

    op.drop_index("ix_event_visible_users_user_id", table_name="event_visible_users")
    op.drop_index("ix_event_visible_users_event_id", table_name="event_visible_users")
    op.drop_table("event_visible_users")

    op.drop_index("ix_group_permissions_permission_id", table_name="group_permissions")
    op.drop_index("ix_group_permissions_group_id", table_name="group_permissions")
    op.drop_table("group_permissions")

    op.drop_index("ix_user_permissions_permission_id", table_name="user_permissions")
    op.drop_index("ix_user_permissions_user_id", table_name="user_permissions")
    op.drop_table("user_permissions")

    op.drop_index("ix_user_groups_group_id", table_name="user_groups")
    op.drop_index("ix_user_groups_user_id", table_name="user_groups")
    op.drop_table("user_groups")

    op.drop_index("ix_permissions_code", table_name="permissions")
    op.drop_table("permissions")

    op.drop_index("ix_groups_name", table_name="groups")
    op.drop_table("groups")
