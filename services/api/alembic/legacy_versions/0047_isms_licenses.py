"""manage ISMS licenses as asset entities

Revision ID: 0047_isms_licenses
Revises: 0046_unify_isms_assets
Create Date: 2026-05-27
"""

from __future__ import annotations

import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0047_isms_licenses"
down_revision = "0046_unify_isms_assets"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("select to_regclass(:name)"), {"name": table_name}
        ).scalar()
    )


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("""
                select 1
                from information_schema.columns
                where table_name = :table_name
                  and column_name = :column_name
                """),
            {"table_name": table_name, "column_name": column_name},
        ).scalar()
    )


def _constraint_exists(bind, table_name: str, constraint_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("""
                select 1
                from information_schema.table_constraints
                where table_name = :table_name
                  and constraint_name = :constraint_name
                """),
            {"table_name": table_name, "constraint_name": constraint_name},
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "isms_licenses"):
        op.create_table(
            "isms_licenses",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(length=256), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
            ),
            sa.UniqueConstraint("name", name="uq_isms_licenses_name"),
        )
        op.create_index("ix_isms_licenses_name", "isms_licenses", ["name"])
        op.create_index("ix_isms_licenses_created_at", "isms_licenses", ["created_at"])

    if not _column_exists(bind, "risk_assets", "license_id"):
        op.add_column(
            "risk_assets",
            sa.Column("license_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_index("ix_risk_assets_license_id", "risk_assets", ["license_id"])
        op.create_foreign_key(
            "fk_risk_assets_license_id_isms_licenses",
            "risk_assets",
            "isms_licenses",
            ["license_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Backfill existing free-text license values into managed license entities.
    rows = bind.execute(sa.text("""
            select distinct trim(license) as name
            from risk_assets
            where license is not null and trim(license) <> ''
            order by trim(license)
            """)).mappings().all()
    now = datetime.utcnow()
    for row in rows:
        name = row["name"]
        license_id = bind.execute(
            sa.text("select id from isms_licenses where lower(name) = lower(:name)"),
            {"name": name},
        ).scalar()
        if not license_id:
            license_id = uuid.uuid4()
            bind.execute(
                sa.text("""
                    insert into isms_licenses
                        (id, name, description, created_at, updated_at)
                    values
                        (:id, :name, '', :created_at, :updated_at)
                    """),
                {"id": license_id, "name": name, "created_at": now, "updated_at": now},
            )
        bind.execute(
            sa.text("""
                update risk_assets
                set license_id = :license_id
                where license is not null
                  and trim(license) <> ''
                  and lower(trim(license)) = lower(:name)
                  and license_id is null
                """),
            {"license_id": license_id, "name": name},
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "risk_assets", "license_id"):
        if _constraint_exists(
            bind, "risk_assets", "fk_risk_assets_license_id_isms_licenses"
        ):
            op.drop_constraint(
                "fk_risk_assets_license_id_isms_licenses",
                "risk_assets",
                type_="foreignkey",
            )
        op.drop_index("ix_risk_assets_license_id", table_name="risk_assets")
        op.drop_column("risk_assets", "license_id")

    if _table_exists(bind, "isms_licenses"):
        op.drop_index("ix_isms_licenses_created_at", table_name="isms_licenses")
        op.drop_index("ix_isms_licenses_name", table_name="isms_licenses")
        op.drop_table("isms_licenses")
