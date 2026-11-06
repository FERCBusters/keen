"""Add the OAuth records AI agents use to reach Keen over MCP.

Registered clients, each person's grants, consent requests waiting for an
answer, authorisation codes, and access and refresh tokens. Codes and tokens
are kept only as SHA-256 digests.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045_mcp_oauth"
down_revision = "0044_posture_risk_treatment"
branch_labels = None
depends_on = None


def _id():
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _reference(name, table, *, index=True):
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(f"{table}.id", ondelete="CASCADE"),
        nullable=False,
        index=index,
    )


def upgrade():
    op.create_table(
        "mcp_clients",
        _id(),
        sa.Column("client_id", sa.String(64), nullable=False, unique=True),
        sa.Column("client_name", sa.String(128), nullable=False),
        sa.Column("info", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "mcp_grants",
        _id(),
        _reference("user_id", "users"),
        _reference("client_pk", "mcp_clients"),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("mfa_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "mcp_consent_requests",
        _id(),
        sa.Column("request_hash", sa.String(64), nullable=False, unique=True),
        _reference("client_pk", "mcp_clients"),
        sa.Column("scopes", postgresql.JSONB(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
    )
    op.create_table(
        "mcp_authorization_codes",
        _id(),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        _reference("grant_id", "mcp_grants"),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("used_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "mcp_tokens",
        _id(),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        _reference("grant_id", "mcp_grants"),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("kind in ('access','refresh')", name="ck_mcp_tokens_kind"),
    )


def downgrade():
    op.drop_table("mcp_tokens")
    op.drop_table("mcp_authorization_codes")
    op.drop_table("mcp_consent_requests")
    op.drop_table("mcp_grants")
    op.drop_table("mcp_clients")
