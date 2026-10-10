"""Carry the auditor's Risk-sheet Vanta control IDs into KEEN-AF templates.

Revision ID: 0078_library_control_links
Revises: 0077_unified_risks_assets
"""

import json
import re
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0078_library_control_links"
down_revision = "0077_unified_risks_assets"
branch_labels = None
depends_on = None

FRAMEWORK = "KEEN-AF:1.0"
DATA = Path(__file__).resolve().parents[1] / "seed_assurance_risks.json"


def upgrade():
    bind = op.get_bind()
    for risk in json.loads(DATA.read_text(encoding="utf-8")):
        refs = list(
            dict.fromkeys(
                re.findall(
                    r"\b[A-Z]{2,5}-\d+\b",
                    risk["suggested_assessment"].get("auditor_control_refs", ""),
                )
            )
        )
        if not refs:
            continue
        # Add mappings only to the original seeded entries. Preserve any
        # administrator changes to the template's reference data.
        bind.execute(
            sa.text("""
            UPDATE risk_library_entries
            SET suggested_assessment = jsonb_set(suggested_assessment, '{keen_af_control_refs}', CAST(:refs AS JSONB))
            WHERE name = :name
              AND suggested_assessment->>'auditor_control_refs' = :original
              AND NOT suggested_assessment ? 'keen_af_control_refs'
        """),
            {
                "name": risk["name"],
                "original": risk["suggested_assessment"]["auditor_control_refs"],
                "refs": json.dumps(refs),
            },
        )


def downgrade():
    # Retain administrator-reviewed template associations.
    pass
