"""Cloud vendor posture: providers, their Vendor and the latest scope results."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027_cloud_posture"
down_revision = "0026_posture_risk_links"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cloud_providers",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column(
            "vendor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_vendors.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attempted_at", sa.DateTime(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.String(32), nullable=False, server_default=""),
        sa.Column("refresh_requested_at", sa.DateTime(), nullable=True),
        sa.Column("activity_at", sa.DateTime(), nullable=True),
        sa.Column("activity_error", sa.String(32), nullable=False, server_default=""),
        sa.Column(
            "activity_truncated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index("ix_cloud_providers_vendor_id", "cloud_providers", ["vendor_id"])
    op.create_table(
        "cloud_posture_scopes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "provider",
            sa.String(32),
            sa.ForeignKey("cloud_providers.provider", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope_key", sa.String(128), nullable=False),
        sa.Column("label", sa.String(256), nullable=False, server_default=""),
        sa.Column("kind", sa.String(32), nullable=False, server_default=""),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column(
            "checks",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "identities",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.UniqueConstraint("provider", "scope_key", name="uq_cloud_posture_scope"),
    )


def downgrade():
    op.drop_table("cloud_posture_scopes")
    op.drop_table("cloud_providers")
