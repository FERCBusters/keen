"""interested parties risk assessment

Revision ID: 0039_interested_parties
Revises: 0038_pestle_questions_changes
Create Date: 2026-05-26
"""

from __future__ import annotations

import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0039_interested_parties"
down_revision = "0038_pestle_questions_changes"
branch_labels = None
depends_on = None

INTERESTED_PARTIES_READ = "interested_parties.read"
INTERESTED_PARTIES_MANAGE = "interested_parties.manage"


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
        "interested_party_names",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
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
        sa.UniqueConstraint("name", name="uq_interested_party_names_name"),
    )
    op.create_index(
        "ix_interested_party_names_name", "interested_party_names", ["name"]
    )
    op.create_index(
        "ix_interested_party_names_created_at", "interested_party_names", ["created_at"]
    )

    op.create_table(
        "interested_party_natures",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
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
        sa.UniqueConstraint("name", name="uq_interested_party_natures_name"),
    )
    op.create_index(
        "ix_interested_party_natures_name", "interested_party_natures", ["name"]
    )
    op.create_index(
        "ix_interested_party_natures_created_at",
        "interested_party_natures",
        ["created_at"],
    )

    op.create_table(
        "interested_parties",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("framework_slug", sa.String(length=64), nullable=False),
        sa.Column(
            "name_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interested_party_names.id"),
            nullable=False,
        ),
        sa.Column(
            "nature_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interested_party_natures.id"),
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
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.UniqueConstraint(
            "framework_slug",
            "name_id",
            "nature_id",
            name="uq_interested_parties_framework_name_nature",
        ),
    )
    op.create_index(
        "ix_interested_parties_framework_slug", "interested_parties", ["framework_slug"]
    )
    op.create_index("ix_interested_parties_name_id", "interested_parties", ["name_id"])
    op.create_index(
        "ix_interested_parties_nature_id", "interested_parties", ["nature_id"]
    )
    op.create_index(
        "ix_interested_parties_created_at", "interested_parties", ["created_at"]
    )

    op.create_table(
        "interested_party_control_links",
        sa.Column(
            "interested_party_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interested_parties.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
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
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )
    op.create_index(
        "ix_interested_party_control_links_framework_slug",
        "interested_party_control_links",
        ["framework_slug"],
    )

    op.create_table(
        "interested_party_communications",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "interested_party_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interested_parties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("when", sa.String(length=64), nullable=False),
        sa.Column("with_whom", sa.String(length=128), nullable=False),
        sa.Column(
            "methods",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.CheckConstraint(
            "event in ('Business as Usual','Incident','Alert','Notifiable Event')",
            name="ck_interested_party_communications_event",
        ),
        sa.CheckConstraint(
            "\"when\" in ('As Required','Upon Identification','Routine','Within 24 hours','Within 48 hours','Within 72 hours','Within a week','Within a month')",
            name="ck_interested_party_communications_when",
        ),
        sa.CheckConstraint(
            "with_whom in ('Individual member','Individual staff member','Senior Supplier Point of Contact','Supplier Point of Contact','NCSC','ICO','Sender')",
            name="ck_interested_party_communications_with_whom",
        ),
    )
    op.create_index(
        "ix_interested_party_communications_interested_party_id",
        "interested_party_communications",
        ["interested_party_id"],
    )
    op.create_index(
        "ix_interested_party_communications_event",
        "interested_party_communications",
        ["event"],
    )
    op.create_index(
        "ix_interested_party_communications_when",
        "interested_party_communications",
        ["when"],
    )
    op.create_index(
        "ix_interested_party_communications_with_whom",
        "interested_party_communications",
        ["with_whom"],
    )
    op.create_index(
        "ix_interested_party_communications_created_at",
        "interested_party_communications",
        ["created_at"],
    )

    _insert_permission(
        INTERESTED_PARTIES_READ,
        "View Interested Parties assessments, communications and control mappings.",
    )
    _insert_permission(
        INTERESTED_PARTIES_MANAGE,
        "Create, edit and delete Interested Parties, lookup values, communications and control mappings.",
    )


def downgrade() -> None:
    bind = op.get_bind()
    permissions = sa.table("permissions", sa.Column("code", sa.String(length=128)))
    bind.execute(
        permissions.delete().where(
            permissions.c.code.in_([INTERESTED_PARTIES_READ, INTERESTED_PARTIES_MANAGE])
        )
    )
    op.drop_index(
        "ix_interested_party_communications_created_at",
        table_name="interested_party_communications",
    )
    op.drop_index(
        "ix_interested_party_communications_with_whom",
        table_name="interested_party_communications",
    )
    op.drop_index(
        "ix_interested_party_communications_when",
        table_name="interested_party_communications",
    )
    op.drop_index(
        "ix_interested_party_communications_event",
        table_name="interested_party_communications",
    )
    op.drop_index(
        "ix_interested_party_communications_interested_party_id",
        table_name="interested_party_communications",
    )
    op.drop_table("interested_party_communications")
    op.drop_index(
        "ix_interested_party_control_links_framework_slug",
        table_name="interested_party_control_links",
    )
    op.drop_table("interested_party_control_links")
    op.drop_index("ix_interested_parties_created_at", table_name="interested_parties")
    op.drop_index("ix_interested_parties_nature_id", table_name="interested_parties")
    op.drop_index("ix_interested_parties_name_id", table_name="interested_parties")
    op.drop_index(
        "ix_interested_parties_framework_slug", table_name="interested_parties"
    )
    op.drop_table("interested_parties")
    op.drop_index(
        "ix_interested_party_natures_created_at", table_name="interested_party_natures"
    )
    op.drop_index(
        "ix_interested_party_natures_name", table_name="interested_party_natures"
    )
    op.drop_table("interested_party_natures")
    op.drop_index(
        "ix_interested_party_names_created_at", table_name="interested_party_names"
    )
    op.drop_index("ix_interested_party_names_name", table_name="interested_party_names")
    op.drop_table("interested_party_names")
