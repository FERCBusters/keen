"""pestle impact assessments

Revision ID: 0037_pestle_assessments
Revises: 0036_risk_mitigator
Create Date: 2026-05-26

"""

from __future__ import annotations

import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0037_pestle_assessments"
down_revision = "0036_risk_mitigator"
branch_labels = None
depends_on = None

PESTLE_READ = "pestle.read"
PESTLE_MANAGE = "pestle.manage"

_RELEVANCE_LEVELS = [
    ("na", "N/A", 0),
    ("high", "1 High", 1),
    ("medium", "2 Medium", 2),
    ("low", "3 Low", 3),
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


def upgrade():
    op.create_table(
        "pestle_relevance_levels",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.UniqueConstraint("code", name="uq_pestle_relevance_levels_code"),
    )
    op.create_index(
        "ix_pestle_relevance_levels_code", "pestle_relevance_levels", ["code"]
    )

    relevance = sa.table(
        "pestle_relevance_levels",
        sa.Column("id", postgresql.UUID(as_uuid=True)),
        sa.Column("code", sa.String(length=16)),
        sa.Column("label", sa.String(length=32)),
        sa.Column("sort_order", sa.Integer()),
        sa.Column("created_at", sa.DateTime()),
    )
    now = datetime.utcnow()
    op.bulk_insert(
        relevance,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "label": label,
                "sort_order": order,
                "created_at": now,
            }
            for code, label, order in _RELEVANCE_LEVELS
        ],
    )

    op.create_table(
        "pestle_items",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("framework_slug", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("lens", sa.String(length=16), nullable=False),
        sa.Column("item", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "overall_relevance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pestle_relevance_levels.id"),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
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
            "type in ('Political','Economical','Social','Technological','Legal','Environmental','Ethical')",
            name="ck_pestle_items_type",
        ),
        sa.CheckConstraint(
            "lens in ('Internal','External')", name="ck_pestle_items_lens"
        ),
    )
    op.create_index(
        "ix_pestle_items_framework_slug", "pestle_items", ["framework_slug"]
    )
    op.create_index("ix_pestle_items_type", "pestle_items", ["type"])
    op.create_index("ix_pestle_items_lens", "pestle_items", ["lens"])
    op.create_index(
        "ix_pestle_items_overall_relevance_id", "pestle_items", ["overall_relevance_id"]
    )
    op.create_index("ix_pestle_items_created_at", "pestle_items", ["created_at"])

    op.create_table(
        "pestle_business_processes",
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
        sa.UniqueConstraint("name", name="uq_pestle_business_processes_name"),
    )
    op.create_index(
        "ix_pestle_business_processes_name", "pestle_business_processes", ["name"]
    )
    op.create_index(
        "ix_pestle_business_processes_created_at",
        "pestle_business_processes",
        ["created_at"],
    )

    op.create_table(
        "pestle_business_process_relevance",
        sa.Column(
            "pestle_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pestle_items.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "business_process_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pestle_business_processes.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "relevance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pestle_relevance_levels.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )
    op.create_index(
        "ix_pestle_business_process_relevance_relevance_id",
        "pestle_business_process_relevance",
        ["relevance_id"],
    )

    op.create_table(
        "pestle_clause_relevance",
        sa.Column(
            "pestle_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pestle_items.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "clause_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("framework_clauses.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "relevance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pestle_relevance_levels.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
    )
    op.create_index(
        "ix_pestle_clause_relevance_clause_id", "pestle_clause_relevance", ["clause_id"]
    )
    op.create_index(
        "ix_pestle_clause_relevance_relevance_id",
        "pestle_clause_relevance",
        ["relevance_id"],
    )

    _insert_permission(
        PESTLE_READ, "View PESTLE(E) impact assessments and relevance mappings."
    )
    _insert_permission(
        PESTLE_MANAGE,
        "Create, edit and delete PESTLE(E) items, business processes and relevance mappings.",
    )


def downgrade():
    bind = op.get_bind()
    permissions = sa.table("permissions", sa.Column("code", sa.String(length=128)))
    bind.execute(
        permissions.delete().where(permissions.c.code.in_([PESTLE_READ, PESTLE_MANAGE]))
    )

    op.drop_index(
        "ix_pestle_clause_relevance_relevance_id", table_name="pestle_clause_relevance"
    )
    op.drop_index(
        "ix_pestle_clause_relevance_clause_id", table_name="pestle_clause_relevance"
    )
    op.drop_table("pestle_clause_relevance")

    op.drop_index(
        "ix_pestle_business_process_relevance_relevance_id",
        table_name="pestle_business_process_relevance",
    )
    op.drop_table("pestle_business_process_relevance")

    op.drop_index(
        "ix_pestle_business_processes_created_at",
        table_name="pestle_business_processes",
    )
    op.drop_index(
        "ix_pestle_business_processes_name", table_name="pestle_business_processes"
    )
    op.drop_table("pestle_business_processes")

    op.drop_index("ix_pestle_items_created_at", table_name="pestle_items")
    op.drop_index("ix_pestle_items_overall_relevance_id", table_name="pestle_items")
    op.drop_index("ix_pestle_items_lens", table_name="pestle_items")
    op.drop_index("ix_pestle_items_type", table_name="pestle_items")
    op.drop_index("ix_pestle_items_framework_slug", table_name="pestle_items")
    op.drop_table("pestle_items")

    op.drop_index(
        "ix_pestle_relevance_levels_code", table_name="pestle_relevance_levels"
    )
    op.drop_table("pestle_relevance_levels")
