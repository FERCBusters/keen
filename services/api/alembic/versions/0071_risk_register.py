"""Conventional risk register, treatment and reusable risk scenarios.

Revision ID: 0071_risk_register
Revises: 0070_control_links
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from alembic import op

revision = "0071_risk_register"
down_revision = "0070_control_links"
branch_labels = None
depends_on = None


def upgrade():
    for field in ("register_likelihood", "register_impact", "register_residual_likelihood", "register_residual_impact"):
        op.add_column("risks", sa.Column(field, sa.Integer(), nullable=True))
        op.create_check_constraint("ck_risks_" + field, "risks", field + " between 1 and 5")
    op.add_column("risks", sa.Column("treatment_strategy", sa.String(32), nullable=False, server_default=""))
    op.add_column("risks", sa.Column("treatment_status", sa.String(32), nullable=False, server_default="open"))
    op.add_column("risks", sa.Column("treatment_plan", sa.Text(), nullable=False, server_default=""))
    op.add_column("risks", sa.Column("treatment_due_at", sa.Date(), nullable=True))
    op.create_table(
        "risk_register_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("low_max", sa.Integer(), nullable=False),
        sa.Column("moderate_max", sa.Integer(), nullable=False),
        sa.Column("high_max", sa.Integer(), nullable=False),
    )
    op.create_table(
        "risk_library_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False, unique=True),
        sa.Column("threat_summary", sa.Text(), nullable=False),
        sa.Column("risk_types", JSONB(), nullable=False),
        sa.Column("treatment_guidance", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("risk_library_entries")
    op.drop_table("risk_register_settings")
    op.drop_column("risks", "treatment_due_at")
    op.drop_column("risks", "treatment_plan")
    op.drop_column("risks", "treatment_status")
    op.drop_column("risks", "treatment_strategy")
    for field in ("register_residual_impact", "register_residual_likelihood", "register_impact", "register_likelihood"):
        op.drop_constraint("ck_risks_" + field, "risks", type_="check")
        op.drop_column("risks", field)
