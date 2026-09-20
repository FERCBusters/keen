"""audit engagements

Revision ID: 0018_audit_engagements
Revises: 0017_query_performance_indexes
Create Date: 2026-05-19

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0018_audit_engagements"
down_revision = "0017_query_performance_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "audits",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("framework_slug", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="open"
        ),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("final_report_storage_uri", sa.String(length=512), nullable=True),
        sa.Column("final_report_filename", sa.String(length=255), nullable=True),
        sa.Column("final_report_content_type", sa.String(length=128), nullable=True),
        sa.Column("final_report_sha256", sa.String(length=64), nullable=True),
        sa.Column("final_report_size_bytes", sa.Integer(), nullable=True),
        sa.Column("final_report_uploaded_at", sa.DateTime(), nullable=True),
        sa.Column(
            "final_report_uploaded_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "status in ('open','in_progress','completed','archived')",
            name="ck_audits_status",
        ),
    )
    op.create_index("ix_audits_framework_slug", "audits", ["framework_slug"])
    op.create_index("ix_audits_created_by_user_id", "audits", ["created_by_user_id"])
    op.create_index("ix_audits_updated_at", "audits", ["updated_at"])

    op.create_table(
        "audit_scoped_controls",
        sa.Column(
            "audit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audits.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "control_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control_items.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_audit_scoped_controls_audit_id", "audit_scoped_controls", ["audit_id"]
    )
    op.create_index(
        "ix_audit_scoped_controls_control_item_id",
        "audit_scoped_controls",
        ["control_item_id"],
    )

    op.create_table(
        "audit_evidence",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "audit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("evidence_url", sa.String(length=2048), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "added_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint("audit_id", "event_id", name="uq_audit_evidence_event"),
    )
    op.create_index("ix_audit_evidence_audit_id", "audit_evidence", ["audit_id"])
    op.create_index("ix_audit_evidence_event_id", "audit_evidence", ["event_id"])
    op.create_index(
        "ix_audit_evidence_added_by_user_id", "audit_evidence", ["added_by_user_id"]
    )

    op.create_table(
        "audit_attendees",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "audit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("email", sa.String(length=256), nullable=True),
        sa.Column("role", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_attendees_audit_id", "audit_attendees", ["audit_id"])

    op.create_table(
        "audit_findings",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "audit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "control_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="open"
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind in ('major_nc','minor_nc','ofi','best_practice')",
            name="ck_audit_findings_kind",
        ),
        sa.CheckConstraint(
            "status in ('open','closed','accepted')", name="ck_audit_findings_status"
        ),
    )
    op.create_index("ix_audit_findings_audit_id", "audit_findings", ["audit_id"])
    op.create_index("ix_audit_findings_kind", "audit_findings", ["kind"])
    op.create_index(
        "ix_audit_findings_control_item_id", "audit_findings", ["control_item_id"]
    )
    op.create_index(
        "ix_audit_findings_created_by_user_id", "audit_findings", ["created_by_user_id"]
    )

    # Permission seed. Grant these to a user/group such as "auditors" as needed.
    op.execute("""
        INSERT INTO permissions (id, code, description, created_at)
        SELECT gen_random_uuid(), 'audits.read', 'View audit engagements, sampled evidence, scope, attendees, findings and final reports.', NOW()
        WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code='audits.read');
    """)
    op.execute("""
        INSERT INTO permissions (id, code, description, created_at)
        SELECT gen_random_uuid(), 'audits.manage', 'Create and maintain audit engagements, sampled evidence, scope, attendees, findings and final reports.', NOW()
        WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code='audits.manage');
    """)


def downgrade():
    op.execute("DELETE FROM permissions WHERE code in ('audits.read', 'audits.manage')")
    op.drop_index("ix_audit_findings_created_by_user_id", table_name="audit_findings")
    op.drop_index("ix_audit_findings_control_item_id", table_name="audit_findings")
    op.drop_index("ix_audit_findings_kind", table_name="audit_findings")
    op.drop_index("ix_audit_findings_audit_id", table_name="audit_findings")
    op.drop_table("audit_findings")

    op.drop_index("ix_audit_attendees_audit_id", table_name="audit_attendees")
    op.drop_table("audit_attendees")

    op.drop_index("ix_audit_evidence_added_by_user_id", table_name="audit_evidence")
    op.drop_index("ix_audit_evidence_event_id", table_name="audit_evidence")
    op.drop_index("ix_audit_evidence_audit_id", table_name="audit_evidence")
    op.drop_table("audit_evidence")

    op.drop_index(
        "ix_audit_scoped_controls_control_item_id", table_name="audit_scoped_controls"
    )
    op.drop_index(
        "ix_audit_scoped_controls_audit_id", table_name="audit_scoped_controls"
    )
    op.drop_table("audit_scoped_controls")

    op.drop_index("ix_audits_updated_at", table_name="audits")
    op.drop_index("ix_audits_created_by_user_id", table_name="audits")
    op.drop_index("ix_audits_framework_slug", table_name="audits")
    op.drop_table("audits")
