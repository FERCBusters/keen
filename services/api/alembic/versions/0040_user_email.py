"""add email address to Keen users

Revision ID: 0040_user_email
Revises: 0039_interested_parties
Create Date: 2026-05-26

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0040_user_email"
down_revision = "0039_interested_parties"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("email", sa.String(length=256), nullable=True))


def downgrade():
    op.drop_column("users", "email")
