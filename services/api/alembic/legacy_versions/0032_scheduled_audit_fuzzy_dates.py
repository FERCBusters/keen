"""Scheduled audit fuzzy date rules

Revision ID: 0032_scheduled_audit_fuzzy_dates
Revises: 0031_scheduled_audit_templates
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_scheduled_audit_fuzzy_dates"
down_revision = "0031_scheduled_audit_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audits",
        sa.Column(
            "schedule_date_rule",
            sa.String(length=32),
            nullable=False,
            server_default="exact",
        ),
    )
    op.add_column(
        "audits",
        sa.Column("schedule_anchor_month", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_audits_schedule_date_rule",
        "audits",
        "schedule_date_rule in ('exact','first_weekday_of_month')",
    )
    op.create_check_constraint(
        "ck_audits_schedule_anchor_month",
        "audits",
        "schedule_anchor_month is null or (schedule_anchor_month >= 1 and schedule_anchor_month <= 12)",
    )
    op.alter_column("audits", "schedule_date_rule", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_audits_schedule_anchor_month", "audits", type_="check")
    op.drop_constraint("ck_audits_schedule_date_rule", "audits", type_="check")
    op.drop_column("audits", "schedule_anchor_month")
    op.drop_column("audits", "schedule_date_rule")
