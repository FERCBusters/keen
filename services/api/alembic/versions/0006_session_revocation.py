"""Persistent account-wide session revocation."""

import sqlalchemy as sa
from alembic import op

revision = "0006_session_revocation"
down_revision = "0005_agent_enrollment"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("users", "session_version")
