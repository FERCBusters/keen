"""Scheduled audit named weekday rules

Revision ID: 0035_scheduled_audit_days
Revises: 0034_entity_changelogs
Create Date: 2026-05-26
"""

from __future__ import annotations

from alembic import op

revision = "0035_scheduled_audit_days"
down_revision = "0034_entity_changelogs"
branch_labels = None
depends_on = None

_DATE_RULES_SQL = (
    "schedule_date_rule in ("
    "'exact',"
    "'first_weekday_of_month',"
    "'first_monday_of_month',"
    "'first_tuesday_of_month',"
    "'first_wednesday_of_month',"
    "'first_thursday_of_month',"
    "'first_friday_of_month'"
    ")"
)


def upgrade() -> None:
    op.drop_constraint("ck_audits_schedule_date_rule", "audits", type_="check")
    op.create_check_constraint(
        "ck_audits_schedule_date_rule",
        "audits",
        _DATE_RULES_SQL,
    )


def downgrade() -> None:
    # Preserve schedule validity before restoring the older two-value constraint.
    op.execute(
        "UPDATE audits "
        "SET schedule_date_rule = 'first_weekday_of_month' "
        "WHERE schedule_date_rule in ("
        "'first_monday_of_month',"
        "'first_tuesday_of_month',"
        "'first_wednesday_of_month',"
        "'first_thursday_of_month',"
        "'first_friday_of_month'"
        ")"
    )
    op.drop_constraint("ck_audits_schedule_date_rule", "audits", type_="check")
    op.create_check_constraint(
        "ck_audits_schedule_date_rule",
        "audits",
        "schedule_date_rule in ('exact','first_weekday_of_month')",
    )
