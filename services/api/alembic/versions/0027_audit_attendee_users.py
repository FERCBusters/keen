"""link audit attendees to Keen users

Revision ID: 0027_audit_attendee_users
Revises: 0026_risk_scores_allow_zero
Create Date: 2026-05-21

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0027_audit_attendee_users"
down_revision = "0026_risk_scores_allow_zero"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "audit_attendees",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_audit_attendees_user_id_users",
        "audit_attendees",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_audit_attendees_user_id", "audit_attendees", ["user_id"])


def downgrade():
    op.drop_index("ix_audit_attendees_user_id", table_name="audit_attendees")
    op.drop_constraint(
        "fk_audit_attendees_user_id_users",
        "audit_attendees",
        type_="foreignkey",
    )
    op.drop_column("audit_attendees", "user_id")
