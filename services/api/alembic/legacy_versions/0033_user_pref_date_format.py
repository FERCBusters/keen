"""User preference for visible date input format

Revision ID: 0033_user_pref_date_format
Revises: 0032_scheduled_audit_fuzzy_dates
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_user_pref_date_format"
down_revision = "0032_scheduled_audit_fuzzy_dates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "pref_date_format",
            sa.String(length=16),
            nullable=False,
            server_default="default",
        ),
    )
    op.create_check_constraint(
        "ck_users_pref_date_format",
        "users",
        "pref_date_format in ('default','ymd','dmy')",
    )
    op.alter_column("users", "pref_date_format", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_users_pref_date_format", "users", type_="check")
    op.drop_column("users", "pref_date_format")
