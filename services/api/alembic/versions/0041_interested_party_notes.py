"""add notes to interested parties

Revision ID: 0041_interested_party_notes
Revises: 0040_user_email
Create Date: 2026-05-26

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0041_interested_party_notes"
down_revision = "0040_user_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "interested_parties",
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("interested_parties", "note")
