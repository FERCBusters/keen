"""add ISMS access control matrix

Revision ID: 0053_isms_access_control
Revises: 0052_repair_framework_stats
Create Date: 2026-05-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0053_isms_access_control"
down_revision = "0052_repair_framework_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "isms_aws_accounts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.UniqueConstraint("account_id", name="uq_isms_aws_accounts_account_id"),
    )
    op.create_index("ix_isms_aws_accounts_name", "isms_aws_accounts", ["name"])
    op.create_index(
        "ix_isms_aws_accounts_created_at", "isms_aws_accounts", ["created_at"]
    )

    op.create_table(
        "isms_access_control_matrix_entries",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("task_action", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "service_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("risk_assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="Pending Approval",
        ),
        sa.Column(
            "approved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.CheckConstraint(
            "status in ('Pending Approval','Approved')",
            name="ck_isms_access_control_matrix_status",
        ),
    )
    for col in [
        "service_asset_id",
        "status",
        "approved_by_user_id",
        "created_at",
    ]:
        op.create_index(
            f"ix_isms_access_control_matrix_entries_{col}",
            "isms_access_control_matrix_entries",
            [col],
        )

    op.create_table(
        "isms_access_control_matrix_aws_accounts",
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_access_control_matrix_entries.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "aws_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_aws_accounts.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )
    op.create_index(
        "ix_isms_access_control_matrix_aws_accounts_entry_id",
        "isms_access_control_matrix_aws_accounts",
        ["entry_id"],
    )
    op.create_index(
        "ix_isms_access_control_matrix_aws_accounts_aws_account_id",
        "isms_access_control_matrix_aws_accounts",
        ["aws_account_id"],
    )

    op.create_table(
        "isms_access_control_matrix_roles",
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_access_control_matrix_entries.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "org_node_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_org_nodes.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )
    op.create_index(
        "ix_isms_access_control_matrix_roles_entry_id",
        "isms_access_control_matrix_roles",
        ["entry_id"],
    )
    op.create_index(
        "ix_isms_access_control_matrix_roles_org_node_id",
        "isms_access_control_matrix_roles",
        ["org_node_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_isms_access_control_matrix_roles_org_node_id",
        table_name="isms_access_control_matrix_roles",
    )
    op.drop_index(
        "ix_isms_access_control_matrix_roles_entry_id",
        table_name="isms_access_control_matrix_roles",
    )
    op.drop_table("isms_access_control_matrix_roles")
    op.drop_index(
        "ix_isms_access_control_matrix_aws_accounts_aws_account_id",
        table_name="isms_access_control_matrix_aws_accounts",
    )
    op.drop_index(
        "ix_isms_access_control_matrix_aws_accounts_entry_id",
        table_name="isms_access_control_matrix_aws_accounts",
    )
    op.drop_table("isms_access_control_matrix_aws_accounts")
    for col in [
        "created_at",
        "approved_by_user_id",
        "status",
        "service_asset_id",
    ]:
        op.drop_index(
            f"ix_isms_access_control_matrix_entries_{col}",
            table_name="isms_access_control_matrix_entries",
        )
    op.drop_table("isms_access_control_matrix_entries")
    op.drop_index("ix_isms_aws_accounts_created_at", table_name="isms_aws_accounts")
    op.drop_index("ix_isms_aws_accounts_name", table_name="isms_aws_accounts")
    op.drop_table("isms_aws_accounts")
