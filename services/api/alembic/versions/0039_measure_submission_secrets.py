"""Submission secrets for effectiveness measures.

Each measure that receives results from a reporting job holds its own secret,
encrypted with KEEN_INTEGRATION_SECRET_KEY, which signs that job's submissions.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0039_measure_submission_secrets"
down_revision = "0038_builtin_host_rules"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "isms_effectiveness_measure_secrets",
        sa.Column(
            "measure_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("isms_effectiveness_measures.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("isms_effectiveness_measure_secrets")
