"""store user authz version in database

Revision ID: 0063_user_authz_version
Revises: 0062_permission_updates
Create Date: 2026-06-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0063_user_authz_version"
down_revision = "0062_permission_updates"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return any(col.get("name") == column for col in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_column(inspector, "users", "authz_version"):
        op.add_column(
            "users",
            sa.Column(
                "authz_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
        op.alter_column("users", "authz_version", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_column(inspector, "users", "authz_version"):
        op.drop_column("users", "authz_version")
