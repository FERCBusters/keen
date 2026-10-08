"""native OIDC auth with identity mapping

Revision ID: 0061_native_oidc
Revises: 0060_repair_unmapped
Create Date: 2026-06-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0061_native_oidc"
down_revision = "0060_repair_unmapped"
branch_labels = None
depends_on = None


def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def _has_index(inspector, table: str, index_name: str) -> bool:
    return any(ix.get("name") == index_name for ix in inspector.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "user_identities"):
        op.create_table(
            "user_identities",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "provider", sa.String(length=40), nullable=False, server_default="oidc"
            ),
            sa.Column("issuer", sa.String(length=512), nullable=False),
            sa.Column("subject", sa.String(length=512), nullable=False),
            sa.Column("email", sa.String(length=320), nullable=True),
            sa.Column("preferred_username", sa.String(length=255), nullable=True),
            sa.Column("display_name", sa.String(length=255), nullable=True),
            sa.Column(
                "claims",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint(
                "issuer", "subject", name="uq_user_identities_issuer_subject"
            ),
        )
        op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])
    elif not _has_index(inspector, "user_identities", "ix_user_identities_user_id"):
        op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])

    inspector = sa.inspect(bind)
    if not _has_table(inspector, "oidc_login_states"):
        op.create_table(
            "oidc_login_states",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("state", sa.String(length=255), nullable=False),
            sa.Column("nonce", sa.String(length=255), nullable=False),
            sa.Column("code_verifier", sa.Text(), nullable=False),
            sa.Column("next_url", sa.Text(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint("state", name="uq_oidc_login_states_state"),
        )
        op.create_index("ix_oidc_login_states_state", "oidc_login_states", ["state"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_table(inspector, "oidc_login_states"):
        if _has_index(inspector, "oidc_login_states", "ix_oidc_login_states_state"):
            op.drop_index("ix_oidc_login_states_state", table_name="oidc_login_states")
        op.drop_table("oidc_login_states")
    inspector = sa.inspect(bind)
    if _has_table(inspector, "user_identities"):
        if _has_index(inspector, "user_identities", "ix_user_identities_user_id"):
            op.drop_index("ix_user_identities_user_id", table_name="user_identities")
        op.drop_table("user_identities")
