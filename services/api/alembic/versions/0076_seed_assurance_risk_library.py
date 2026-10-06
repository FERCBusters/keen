"""Seed auditor donated risk scenarios as reusable templates, preserving existing assessments.

Revision ID: 0076_assurance_risks
Revises: 0075_people_attendance
"""
import json
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0076_assurance_risks"
down_revision = "0075_people_attendance"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("risk_library_entries", sa.Column("suggested_assessment", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    data = json.loads((Path(__file__).resolve().parents[1] / "seed_assurance_risks.json").read_text(encoding="utf-8"))
    statement = sa.text("""
        INSERT INTO risk_library_entries
            (id, name, threat_summary, risk_types, treatment_guidance, suggested_assessment, created_at)
        VALUES (CAST(:id AS UUID), :name, :threat, CAST(:types AS JSONB), '', CAST(:suggestion AS JSONB), NOW())
        ON CONFLICT (name) DO NOTHING
    """)
    bind = op.get_bind()
    for risk in data:
        bind.execute(statement, dict(id=str(uuid.uuid4()), name=risk["name"], threat=risk["threat_summary"],
                                     types=json.dumps(risk["risk_types"]), suggestion=json.dumps(risk["suggested_assessment"])))


def downgrade():
    op.drop_column("risk_library_entries", "suggested_assessment")
