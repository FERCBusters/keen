"""risk assets and subcategories

Revision ID: 0025_risk_assets
Revises: 0024_risks
Create Date: 2026-05-20

"""

from __future__ import annotations

import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0025_risk_assets"
down_revision = "0024_risks"
branch_labels = None
depends_on = None


def _get_or_create_category(bind, name: str) -> uuid.UUID:
    categories = sa.table(
        "risk_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    row_id = bind.execute(
        sa.select(categories.c.id).where(
            sa.func.lower(categories.c.name) == name.lower()
        )
    ).scalar()
    if row_id:
        return row_id
    row_id = uuid.uuid4()
    now = datetime.utcnow()
    op.bulk_insert(
        categories,
        [{"id": row_id, "name": name, "created_at": now, "updated_at": now}],
    )
    return row_id


def _get_or_create_subcategory(bind, category_id: uuid.UUID, name: str) -> uuid.UUID:
    subcategories = sa.table(
        "risk_asset_subcategories",
        sa.Column("id", postgresql.UUID(as_uuid=True)),
        sa.Column("category_id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    row_id = bind.execute(
        sa.select(subcategories.c.id).where(
            subcategories.c.category_id == category_id,
            sa.func.lower(subcategories.c.name) == name.lower(),
        )
    ).scalar()
    if row_id:
        return row_id
    row_id = uuid.uuid4()
    now = datetime.utcnow()
    op.bulk_insert(
        subcategories,
        [
            {
                "id": row_id,
                "category_id": category_id,
                "name": name,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    return row_id


def upgrade():
    op.create_table(
        "risk_asset_subcategories",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("risk_categories.id", ondelete="CASCADE"),
            nullable=False,
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
        sa.UniqueConstraint(
            "category_id", "name", name="uq_risk_asset_subcategories_category_name"
        ),
    )
    op.create_index(
        "ix_risk_asset_subcategories_category_id",
        "risk_asset_subcategories",
        ["category_id"],
    )
    op.create_index(
        "ix_risk_asset_subcategories_name", "risk_asset_subcategories", ["name"]
    )

    op.create_table(
        "risk_assets",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("risk_categories.id"),
            nullable=False,
        ),
        sa.Column(
            "subcategory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("risk_asset_subcategories.id"),
            nullable=False,
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
        sa.UniqueConstraint(
            "name",
            "category_id",
            "subcategory_id",
            name="uq_risk_assets_name_category_subcategory",
        ),
    )
    op.create_index("ix_risk_assets_name", "risk_assets", ["name"])
    op.create_index("ix_risk_assets_category_id", "risk_assets", ["category_id"])
    op.create_index("ix_risk_assets_subcategory_id", "risk_assets", ["subcategory_id"])
    op.create_index("ix_risk_assets_created_at", "risk_assets", ["created_at"])

    bind = op.get_bind()
    it_id = _get_or_create_category(bind, "I.T")
    data_id = _get_or_create_category(bind, "Data")
    _get_or_create_subcategory(bind, it_id, "System software")
    _get_or_create_subcategory(bind, it_id, "Application software")
    _get_or_create_subcategory(bind, data_id, "General")

    # The previous risk register migration had asset/category directly on a risk.
    # The user confirmed there is no risk data yet, so this can be a structural
    # replacement rather than a lossy data migration.
    op.add_column(
        "risks",
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_risks_asset_id_risk_assets",
        "risks",
        "risk_assets",
        ["asset_id"],
        ["id"],
    )
    op.create_index("ix_risks_asset_id", "risks", ["asset_id"])

    # If someone had created test rows before upgrading, remove them rather than
    # attempting to infer reusable assets from the deprecated columns.
    bind.execute(sa.text("DELETE FROM risk_control_links"))
    bind.execute(sa.text("DELETE FROM risks"))

    op.alter_column("risks", "asset_id", nullable=False)
    op.drop_index("ix_risks_asset", table_name="risks")
    op.drop_index("ix_risks_category_id", table_name="risks")
    op.drop_column("risks", "asset")
    op.drop_column("risks", "category_id")


def downgrade():
    op.add_column(
        "risks",
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("risks", sa.Column("asset", sa.String(length=256), nullable=True))
    op.create_foreign_key(
        "risks_category_id_fkey",
        "risks",
        "risk_categories",
        ["category_id"],
        ["id"],
    )
    op.create_index("ix_risks_category_id", "risks", ["category_id"])
    op.create_index("ix_risks_asset", "risks", ["asset"])

    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM risk_control_links"))
    bind.execute(sa.text("DELETE FROM risks"))

    op.alter_column("risks", "category_id", nullable=False)
    op.alter_column("risks", "asset", nullable=False)
    op.drop_index("ix_risks_asset_id", table_name="risks")
    op.drop_constraint("fk_risks_asset_id_risk_assets", "risks", type_="foreignkey")
    op.drop_column("risks", "asset_id")

    op.drop_index("ix_risk_assets_created_at", table_name="risk_assets")
    op.drop_index("ix_risk_assets_subcategory_id", table_name="risk_assets")
    op.drop_index("ix_risk_assets_category_id", table_name="risk_assets")
    op.drop_index("ix_risk_assets_name", table_name="risk_assets")
    op.drop_table("risk_assets")
    op.drop_index(
        "ix_risk_asset_subcategories_name", table_name="risk_asset_subcategories"
    )
    op.drop_index(
        "ix_risk_asset_subcategories_category_id",
        table_name="risk_asset_subcategories",
    )
    op.drop_table("risk_asset_subcategories")
