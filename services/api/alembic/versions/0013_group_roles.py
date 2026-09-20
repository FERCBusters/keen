"""group_roles

Revision ID: 0013_group_roles
Revises: 0012_default_diary_audit_perms
Create Date: 2026-01-20

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0013_group_roles"
down_revision = "0012_default_diary_audit_perms"
branch_labels = None
depends_on = None


def upgrade():
    # Add optional role to groups so users can inherit roles from group membership.
    op.add_column("groups", sa.Column("role", sa.String(length=32), nullable=True))


def downgrade():
    op.drop_column("groups", "role")
