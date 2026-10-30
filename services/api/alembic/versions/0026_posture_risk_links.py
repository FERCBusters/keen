"""Risks recorded against workstation and cloud posture results."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026_posture_risk_links"
down_revision = "0025_ueu_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "posture_risk_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("origin_key", sa.String(96), nullable=False),
        sa.Column("scope_key", sa.String(128), nullable=False),
        sa.Column("check_key", sa.String(64), nullable=False),
        sa.Column("subject_key", sa.String(256), nullable=False),
        sa.Column(
            "risk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("risks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "origin IN ('workstation','cloud')", name="ck_posture_risk_origin"
        ),
        sa.UniqueConstraint(
            "origin",
            "origin_key",
            "scope_key",
            "check_key",
            "subject_key",
            "risk_id",
            name="uq_posture_risk_link",
        ),
    )
    op.create_index("ix_posture_risk_links_risk_id", "posture_risk_links", ["risk_id"])
    op.create_index(
        "ix_posture_risk_links_origin", "posture_risk_links", ["origin", "origin_key"]
    )


def downgrade():
    op.drop_table("posture_risk_links")
