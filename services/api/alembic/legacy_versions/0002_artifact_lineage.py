"""artifact lineage + metadata

Revision ID: 0002_artifact_lineage
Revises: 0001_initial
Create Date: 2026-01-06

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_artifact_lineage"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "artifacts",
        sa.Column("parent_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_artifacts_parent_artifact_id", "artifacts", ["parent_artifact_id"]
    )
    op.add_column(
        "artifacts",
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade():
    op.drop_column("artifacts", "metadata")
    op.drop_index("ix_artifacts_parent_artifact_id", table_name="artifacts")
    op.drop_column("artifacts", "parent_artifact_id")
