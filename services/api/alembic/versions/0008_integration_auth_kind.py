"""Fit every supported custom integration authentication type."""

import sqlalchemy as sa
from alembic import op

revision = "0008_integration_auth_kind"
down_revision = "0007_audit_permissions"
branch_labels = None
depends_on = None


def upgrade():
    # "oauth_client_credentials" is 24 characters.
    op.alter_column(
        "integration_connections",
        "auth_kind",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=False,
    )


def downgrade():
    op.execute(
        sa.text(
            "UPDATE integration_connections SET auth_kind = 'none', "
            "encrypted_secret = '' WHERE length(auth_kind) > 16"
        )
    )
    op.alter_column(
        "integration_connections",
        "auth_kind",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=False,
    )
