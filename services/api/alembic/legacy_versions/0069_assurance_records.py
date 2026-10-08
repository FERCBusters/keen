"""Add independent people, personnel assurance, vendors and asset relationships.

Revision ID: 0069_assurance_records
Revises: 0068_unified_evidence
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision = "0069_assurance_records"
down_revision = "0068_unified_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "isms_people",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("email", sa.String(256), nullable=False, server_default=""),
        sa.Column("position", sa.String(256), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), unique=True),
        sa.Column("org_node_id", UUID(as_uuid=True), sa.ForeignKey("isms_org_nodes.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_isms_people_name", "isms_people", ["name"])
    op.create_table(
        "isms_vendors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), unique=True, nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("website", sa.String(2048), nullable=False, server_default=""),
        sa.Column("contact", sa.String(256), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.add_column("risk_assets", sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("isms_vendors.id", ondelete="SET NULL")))
    op.create_index("ix_risk_assets_vendor_id", "risk_assets", ["vendor_id"])
    op.create_table(
        "isms_person_assets",
        sa.Column("person_id", UUID(as_uuid=True), sa.ForeignKey("isms_people.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("asset_id", UUID(as_uuid=True), sa.ForeignKey("risk_assets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("relationship_type", sa.String(32), nullable=False, server_default="uses"),
    )
    op.create_table(
        "isms_person_assurances",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("person_id", UUID(as_uuid=True), sa.ForeignKey("isms_people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("source_system", sa.String(128), nullable=False, server_default=""),
        sa.Column("evidence_url", sa.String(2048), nullable=False, server_default=""),
        sa.Column("completed_at", sa.Date(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_isms_person_assurances_person_id", "isms_person_assurances", ["person_id"])


def downgrade():
    op.drop_index("ix_isms_person_assurances_person_id", table_name="isms_person_assurances")
    op.drop_table("isms_person_assurances")
    op.drop_table("isms_person_assets")
    op.drop_index("ix_risk_assets_vendor_id", table_name="risk_assets")
    op.drop_column("risk_assets", "vendor_id")
    op.drop_table("isms_vendors")
    op.drop_index("ix_isms_people_name", table_name="isms_people")
    op.drop_table("isms_people")
