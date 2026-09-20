"""users

Revision ID: 0004_users
Revises: 0003_audit_logs
Create Date: 2026-01-11

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_users"
down_revision = "0003_audit_logs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("username", sa.String(length=128), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'normal'"),
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        # --- Preferences (UI display)
        # When true, the UI will display times in the user's preferred timezone.
        sa.Column(
            "pref_use_local_timezone",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # IANA timezone name (e.g. Australia/Melbourne). Optional.
        sa.Column("pref_timezone", sa.String(length=64), nullable=True),
        # UI theme id.
        sa.Column(
            "pref_theme",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'purple'"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
    )

    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_is_active", "users", ["is_active"])


def downgrade():
    op.drop_index("ix_users_is_active", table_name="users")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
