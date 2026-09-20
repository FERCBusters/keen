"""Allow audits to sample non-event Keen entities.

Revision ID: 0055_audit_sample_entities
Revises: 0054_backfill_access
Create Date: 2026-06-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0055_audit_sample_entities"
down_revision = "0054_backfill_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_evidence",
        sa.Column("entity_type", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "audit_evidence",
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_audit_evidence_entity_type", "audit_evidence", ["entity_type"])
    op.create_index("ix_audit_evidence_entity_id", "audit_evidence", ["entity_id"])
    op.create_unique_constraint(
        "uq_audit_evidence_entity",
        "audit_evidence",
        ["audit_id", "entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_audit_evidence_entity", "audit_evidence", type_="unique")
    op.drop_index("ix_audit_evidence_entity_id", table_name="audit_evidence")
    op.drop_index("ix_audit_evidence_entity_type", table_name="audit_evidence")
    op.drop_column("audit_evidence", "entity_id")
    op.drop_column("audit_evidence", "entity_type")
