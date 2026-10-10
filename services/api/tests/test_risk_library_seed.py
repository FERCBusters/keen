"""Validate the auditor's donated risk-to-KEEN-AF control references."""

import json
import re
import unittest
from pathlib import Path

SEED = Path(__file__).resolve().parents[1] / "alembic"


class RiskLibrarySeedTest(unittest.TestCase):
    def test_risk_control_references_exist_in_assurance_framework(self):
        risks = json.loads(
            (SEED / "seed_assurance_risks.json").read_text(encoding="utf-8")
        )
        frameworks = json.loads(
            (SEED / "seed_frameworks.json").read_text(encoding="utf-8")
        )
        controls = {control["ref"] for control in frameworks["KEEN-AF:1.0"]["controls"]}
        mappings = [
            set(
                re.findall(
                    r"\b[A-Z]{2,5}-\d+\b",
                    risk["suggested_assessment"].get("auditor_control_refs", ""),
                )
            )
            for risk in risks
        ]
        self.assertGreater(sum(bool(refs) for refs in mappings), 100)
        self.assertEqual(set().union(*mappings) - controls, set())


if __name__ == "__main__":
    unittest.main()
