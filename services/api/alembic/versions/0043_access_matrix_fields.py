"""Bind access control matrix rows to their service alone.

Only accounts that manage ISMS records edit the matrix, so a saved row is the
access its editor approves, and a status beside it repeats that. A row's
service is an asset, and the cloud accounts, subscriptions and projects Keen
collects posture from are assets in the Cloud services category, so a separate
list of hosting accounts repeats the asset register.
"""

import sqlalchemy as sa
from alembic import op

revision = "0043_access_matrix_fields"
down_revision = "0042_access_matrix_approver"
branch_labels = None
depends_on = None

ENTRIES = "isms_access_control_matrix_entries"
LINKS = "isms_access_control_matrix_aws_accounts"
ACCOUNTS = "isms_aws_accounts"
STATUS_CHECK = "ck_isms_access_control_matrix_status"
STATUS_INDEX = "ix_isms_access_control_matrix_entries_status"


def upgrade():
    op.drop_table(LINKS)
    op.drop_table(ACCOUNTS)
    # The column's index and check constraint go with it.
    op.drop_column(ENTRIES, "status")


def downgrade():
    # Every row a manager has saved since stands approved; the hosting-account
    # tables return empty.
    op.add_column(
        ENTRIES,
        sa.Column("status", sa.String(32), nullable=False, server_default="Approved"),
    )
    op.alter_column(ENTRIES, "status", server_default="Pending Approval")
    op.create_check_constraint(
        STATUS_CHECK, ENTRIES, "status in ('Pending Approval','Approved')"
    )
    op.create_index(STATUS_INDEX, ENTRIES, ["status"])
    op.create_table(
        ACCOUNTS,
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("account_id", sa.String(32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_by_user_id",
            sa.UUID(),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="isms_aws_accounts_created_by_user_id_fkey",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("account_id", name="uq_isms_aws_accounts_account_id"),
    )
    op.create_index("ix_isms_aws_accounts_created_at", ACCOUNTS, ["created_at"])
    op.create_index("ix_isms_aws_accounts_name", ACCOUNTS, ["name"])
    op.create_table(
        LINKS,
        sa.Column(
            "entry_id",
            sa.UUID(),
            sa.ForeignKey(
                f"{ENTRIES}.id",
                ondelete="CASCADE",
                name="isms_access_control_matrix_aws_accounts_entry_id_fkey",
            ),
            primary_key=True,
        ),
        sa.Column(
            "aws_account_id",
            sa.UUID(),
            sa.ForeignKey(
                f"{ACCOUNTS}.id",
                ondelete="CASCADE",
                name="isms_access_control_matrix_aws_accounts_aws_account_id_fkey",
            ),
            primary_key=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_isms_access_control_matrix_aws_accounts_aws_account_id",
        LINKS,
        ["aws_account_id"],
    )
    op.create_index(
        "ix_isms_access_control_matrix_aws_accounts_entry_id", LINKS, ["entry_id"]
    )
