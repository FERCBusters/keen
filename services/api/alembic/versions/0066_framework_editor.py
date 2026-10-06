"""Persist framework metadata for the admin editor.

Revision ID: 0066_framework_editor
Revises: 0065_drop_triggers
"""
from alembic import op
import sqlalchemy as sa

revision = "0066_framework_editor"
down_revision = "0065_drop_triggers"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("frameworks", sa.Column("name", sa.String(256)))
    op.add_column("frameworks", sa.Column("version", sa.String(128)))
    op.add_column("frameworks", sa.Column("description", sa.Text()))
    op.add_column("frameworks", sa.Column("upstream_url", sa.Text()))


def downgrade():
    for name in ("upstream_url", "description", "version", "name"):
        op.drop_column("frameworks", name)
