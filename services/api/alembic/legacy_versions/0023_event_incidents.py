"""event incidents

Revision ID: 0023_event_incidents
Revises: 0022_audit_scoped_clauses
Create Date: 2026-05-20

"""

from __future__ import annotations

import uuid
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0023_event_incidents"
down_revision = "0022_audit_scoped_clauses"
branch_labels = None
depends_on = None

INCIDENT_CREATE = "incident.create"


def upgrade():
    op.create_table(
        "event_incidents",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("event_url", sa.String(length=2048), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("webhook_status_code", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index("ix_event_incidents_event_id", "event_incidents", ["event_id"])
    op.create_index(
        "ix_event_incidents_created_by_user_id",
        "event_incidents",
        ["created_by_user_id"],
    )
    op.create_index("ix_event_incidents_created_at", "event_incidents", ["created_at"])

    permissions = sa.table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True)),
        sa.Column("code", sa.String(length=128)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
    )
    bind = op.get_bind()
    exists = bind.execute(
        sa.select(permissions.c.id).where(permissions.c.code == INCIDENT_CREATE)
    ).scalar()
    if not exists:
        op.bulk_insert(
            permissions,
            [
                {
                    "id": uuid.uuid4(),
                    "code": INCIDENT_CREATE,
                    "description": "Can create an external incident from an event when the incident webhook is configured.",
                    "created_at": datetime.utcnow(),
                }
            ],
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

    perm_id = bind.execute(
        sa.select(permissions.c.id).where(permissions.c.code == INCIDENT_CREATE)
    ).scalar()
    if perm_id:
        bind.execute(
            user_permissions.delete().where(user_permissions.c.permission_id == perm_id)
        )
        bind.execute(
            group_permissions.delete().where(
                group_permissions.c.permission_id == perm_id
            )
        )
        bind.execute(permissions.delete().where(permissions.c.id == perm_id))

    op.drop_index("ix_event_incidents_created_at", table_name="event_incidents")
    op.drop_index("ix_event_incidents_created_by_user_id", table_name="event_incidents")
    op.drop_index("ix_event_incidents_event_id", table_name="event_incidents")
    op.drop_table("event_incidents")
