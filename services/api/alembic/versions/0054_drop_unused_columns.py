"""Drop columns nothing reads.

A risk kept a three-factor score (threat, vulnerability and impact, with
residual vulnerability and impact) beside the four register ratings its scores
are worked out from. Keen set the threat score to 1 and copied the ratings into
the rest, so the five columns go, and their copies leave each risk's change
history. An artifact kept PII flags and a retention class that were never set
or read.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0054_drop_unused_columns"
down_revision = "0053_risk_and_factor_links"
branch_labels = None
depends_on = None

# Each legacy score with the register rating it mirrored (none for the threat).
LEGACY_SCORES = {
    "threat_score": None,
    "vulnerability_score": "register_likelihood",
    "impact_score": "register_impact",
    "residual_vulnerability_score": "register_residual_likelihood",
    "residual_impact_score": "register_residual_impact",
}


def upgrade():
    # Dropping a column drops the check constraint on it.
    for column in LEGACY_SCORES:
        op.drop_column("risks", column)
    keys = list(LEGACY_SCORES)
    # A state that was never recorded is stored as a JSON null, which has no
    # keys to remove.
    op.execute(
        sa.text(
            "UPDATE entity_changelogs SET"
            " before_state = CASE WHEN jsonb_typeof(before_state) = 'object'"
            " THEN before_state - CAST(:keys AS text[]) ELSE before_state END,"
            " after_state = CASE WHEN jsonb_typeof(after_state) = 'object'"
            " THEN after_state - CAST(:keys AS text[]) ELSE after_state END,"
            " changes = COALESCE((SELECT jsonb_agg(change) FROM"
            " jsonb_array_elements(changes) AS change"
            " WHERE NOT (change->>'field' = ANY(CAST(:keys AS text[])))), '[]'::jsonb)"
            " WHERE entity_type = 'risk' AND jsonb_typeof(changes) = 'array'"
        ).bindparams(keys=keys)
    )
    op.drop_column("artifacts", "pii_flags")
    op.drop_column("artifacts", "retention_class")


def downgrade():
    """Restore the columns; the scores are copied from the register ratings again."""
    for column, rating in LEGACY_SCORES.items():
        op.add_column(
            "risks",
            sa.Column(column, sa.Integer(), server_default="1", nullable=False),
        )
        if rating:
            op.execute(f"UPDATE risks SET {column} = COALESCE({rating}, 1)")
        op.create_check_constraint(
            f"ck_risks_{column}", "risks", f"{column} between 0 and 5"
        )
    op.add_column(
        "artifacts",
        sa.Column(
            "pii_flags",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "artifacts",
        sa.Column(
            "retention_class",
            sa.String(32),
            server_default="default",
            nullable=False,
        ),
    )
