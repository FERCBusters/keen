"""Show MIDENA diagrams with ISMS policy and process documents.

Each row is the drawing of the MIDENA diagram version captured for one
document, as Keen sanitised and stored it, with its checksum, so the document
shows that version until someone refreshes it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0058_isms_document_diagrams"
down_revision = "0057_user_login_keys"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "isms_document_diagrams",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("diagram_uuid", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("diagram_type", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=True),
        sa.Column("source_updated_at", sa.String(length=64), nullable=False),
        sa.Column("svg", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("captured_by_user_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"], ["isms_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["captured_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "diagram_uuid"),
    )
    op.create_index(
        op.f("ix_isms_document_diagrams_document_id"),
        "isms_document_diagrams",
        ["document_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_isms_document_diagrams_document_id"),
        table_name="isms_document_diagrams",
    )
    op.drop_table("isms_document_diagrams")
