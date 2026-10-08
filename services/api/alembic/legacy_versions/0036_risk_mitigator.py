"""risk mitigator support

Revision ID: 0036_risk_mitigator
Revises: 0035_scheduled_audit_days
Create Date: 2026-05-26

"""

from __future__ import annotations

import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0036_risk_mitigator"
down_revision = "0035_scheduled_audit_days"
branch_labels = None
depends_on = None


_DEFAULT_CATEGORIES = [
    ("Data", "General"),
    ("I.T", "General"),
    ("Cost / financial", "General"),
    ("Brand / reputation", "General"),
    ("Legal / regulatory", "General"),
    ("Supplier / third party", "General"),
    ("People / process", "General"),
    ("Facilities", "General"),
    ("Other", "General"),
]


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
    op.add_column(
        "risks",
        sa.Column(
            "mitigator_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    bind = op.get_bind()
    for category_name, subcategory_name in _DEFAULT_CATEGORIES:
        cid = _get_or_create_category(bind, category_name)
        _get_or_create_subcategory(bind, cid, subcategory_name)

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with op.get_context().autocommit_block():
        # Compact control catalog search document used by Keen Mitigator.  This mirrors
        # the event/evidence FTS approach, but keeps the indexed text limited to the
        # control catalog fields instead of relationship-heavy derived data.
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_control_items_mitigator_fts "
            "ON control_items USING gin ("
            "to_tsvector('simple', "
            "coalesce(ref, '') || ' ' || "
            "coalesce(title, '') || ' ' || "
            "coalesce(type, '') || ' ' || "
            "coalesce(tags::text, '') || ' ' || "
            "coalesce(\"metadata\"::text, '')"
            ")"
            ")"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_control_items_title_trgm "
            "ON control_items USING gin (title gin_trgm_ops)"
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_control_items_title_trgm")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_control_items_mitigator_fts")
    op.drop_column("risks", "mitigator_context")
