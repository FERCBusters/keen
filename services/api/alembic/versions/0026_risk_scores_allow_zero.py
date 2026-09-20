"""allow zero risk scores

Revision ID: 0026_risk_scores_allow_zero
Revises: 0025_risk_assets
Create Date: 2026-05-21

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0026_risk_scores_allow_zero"
down_revision = "0025_risk_assets"
branch_labels = None
depends_on = None

_SCORE_CONSTRAINTS = (
    ("ck_risks_threat_score", "threat_score"),
    ("ck_risks_vulnerability_score", "vulnerability_score"),
    ("ck_risks_impact_score", "impact_score"),
    ("ck_risks_residual_vulnerability_score", "residual_vulnerability_score"),
    ("ck_risks_residual_impact_score", "residual_impact_score"),
)


def _replace_constraints(*, minimum: int) -> None:
    for name, column in _SCORE_CONSTRAINTS:
        op.drop_constraint(name, "risks", type_="check")
        op.create_check_constraint(
            name,
            "risks",
            sa.text(f"{column} between {minimum} and 5"),
        )


def upgrade() -> None:
    _replace_constraints(minimum=0)


def downgrade() -> None:
    # Existing zero scores would violate the old constraints, so reset only those
    # zero component scores back to the previous minimum before re-adding checks.
    for _, column in _SCORE_CONSTRAINTS:
        op.execute(sa.text(f"UPDATE risks SET {column} = 1 WHERE {column} < 1"))
    op.execute(sa.text("""
            UPDATE risks
            SET risk_score = threat_score * vulnerability_score * impact_score,
                residual_risk_score = residual_vulnerability_score * residual_impact_score
            """))
    _replace_constraints(minimum=1)
