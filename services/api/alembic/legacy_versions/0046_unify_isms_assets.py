"""unify ISMS assets with risk assets

Revision ID: 0046_unify_isms_assets
Revises: 0045_isms_goal_metric
Create Date: 2026-05-27
"""

from __future__ import annotations

import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0046_unify_isms_assets"
down_revision = "0045_isms_goal_metric"
branch_labels = None
depends_on = None


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


def _table_exists(bind, table_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("select to_regclass(:name)"), {"name": table_name}
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column(
        "risk_assets",
        sa.Column("license", sa.String(length=256), nullable=False, server_default=""),
    )
    op.add_column(
        "risk_assets",
        sa.Column("owner_org_node_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "risk_assets",
        sa.Column(
            "register_held_by_org_node_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.add_column(
        "risk_assets",
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "risk_assets",
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_risk_assets_owner_org_node_id", "risk_assets", ["owner_org_node_id"]
    )
    op.create_index(
        "ix_risk_assets_register_held_by_org_node_id",
        "risk_assets",
        ["register_held_by_org_node_id"],
    )
    op.create_foreign_key(
        "fk_risk_assets_owner_org_node_id_isms_org_nodes",
        "risk_assets",
        "isms_org_nodes",
        ["owner_org_node_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_risk_assets_register_held_by_org_node_id_isms_org_nodes",
        "risk_assets",
        "isms_org_nodes",
        ["register_held_by_org_node_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_risk_assets_created_by_user_id_users",
        "risk_assets",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Preserve any ISMS-only assets created before this unification by moving them
    # into the canonical CIA Triad risk asset register under a neutral bucket.
    if _table_exists(bind, "isms_assets"):
        category_id = bind.execute(
            sa.text(
                "select id from risk_categories where lower(name) = 'uncategorised'"
            )
        ).scalar()
        if not category_id:
            category_id = uuid.uuid4()
            bind.execute(
                sa.text("""
                    insert into risk_categories (id, name, created_at, updated_at)
                    values (:id, 'Uncategorised', :now, :now)
                    """),
                {"id": category_id, "now": datetime.utcnow()},
            )
        subcategory_id = bind.execute(
            sa.text("""
                select id from risk_asset_subcategories
                where category_id = :category_id and lower(name) = 'general'
                """),
            {"category_id": category_id},
        ).scalar()
        if not subcategory_id:
            subcategory_id = uuid.uuid4()
            bind.execute(
                sa.text("""
                    insert into risk_asset_subcategories
                        (id, category_id, name, created_at, updated_at)
                    values (:id, :category_id, 'General', :now, :now)
                    """),
                {
                    "id": subcategory_id,
                    "category_id": category_id,
                    "now": datetime.utcnow(),
                },
            )
        bind.execute(
            sa.text("""
                insert into risk_assets (
                    id, name, category_id, subcategory_id, license,
                    owner_org_node_id, register_held_by_org_node_id,
                    description, created_by_user_id, created_at, updated_at
                )
                select ia.id, ia.asset, :category_id, :subcategory_id, ia.license,
                       ia.owner_org_node_id, ia.register_held_by_org_node_id,
                       ia.description, ia.created_by_user_id, ia.created_at, ia.updated_at
                from isms_assets ia
                where not exists (select 1 from risk_assets ra where ra.id = ia.id)
                on conflict (name, category_id, subcategory_id) do nothing
                """),
            {"category_id": category_id, "subcategory_id": subcategory_id},
        )
        # If an ISMS asset collided with an existing canonical risk asset, point
        # matrix rows and generic ISMS links at the canonical record before the
        # application-configuration FK is moved.
        bind.execute(
            sa.text("""
                update isms_application_configuration_entries ace
                set asset_id = ra.id
                from isms_assets ia
                join risk_assets ra
                  on lower(ra.name) = lower(ia.asset)
                 and ra.category_id = :category_id
                 and ra.subcategory_id = :subcategory_id
                where ace.asset_id = ia.id
                  and not exists (select 1 from risk_assets existing where existing.id = ia.id)
                """),
            {"category_id": category_id, "subcategory_id": subcategory_id},
        )
        bind.execute(
            sa.text("""
                update isms_entity_control_links l
                set entity_id = ra.id
                from isms_assets ia
                join risk_assets ra
                  on lower(ra.name) = lower(ia.asset)
                 and ra.category_id = :category_id
                 and ra.subcategory_id = :subcategory_id
                where l.entity_type = 'asset'
                  and l.entity_id = ia.id
                  and not exists (select 1 from risk_assets existing where existing.id = ia.id)
                """),
            {"category_id": category_id, "subcategory_id": subcategory_id},
        )
        bind.execute(
            sa.text("""
                update isms_entity_clause_links l
                set entity_id = ra.id
                from isms_assets ia
                join risk_assets ra
                  on lower(ra.name) = lower(ia.asset)
                 and ra.category_id = :category_id
                 and ra.subcategory_id = :subcategory_id
                where l.entity_type = 'asset'
                  and l.entity_id = ia.id
                  and not exists (select 1 from risk_assets existing where existing.id = ia.id)
                """),
            {"category_id": category_id, "subcategory_id": subcategory_id},
        )

    if _constraint_exists(
        bind,
        "isms_application_configuration_entries",
        "isms_application_configuration_entries_asset_id_fkey",
    ):
        op.drop_constraint(
            "isms_application_configuration_entries_asset_id_fkey",
            "isms_application_configuration_entries",
            type_="foreignkey",
        )
    op.create_foreign_key(
        "fk_isms_app_config_asset_id_risk_assets",
        "isms_application_configuration_entries",
        "risk_assets",
        ["asset_id"],
        ["id"],
        ondelete="CASCADE",
    )

    if _table_exists(bind, "isms_assets"):
        op.drop_table("isms_assets")


def downgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "isms_assets",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("asset", sa.String(length=256), nullable=False),
        sa.Column("license", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("owner_org_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "register_held_by_org_node_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")
        ),
        sa.ForeignKeyConstraint(
            ["owner_org_node_id"], ["isms_org_nodes.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["register_held_by_org_node_id"], ["isms_org_nodes.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
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

    bind.execute(sa.text("""
            insert into isms_assets (
                id, asset, license, owner_org_node_id,
                register_held_by_org_node_id, description,
                created_by_user_id, created_at, updated_at
            )
            select id, name, license, owner_org_node_id,
                   register_held_by_org_node_id, description,
                   created_by_user_id, created_at, updated_at
            from risk_assets
            where owner_org_node_id is not null
               or register_held_by_org_node_id is not null
               or license <> ''
               or description <> ''
            on conflict (id) do nothing
            """))

    if _constraint_exists(
        bind,
        "isms_application_configuration_entries",
        "fk_isms_app_config_asset_id_risk_assets",
    ):
        op.drop_constraint(
            "fk_isms_app_config_asset_id_risk_assets",
            "isms_application_configuration_entries",
            type_="foreignkey",
        )
    op.create_foreign_key(
        "isms_application_configuration_entries_asset_id_fkey",
        "isms_application_configuration_entries",
        "isms_assets",
        ["asset_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "fk_risk_assets_created_by_user_id_users", "risk_assets", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_risk_assets_register_held_by_org_node_id_isms_org_nodes",
        "risk_assets",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_risk_assets_owner_org_node_id_isms_org_nodes",
        "risk_assets",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_risk_assets_register_held_by_org_node_id", table_name="risk_assets"
    )
    op.drop_index("ix_risk_assets_owner_org_node_id", table_name="risk_assets")
    op.drop_column("risk_assets", "created_by_user_id")
    op.drop_column("risk_assets", "description")
    op.drop_column("risk_assets", "register_held_by_org_node_id")
    op.drop_column("risk_assets", "owner_org_node_id")
    op.drop_column("risk_assets", "license")
