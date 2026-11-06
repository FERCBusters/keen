"""Record when a risk closes and when a posture result began needing attention.

A failing posture result whose risk closed after the failure began reads as
Risk treated. A risk's closing time and a workstation stream's attention start
are columns; a cloud check keeps its attention record beside its result.
"""

import sqlalchemy as sa
from alembic import op

revision = "0044_posture_risk_treatment"
down_revision = "0043_access_matrix_fields"
branch_labels = None
depends_on = None


def upgrade():
    # Risks closed and results failing before this revision have no recorded
    # start, so they read as needing attention until their risk closes again.
    op.add_column("risks", sa.Column("closed_at", sa.DateTime(), nullable=True))
    op.add_column(
        "agent_posture_observations",
        sa.Column("attention_since", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("agent_posture_observations", "attention_since")
    op.drop_column("risks", "closed_at")
