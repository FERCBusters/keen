"""default_diary_audit_perms

Revision ID: 0012_default_diary_audit_perms
Revises: 0011_groups_perms_diary_acl
Create Date: 2026-01-20

"""

from __future__ import annotations

import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_default_diary_audit_perms"
down_revision = "0011_groups_perms_diary_acl"
branch_labels = None
depends_on = None


DIARY_READ = "diary.read"
AUDIT_READ = "audit.read"


def upgrade():
    bind = op.get_bind()

    permissions = sa.table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True)),
        sa.Column("code", sa.String(length=128)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
    )

    # Ensure the built-in permission rows exist.
    existing_codes = {
        c for (c,) in bind.execute(sa.select(permissions.c.code)).fetchall() if c
    }

    rows = []
    now = datetime.utcnow()

    if DIARY_READ not in existing_codes:
        rows.append(
            {
                "id": uuid.uuid4(),
                "code": DIARY_READ,
                "description": "Can view diary (manual evidence) events, subject to per-entry visibility rules.",
                "created_at": now,
            }
        )

    if AUDIT_READ not in existing_codes:
        rows.append(
            {
                "id": uuid.uuid4(),
                "code": AUDIT_READ,
                "description": "Can view the internal UI->API audit trail (admin audit tab).",
                "created_at": now,
            }
        )

    if rows:
        op.bulk_insert(permissions, rows)

    # Default: grant diary.read to all active non-admin users.
    diary_id = bind.execute(
        sa.select(permissions.c.id).where(permissions.c.code == DIARY_READ)
    ).scalar()
    if not diary_id:
        return

    users = sa.table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True)),
        sa.Column("role", sa.String(length=32)),
        sa.Column("is_active", sa.Boolean()),
    )

    user_permissions = sa.table(
        "user_permissions",
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True)),
    )

    eligible_uids = [
        uid
        for (uid,) in bind.execute(
            sa.select(users.c.id)
            .where(users.c.is_active.is_(True))
            .where(users.c.role != "admin")
        ).fetchall()
    ]

    if not eligible_uids:
        return

    already = {
        uid
        for (uid,) in bind.execute(
            sa.select(user_permissions.c.user_id).where(
                user_permissions.c.permission_id == diary_id
            )
        ).fetchall()
        if uid
    }

    to_add = [
        {"user_id": uid, "permission_id": diary_id}
        for uid in eligible_uids
        if uid not in already
    ]

    if to_add:
        bind.execute(user_permissions.insert(), to_add)


def downgrade():
    bind = op.get_bind()

    permissions = sa.table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True)),
        sa.Column("code", sa.String(length=128)),
    )

    user_permissions = sa.table(
        "user_permissions",
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True)),
    )

    # Remove audit.read permission and any grants.
    audit_id = bind.execute(
        sa.select(permissions.c.id).where(permissions.c.code == AUDIT_READ)
    ).scalar()
    if audit_id:
        bind.execute(
            user_permissions.delete().where(
                user_permissions.c.permission_id == audit_id
            )
        )
        bind.execute(permissions.delete().where(permissions.c.id == audit_id))

    # Best-effort remove the diary.read grants we added.
    diary_id = bind.execute(
        sa.select(permissions.c.id).where(permissions.c.code == DIARY_READ)
    ).scalar()
    if diary_id:
        bind.execute(
            user_permissions.delete().where(
                user_permissions.c.permission_id == diary_id
            )
        )
