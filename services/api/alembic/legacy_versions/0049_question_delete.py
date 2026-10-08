"""question delete permission

Revision ID: 0049_question_delete
Revises: 0048_user_delete
Create Date: 2026-05-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0049_question_delete"
down_revision = "0048_user_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
            INSERT INTO permissions (id, code, description, created_at)
            SELECT gen_random_uuid(), 'question.delete', 'Delete question threads', NOW()
            WHERE NOT EXISTS (
                SELECT 1 FROM permissions WHERE code = 'question.delete'
            )
            """))


def downgrade() -> None:
    op.execute("DELETE FROM permissions WHERE code = 'question.delete'")
