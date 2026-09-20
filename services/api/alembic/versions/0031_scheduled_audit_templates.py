"""scheduled audit templates

Revision ID: 0031_scheduled_audit_templates
Revises: 0030_audit_type
Create Date: 2026-05-25

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0031_scheduled_audit_templates"
down_revision = "0030_audit_type"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_audits_status", "audits", type_="check")
    op.create_check_constraint(
        "ck_audits_status",
        "audits",
        "status in ('open','in_progress','completed','archived','template')",
    )

    op.add_column(
        "audits",
        sa.Column(
            "schedule_recurrence",
            sa.String(length=32),
            nullable=False,
            server_default="once",
        ),
    )
    op.add_column(
        "audits",
        sa.Column(
            "schedule_interval",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "audits",
        sa.Column("schedule_next_run_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "audits",
        sa.Column("schedule_until_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "audits",
        sa.Column("schedule_last_run_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "audits",
        sa.Column(
            "schedule_last_created_audit_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_audits_schedule_last_created_audit_id",
        "audits",
        "audits",
        ["schedule_last_created_audit_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_audits_schedule_recurrence",
        "audits",
        "schedule_recurrence in ('once','weekly','monthly','quarterly','yearly')",
    )
    op.create_check_constraint(
        "ck_audits_schedule_interval",
        "audits",
        "schedule_interval >= 1",
    )
    op.create_index(
        "ix_audits_status_schedule_next_run_date",
        "audits",
        ["status", "schedule_next_run_date"],
    )


def downgrade():
    op.drop_index("ix_audits_status_schedule_next_run_date", table_name="audits")
    op.drop_constraint("ck_audits_schedule_interval", "audits", type_="check")
    op.drop_constraint("ck_audits_schedule_recurrence", "audits", type_="check")
    op.drop_constraint(
        "fk_audits_schedule_last_created_audit_id", "audits", type_="foreignkey"
    )
    op.drop_column("audits", "schedule_last_created_audit_id")
    op.drop_column("audits", "schedule_last_run_date")
    op.drop_column("audits", "schedule_until_date")
    op.drop_column("audits", "schedule_next_run_date")
    op.drop_column("audits", "schedule_interval")
    op.drop_column("audits", "schedule_recurrence")

    # Downgrading cannot preserve scheduled templates under the old constraint.
    op.execute("UPDATE audits SET status='archived' WHERE status='template'")
    op.drop_constraint("ck_audits_status", "audits", type_="check")
    op.create_check_constraint(
        "ck_audits_status",
        "audits",
        "status in ('open','in_progress','completed','archived')",
    )
