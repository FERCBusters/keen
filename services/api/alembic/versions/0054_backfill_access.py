"""repair ISMS access control matrix role links

Revision ID: 0054_backfill_access
Revises: 0053_isms_access_control_matrix
Create Date: 2026-05-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0054_backfill_access"
down_revision = "0053_isms_access_control"
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


def _index_exists(bind, index_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("select to_regclass(:name)"), {"name": index_name}
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()

    # Some deployments may already have applied an earlier 0053 migration before
    # the Role field was changed from a single org-chart node to a multi-select.
    # Alembic will not re-run an already-applied revision, so create the missing
    # join table here as a separate head migration. Fresh databases get the table
    # from 0053 and this migration becomes a no-op.
    if not _table_exists(bind, "isms_access_control_matrix_roles"):
        op.create_table(
            "isms_access_control_matrix_roles",
            sa.Column(
                "entry_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "isms_access_control_matrix_entries.id", ondelete="CASCADE"
                ),
                primary_key=True,
                nullable=False,
            ),
            sa.Column(
                "org_node_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("isms_org_nodes.id", ondelete="CASCADE"),
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

    if not _index_exists(bind, "ix_isms_access_control_matrix_roles_entry_id"):
        op.create_index(
            "ix_isms_access_control_matrix_roles_entry_id",
            "isms_access_control_matrix_roles",
            ["entry_id"],
        )
    if not _index_exists(bind, "ix_isms_access_control_matrix_roles_org_node_id"):
        op.create_index(
            "ix_isms_access_control_matrix_roles_org_node_id",
            "isms_access_control_matrix_roles",
            ["org_node_id"],
        )

    # Preserve roles saved by the first single-select implementation, if that
    # column exists in a database that already ran the older migration.
    if _column_exists(bind, "isms_access_control_matrix_entries", "role_org_node_id"):
        bind.execute(sa.text("""
                insert into isms_access_control_matrix_roles
                    (entry_id, org_node_id, created_at)
                select id, role_org_node_id, coalesce(created_at, now())
                from isms_access_control_matrix_entries
                where role_org_node_id is not null
                on conflict (entry_id, org_node_id) do nothing
                """))

    # Be tolerant of any local/pre-release schema variant that used role_id as
    # the original single-select column name.
    if _column_exists(bind, "isms_access_control_matrix_entries", "role_id"):
        bind.execute(sa.text("""
                insert into isms_access_control_matrix_roles
                    (entry_id, org_node_id, created_at)
                select id, role_id, coalesce(created_at, now())
                from isms_access_control_matrix_entries
                where role_id is not null
                on conflict (entry_id, org_node_id) do nothing
                """))


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "isms_access_control_matrix_roles"):
        if _index_exists(bind, "ix_isms_access_control_matrix_roles_org_node_id"):
            op.drop_index(
                "ix_isms_access_control_matrix_roles_org_node_id",
                table_name="isms_access_control_matrix_roles",
            )
        if _index_exists(bind, "ix_isms_access_control_matrix_roles_entry_id"):
            op.drop_index(
                "ix_isms_access_control_matrix_roles_entry_id",
                table_name="isms_access_control_matrix_roles",
            )
        op.drop_table("isms_access_control_matrix_roles")
