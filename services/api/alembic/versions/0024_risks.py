"""risks

Revision ID: 0024_risks
Revises: 0023_event_incidents
Create Date: 2026-05-20

"""

from __future__ import annotations

import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0024_risks"
down_revision = "0023_event_incidents"
branch_labels = None
depends_on = None

RISK_READ = "risk.read"
RISK_MANAGE = "risk.manage"


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
        "risk_categories",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("name", name="uq_risk_categories_name"),
    )
    op.create_index("ix_risk_categories_name", "risk_categories", ["name"])

    op.create_table(
        "risks",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("asset", sa.String(length=256), nullable=False),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("risk_categories.id"),
            nullable=False,
        ),
        sa.Column(
            "risk_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "risk_owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("threat_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("threat_score", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "vulnerability_score", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("impact_score", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("risk_score", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "residual_vulnerability_score",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "residual_impact_score", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "residual_risk_score", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "threat_score between 0 and 5", name="ck_risks_threat_score"
        ),
        sa.CheckConstraint(
            "vulnerability_score between 0 and 5",
            name="ck_risks_vulnerability_score",
        ),
        sa.CheckConstraint(
            "impact_score between 0 and 5", name="ck_risks_impact_score"
        ),
        sa.CheckConstraint(
            "residual_vulnerability_score between 0 and 5",
            name="ck_risks_residual_vulnerability_score",
        ),
        sa.CheckConstraint(
            "residual_impact_score between 0 and 5",
            name="ck_risks_residual_impact_score",
        ),
    )
    op.create_index("ix_risks_asset", "risks", ["asset"])
    op.create_index("ix_risks_category_id", "risks", ["category_id"])
    op.create_index("ix_risks_risk_owner_user_id", "risks", ["risk_owner_user_id"])
    op.create_index("ix_risks_created_at", "risks", ["created_at"])

    op.create_table(
        "risk_control_links",
        sa.Column(
            "risk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("risks.id", ondelete="CASCADE"),
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
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_risk_control_links_framework_slug",
        "risk_control_links",
        ["framework_slug"],
    )

    categories = sa.table(
        "risk_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    now = datetime.utcnow()
    op.bulk_insert(
        categories,
        [
            {"id": uuid.uuid4(), "name": "I.T", "created_at": now, "updated_at": now},
            {"id": uuid.uuid4(), "name": "Data", "created_at": now, "updated_at": now},
        ],
    )

    _insert_permission(RISK_READ, "View risks and risk/control mappings.")
    _insert_permission(
        RISK_MANAGE,
        "Create, edit and delete risks, risk categories and framework-specific risk/control mappings.",
    )


def downgrade():
    bind = op.get_bind()
    permissions = sa.table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True)),
        sa.Column("code", sa.String(length=128)),
    )
    user_permissions = sa.table(
        "user_permissions",
        sa.Column("permission_id", postgresql.UUID(as_uuid=True)),
    )
    group_permissions = sa.table(
        "group_permissions",
        sa.Column("permission_id", postgresql.UUID(as_uuid=True)),
    )
    for code in (RISK_READ, RISK_MANAGE):
        perm_id = bind.execute(
            sa.select(permissions.c.id).where(permissions.c.code == code)
        ).scalar()
        if perm_id:
            bind.execute(
                user_permissions.delete().where(
                    user_permissions.c.permission_id == perm_id
                )
            )
            bind.execute(
                group_permissions.delete().where(
                    group_permissions.c.permission_id == perm_id
                )
            )
            bind.execute(permissions.delete().where(permissions.c.id == perm_id))

    op.drop_index(
        "ix_risk_control_links_framework_slug", table_name="risk_control_links"
    )
    op.drop_table("risk_control_links")
    op.drop_index("ix_risks_created_at", table_name="risks")
    op.drop_index("ix_risks_risk_owner_user_id", table_name="risks")
    op.drop_index("ix_risks_category_id", table_name="risks")
    op.drop_index("ix_risks_asset", table_name="risks")
    op.drop_table("risks")
    op.drop_index("ix_risk_categories_name", table_name="risk_categories")
    op.drop_table("risk_categories")
