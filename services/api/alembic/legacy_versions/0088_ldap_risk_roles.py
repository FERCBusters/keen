"""LDAP account backend and organisational risk owners."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0088_ldap_risk_roles"
down_revision = "0087_sso_verified_emails"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column(
            "auth_backend", sa.String(16), nullable=False, server_default="local"
        ),
    )
    op.add_column(
        "risks", sa.Column("risk_owner_role_id", UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_risks_owner_role",
        "risks",
        "isms_org_nodes",
        ["risk_owner_role_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_risks_risk_owner_role_id", "risks", ["risk_owner_role_id"])
    op.create_check_constraint(
        "ck_risks_one_owner",
        "risks",
        "risk_owner_user_id IS NULL OR risk_owner_role_id IS NULL",
    )


def downgrade():
    op.drop_constraint("ck_risks_one_owner", "risks", type_="check")
    op.drop_column("risks", "risk_owner_role_id")
    op.drop_column("users", "auth_backend")
