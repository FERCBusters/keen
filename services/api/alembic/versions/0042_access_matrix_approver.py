"""Remove the approver from access control matrix rows.

Only accounts that manage ISMS records edit the matrix, so whoever changes a row
stands behind its current state; each row's change history names them.
"""

import sqlalchemy as sa
from alembic import op

revision = "0042_access_matrix_approver"
down_revision = "0041_mfa_code_failures"
branch_labels = None
depends_on = None

TABLE = "isms_access_control_matrix_entries"


def upgrade():
    # The column's index and foreign key go with it.
    op.drop_column(TABLE, "approved_by_user_id")


def downgrade():
    op.add_column(
        TABLE,
        sa.Column(
            "approved_by_user_id",
            sa.UUID(),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="isms_access_control_matrix_entries_approved_by_user_id_fkey",
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_isms_access_control_matrix_entries_approved_by_user_id",
        TABLE,
        ["approved_by_user_id"],
    )
