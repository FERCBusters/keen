"""audittrail permission rename and audit executive summary

Revision ID: 0019_audittrail_perms
Revises: 0018_audit_engagements
Create Date: 2026-05-19

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0019_audittrail_perms"
down_revision = "0018_audit_engagements"
branch_labels = None
depends_on = None

OLD_AUDIT_TRAIL_PERMISSION = "audit.read"
NEW_AUDIT_TRAIL_PERMISSION = "audittrail.read"


def _merge_or_rename_permission(
    bind, old_code: str, new_code: str, description: str
) -> None:
    permissions = sa.table(
        "permissions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String(length=128)),
        sa.column("description", sa.Text()),
    )
    user_permissions = sa.table(
        "user_permissions",
        sa.column("user_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
    )
    group_permissions = sa.table(
        "group_permissions",
        sa.column("group_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
    )

    old_id = bind.execute(
        sa.select(permissions.c.id).where(permissions.c.code == old_code)
    ).scalar()
    new_id = bind.execute(
        sa.select(permissions.c.id).where(permissions.c.code == new_code)
    ).scalar()

    if old_id and not new_id:
        bind.execute(
            permissions.update()
            .where(permissions.c.id == old_id)
            .values(code=new_code, description=description)
        )
        return

    if not old_id and new_id:
        bind.execute(
            permissions.update()
            .where(permissions.c.id == new_id)
            .values(description=description)
        )
        return

    if old_id and new_id and old_id != new_id:
        # Preserve any existing user/group grants by copying them to the clearer
        # permission row, then remove the old row and its duplicate grants.
        old_user_ids = [
            uid
            for (uid,) in bind.execute(
                sa.select(user_permissions.c.user_id).where(
                    user_permissions.c.permission_id == old_id
                )
            ).fetchall()
            if uid
        ]
        existing_user_ids = {
            uid
            for (uid,) in bind.execute(
                sa.select(user_permissions.c.user_id).where(
                    user_permissions.c.permission_id == new_id
                )
            ).fetchall()
            if uid
        }
        to_add_users = [
            {"user_id": uid, "permission_id": new_id}
            for uid in old_user_ids
            if uid not in existing_user_ids
        ]
        if to_add_users:
            bind.execute(user_permissions.insert(), to_add_users)

        old_group_ids = [
            gid
            for (gid,) in bind.execute(
                sa.select(group_permissions.c.group_id).where(
                    group_permissions.c.permission_id == old_id
                )
            ).fetchall()
            if gid
        ]
        existing_group_ids = {
            gid
            for (gid,) in bind.execute(
                sa.select(group_permissions.c.group_id).where(
                    group_permissions.c.permission_id == new_id
                )
            ).fetchall()
            if gid
        }
        to_add_groups = [
            {"group_id": gid, "permission_id": new_id}
            for gid in old_group_ids
            if gid not in existing_group_ids
        ]
        if to_add_groups:
            bind.execute(group_permissions.insert(), to_add_groups)

        bind.execute(
            user_permissions.delete().where(user_permissions.c.permission_id == old_id)
        )
        bind.execute(
            group_permissions.delete().where(
                group_permissions.c.permission_id == old_id
            )
        )
        bind.execute(permissions.delete().where(permissions.c.id == old_id))
        bind.execute(
            permissions.update()
            .where(permissions.c.id == new_id)
            .values(description=description)
        )
        return

    if not old_id and not new_id:
        op.execute(f"""
            INSERT INTO permissions (id, code, description, created_at)
            SELECT gen_random_uuid(), '{new_code}', '{description.replace("'", "''")}', NOW()
            WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code='{new_code}')
            """)


def upgrade():
    bind = op.get_bind()

    op.add_column(
        "audits",
        sa.Column("executive_summary", sa.Text(), nullable=False, server_default=""),
    )

    _merge_or_rename_permission(
        bind,
        OLD_AUDIT_TRAIL_PERMISSION,
        NEW_AUDIT_TRAIL_PERMISSION,
        "Can view the internal Keen UI->API audit trail (admin audit trail tab).",
    )


def downgrade():
    bind = op.get_bind()

    _merge_or_rename_permission(
        bind,
        NEW_AUDIT_TRAIL_PERMISSION,
        OLD_AUDIT_TRAIL_PERMISSION,
        "Can view the internal UI->API audit trail (admin audit tab).",
    )

    op.drop_column("audits", "executive_summary")
