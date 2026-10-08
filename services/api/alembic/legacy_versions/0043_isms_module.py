"""add ISMS module

Revision ID: 0043_isms_module
Revises: 0042_control_evidence_stats
Create Date: 2026-05-27
"""

from __future__ import annotations

import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0043_isms_module"
down_revision = "0042_control_evidence_stats"
branch_labels = None
depends_on = None

ISMS_READ = "isms.read"
ISMS_MANAGE = "isms.manage"
DEFAULT_BUSINESS_PROCESSES = [
    "Business Development",
    "Design & Implementation",
    "Platform Security & Operations",
    "Development & Support",
    "Finance & Administration",
    "Board",
]


def _insert_permission(code: str, description: str) -> None:
    bind = op.get_bind()
    permissions = sa.table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True)),
        sa.Column("code", sa.String(length=128)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
    )
    exists = bind.execute(
        sa.select(permissions.c.id).where(permissions.c.code == code)
    ).scalar()
    if not exists:
        op.bulk_insert(
            permissions,
            [
                {
                    "id": uuid.uuid4(),
                    "code": code,
                    "description": description,
                    "created_at": datetime.utcnow(),
                }
            ],
        )


def upgrade() -> None:
    op.create_table(
        "isms_objectives",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("requirement", sa.Text(), nullable=False, server_default=""),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("metric", sa.Text(), nullable=False, server_default=""),
        sa.Column("completion_method", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "resource_requirements_text", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completion_target_date", sa.Date(), nullable=True),
        sa.Column("evaluation_method", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="not_started"
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.CheckConstraint(
            "status in ('not_started','in_progress','completed','deferred','superseded')",
            name="ck_isms_objectives_status",
        ),
    )
    op.create_index(
        "ix_isms_objectives_owner_user_id", "isms_objectives", ["owner_user_id"]
    )
    op.create_index(
        "ix_isms_objectives_completion_target_date",
        "isms_objectives",
        ["completion_target_date"],
    )
    op.create_index("ix_isms_objectives_status", "isms_objectives", ["status"])
    op.create_index("ix_isms_objectives_created_at", "isms_objectives", ["created_at"])

    op.create_table(
        "isms_objective_resource_users",
        sa.Column(
            "objective_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_objectives.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )

    op.create_table(
        "isms_documents",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column(
            "document_type",
            sa.String(length=32),
            nullable=False,
            server_default="policy",
        ),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("external_url", sa.String(length=2048), nullable=True),
        sa.Column("storage_uri", sa.String(length=512), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column(
            "uploaded_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )
    op.create_index("ix_isms_documents_title", "isms_documents", ["title"])
    op.create_index(
        "ix_isms_documents_document_type", "isms_documents", ["document_type"]
    )
    op.create_index("ix_isms_documents_created_at", "isms_documents", ["created_at"])

    op.create_table(
        "isms_org_nodes",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_org_nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column(
            "node_type", sa.String(length=64), nullable=False, server_default="role"
        ),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )
    op.create_index("ix_isms_org_nodes_parent_id", "isms_org_nodes", ["parent_id"])
    op.create_index("ix_isms_org_nodes_name", "isms_org_nodes", ["name"])
    op.create_index("ix_isms_org_nodes_node_type", "isms_org_nodes", ["node_type"])
    op.create_index("ix_isms_org_nodes_created_at", "isms_org_nodes", ["created_at"])

    op.create_table(
        "isms_org_node_users",
        sa.Column(
            "org_node_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_org_nodes.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "relationship_type",
            sa.String(length=32),
            primary_key=True,
            nullable=False,
            server_default="member",
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )

    op.create_table(
        "isms_assets",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("asset", sa.String(length=256), nullable=False),
        sa.Column("license", sa.String(length=256), nullable=False, server_default=""),
        sa.Column(
            "owner_org_node_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_org_nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "register_held_by_org_node_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_org_nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )
    op.create_index("ix_isms_assets_asset", "isms_assets", ["asset"])
    op.create_index(
        "ix_isms_assets_owner_org_node_id", "isms_assets", ["owner_org_node_id"]
    )
    op.create_index(
        "ix_isms_assets_register_held_by_org_node_id",
        "isms_assets",
        ["register_held_by_org_node_id"],
    )
    op.create_index("ix_isms_assets_created_at", "isms_assets", ["created_at"])

    op.create_table(
        "isms_business_processes",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.UniqueConstraint("name", name="uq_isms_business_processes_name"),
    )
    op.create_index(
        "ix_isms_business_processes_name", "isms_business_processes", ["name"]
    )

    op.create_table(
        "isms_application_configuration_entries",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_documents.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_assets.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "org_node_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_org_nodes.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "business_process_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_business_processes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value", sa.String(length=16), nullable=False, server_default="Low"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.CheckConstraint(
            "source_type in ('document','person','asset','org_node')",
            name="ck_isms_app_config_source_type",
        ),
        sa.CheckConstraint(
            "value in ('Low','Medium','High')", name="ck_isms_app_config_value"
        ),
    )
    for col in [
        "source_type",
        "document_id",
        "user_id",
        "asset_id",
        "org_node_id",
        "business_process_id",
        "value",
        "created_at",
    ]:
        op.create_index(
            f"ix_isms_application_configuration_entries_{col}",
            "isms_application_configuration_entries",
            [col],
        )

    op.create_table(
        "isms_meetings",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "title",
            sa.String(length=256),
            nullable=False,
            server_default="ISMS Meeting",
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("agenda_minutes_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )
    op.create_index("ix_isms_meetings_title", "isms_meetings", ["title"])
    op.create_index("ix_isms_meetings_date", "isms_meetings", ["date"])
    op.create_index("ix_isms_meetings_created_at", "isms_meetings", ["created_at"])

    op.create_table(
        "isms_meeting_attendees",
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_meetings.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "attendance_type", sa.String(length=16), primary_key=True, nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.CheckConstraint(
            "attendance_type in ('attendee','apology')",
            name="ck_isms_meeting_attendees_type",
        ),
    )

    op.create_table(
        "isms_meeting_links",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "link_type",
            sa.String(length=32),
            nullable=False,
            server_default="external_url",
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_documents.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )
    op.create_index(
        "ix_isms_meeting_links_meeting_id", "isms_meeting_links", ["meeting_id"]
    )
    op.create_index(
        "ix_isms_meeting_links_link_type", "isms_meeting_links", ["link_type"]
    )
    op.create_index(
        "ix_isms_meeting_links_document_id", "isms_meeting_links", ["document_id"]
    )

    op.create_table(
        "isms_entity_control_links",
        sa.Column(
            "entity_type", sa.String(length=64), primary_key=True, nullable=False
        ),
        sa.Column(
            "entity_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "framework_slug", sa.String(length=64), primary_key=True, nullable=False
        ),
        sa.Column(
            "control_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control_items.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )
    op.create_index(
        "ix_isms_entity_control_links_framework_slug",
        "isms_entity_control_links",
        ["framework_slug"],
    )
    op.create_index(
        "ix_isms_entity_control_links_entity",
        "isms_entity_control_links",
        ["entity_type", "entity_id"],
    )

    op.create_table(
        "isms_entity_clause_links",
        sa.Column(
            "entity_type", sa.String(length=64), primary_key=True, nullable=False
        ),
        sa.Column(
            "entity_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "framework_slug", sa.String(length=64), primary_key=True, nullable=False
        ),
        sa.Column(
            "clause_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("framework_clauses.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )
    op.create_index(
        "ix_isms_entity_clause_links_framework_slug",
        "isms_entity_clause_links",
        ["framework_slug"],
    )
    op.create_index(
        "ix_isms_entity_clause_links_entity",
        "isms_entity_clause_links",
        ["entity_type", "entity_id"],
    )

    _insert_permission(
        ISMS_READ,
        "View ISMS objectives, documents, organisation chart, assets, meetings and matrices.",
    )
    _insert_permission(
        ISMS_MANAGE,
        "Create, edit and delete ISMS objectives, documents, organisation chart, assets, meetings and matrices.",
    )

    bp_table = sa.table(
        "isms_business_processes",
        sa.Column("id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.String(length=256)),
        sa.Column("sort_order", sa.Integer()),
        sa.Column("is_default", sa.Boolean()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    now = datetime.utcnow()
    op.bulk_insert(
        bp_table,
        [
            {
                "id": uuid.uuid4(),
                "name": name,
                "sort_order": i * 10,
                "is_default": True,
                "created_at": now,
                "updated_at": now,
            }
            for i, name in enumerate(DEFAULT_BUSINESS_PROCESSES, start=1)
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    permissions = sa.table("permissions", sa.Column("code", sa.String(length=128)))
    bind.execute(
        permissions.delete().where(permissions.c.code.in_([ISMS_READ, ISMS_MANAGE]))
    )
    for table in [
        "isms_entity_clause_links",
        "isms_entity_control_links",
        "isms_meeting_links",
        "isms_meeting_attendees",
        "isms_meetings",
        "isms_application_configuration_entries",
        "isms_business_processes",
        "isms_assets",
        "isms_org_node_users",
        "isms_org_nodes",
        "isms_documents",
        "isms_objective_resource_users",
        "isms_objectives",
    ]:
        op.drop_table(table)
