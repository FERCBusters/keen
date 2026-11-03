"""Count wrong second-factor codes for each account, across sign-ins.

Five wrong TOTP or recovery codes within an hour end the ceremony, refuse codes
for the account for an hour, and tell its owner.
"""

import sqlalchemy as sa
from alembic import op

revision = "0041_mfa_code_failures"
down_revision = "0040_remove_audittrail_read"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column(
            "mfa_code_failures", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "users", sa.Column("mfa_code_failures_since", sa.DateTime(), nullable=True)
    )


def downgrade():
    op.drop_column("users", "mfa_code_failures_since")
    op.drop_column("users", "mfa_code_failures")
