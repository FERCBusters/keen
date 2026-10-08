"""permission updates for questions, incidents and event reads

Revision ID: 0062_permission_updates
Revises: 0061_native_oidc
Create Date: 2026-06-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0062_permission_updates"
down_revision = "0061_native_oidc"
branch_labels = None
depends_on = None


def _ensure_permission(code: str, description: str) -> None:
    op.execute(sa.text("""
            INSERT INTO permissions (id, code, description, created_at)
            SELECT gen_random_uuid(), :code, :description, NOW()
            WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)
            """).bindparams(code=code, description=description))


def upgrade() -> None:
    # Rename the older event-specific question permission in-place so existing
    # user/group grants carry forward to the now entity-wide question permission.
    op.execute(sa.text("""
            UPDATE permissions
            SET code = 'question.create',
                description = 'Create question threads on supported entities'
            WHERE code = 'event.question'
              AND NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'question.create')
            """))

    # If both codes already exist, merge old grants into question.create then
    # remove the obsolete permission row.
    op.execute(sa.text("""
            INSERT INTO user_permissions (user_id, permission_id)
            SELECT up.user_id, newp.id
            FROM user_permissions up
            JOIN permissions oldp ON oldp.id = up.permission_id AND oldp.code = 'event.question'
            JOIN permissions newp ON newp.code = 'question.create'
            ON CONFLICT DO NOTHING
            """))
    op.execute(sa.text("""
            INSERT INTO group_permissions (group_id, permission_id)
            SELECT gp.group_id, newp.id
            FROM group_permissions gp
            JOIN permissions oldp ON oldp.id = gp.permission_id AND oldp.code = 'event.question'
            JOIN permissions newp ON newp.code = 'question.create'
            ON CONFLICT DO NOTHING
            """))
    op.execute(sa.text("""
            DELETE FROM user_permissions
            WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'event.question')
            """))
    op.execute(sa.text("""
            DELETE FROM group_permissions
            WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'event.question')
            """))
    op.execute(sa.text("DELETE FROM permissions WHERE code = 'event.question'"))

    _ensure_permission(
        "question.create", "Create question threads on supported entities"
    )
    _ensure_permission("incident.delete", "Delete event incident records")
    _ensure_permission("events.read", "View events and evidence")


def downgrade() -> None:
    # Remove permissions introduced by this migration. Preserve question-create
    # grants by renaming it back to the legacy event-specific permission where
    # possible.
    op.execute(sa.text("""
            UPDATE permissions
            SET code = 'event.question',
                description = 'Create event question threads'
            WHERE code = 'question.create'
              AND NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'event.question')
            """))
    op.execute(sa.text("""
            DELETE FROM user_permissions
            WHERE permission_id IN (
                SELECT id FROM permissions WHERE code IN ('incident.delete', 'events.read')
            )
            """))
    op.execute(sa.text("""
            DELETE FROM group_permissions
            WHERE permission_id IN (
                SELECT id FROM permissions WHERE code IN ('incident.delete', 'events.read')
            )
            """))
    op.execute(
        sa.text(
            "DELETE FROM permissions WHERE code IN ('incident.delete', 'events.read')"
        )
    )
