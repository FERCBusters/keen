"""Risks: a title, the parties a risk affects, and library templates for any method.

A risk gains a short title for the register. A new table records who a risk
affects: an interested party, a person from the ISMS directory or a vendor.
Library templates record the method they seed (a register risk, with or
without CIA associations, or a PESTLE(E) item) so one library serves every
kind of risk.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_risk_wizard"
down_revision = "0014_measure_source_type"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "risks",
        sa.Column("title", sa.String(length=256), nullable=False, server_default=""),
    )
    op.execute(
        """CREATE TABLE risk_interested_parties (
        id uuid PRIMARY KEY,
        risk_id uuid NOT NULL REFERENCES risks(id) ON DELETE CASCADE,
        interested_party_id uuid REFERENCES interested_parties(id) ON DELETE CASCADE,
        person_id uuid REFERENCES isms_people(id) ON DELETE CASCADE,
        vendor_id uuid REFERENCES isms_vendors(id) ON DELETE CASCADE,
        created_at timestamp NOT NULL,
        CONSTRAINT ck_risk_interested_parties_one_party CHECK (
            (CASE WHEN interested_party_id IS NULL THEN 0 ELSE 1 END)
            + (CASE WHEN person_id IS NULL THEN 0 ELSE 1 END)
            + (CASE WHEN vendor_id IS NULL THEN 0 ELSE 1 END) = 1
        )
    )"""
    )
    for column in ("risk_id", "interested_party_id", "person_id", "vendor_id"):
        op.create_index(
            f"ix_risk_interested_parties_{column}",
            "risk_interested_parties",
            [column],
        )
    op.add_column(
        "risk_library_entries",
        sa.Column(
            "method", sa.String(length=16), nullable=False, server_default="risk"
        ),
    )
    op.add_column(
        "risk_library_entries",
        sa.Column(
            "pestle_type", sa.String(length=32), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "risk_library_entries",
        sa.Column("lens", sa.String(length=16), nullable=False, server_default=""),
    )


def downgrade():
    op.drop_column("risk_library_entries", "lens")
    op.drop_column("risk_library_entries", "pestle_type")
    op.drop_column("risk_library_entries", "method")
    op.drop_table("risk_interested_parties")
    op.drop_column("risks", "title")
