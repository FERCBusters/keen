"""saved searches

Revision ID: 0005_saved_searches
Revises: 0004_users
Create Date: 2026-01-13

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_saved_searches"
down_revision = "0004_users"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "saved_searches",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="uq_saved_search_user_name"),
    )

    op.create_index("ix_saved_searches_user_id", "saved_searches", ["user_id"])


def downgrade():
    op.drop_index("ix_saved_searches_user_id", table_name="saved_searches")
    op.drop_table("saved_searches")
