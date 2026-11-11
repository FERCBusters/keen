from unittest.mock import Mock

import pytest
from app.ingest import rss
from app.ingest.demo_rss import FEED, RULE
from app.mapping.rules import evaluate_by_framework, parse_rules

pytestmark = pytest.mark.usefixtures("no_evidence_purge", "no_competing_run")


def test_demo_rejects_other_feeds(monkeypatch):
    monkeypatch.setattr(rss.settings, "demo_mode", True)
    assert (
        rss.ingest_rss_feed(Mock(), {"url": "https://other.example/feed"})["ok"]
        is False
    )


def test_demo_uses_only_preset(monkeypatch):
    monkeypatch.setattr(rss.settings, "demo_mode", True)
    monkeypatch.setattr(rss.settings, "rss_enabled", True)
    fn = Mock(return_value={"ok": True})
    monkeypatch.setattr(rss, "ingest_rss_feed", fn)
    monkeypatch.setattr(
        rss,
        "load_rss_config",
        lambda *a: (_ for _ in ()).throw(
            AssertionError("Demo must ignore arbitrary config")
        ),
    )
    db = Mock()
    db.scalars.return_value.all.return_value = []
    rss.ingest_rss_all(db)
    fn.assert_called_once_with(db, FEED)


def test_supplier_rule_matches_only_preset_source():
    rules = parse_rules({"rules": [RULE]})
    hits = evaluate_by_framework({"source": "rss", "system": FEED["system"]}, rules)
    assert set(hits["iso_27001_2022"]) == {"A.5.19", "A.5.22"}
    assert hits["KEEN-AF:1.0"] == ["TPM-2"]
    assert (
        evaluate_by_framework({"source": "rss", "system": "Another feed"}, rules) == {}
    )


def test_seeding_the_preset_keeps_the_framework_rules_inherited(
    contract_db, monkeypatch
):
    from app.core.config import settings
    from app.db.models import ManagedConfiguration
    from app.ingest.demo_rss import seed_preset

    monkeypatch.setattr(settings, "demo_mode", True)
    when = {"source": "test"}
    contract_db.add(
        ManagedConfiguration(
            name="rules",
            version=1,
            document={
                "framework": "iso_27001_2022",
                "rules": [
                    {"id": "inherits", "when": when, "map_to": ["A.5.1"]},
                    {
                        "id": "own",
                        "framework": "soc2_tsc",
                        "when": when,
                        "map_to": ["CC6.1"],
                    },
                    {
                        "id": "several",
                        "frameworks": ["pci_dss_v4"],
                        "when": when,
                        "map_to": ["8.3.1"],
                    },
                ],
            },
        )
    )
    contract_db.flush()

    seed_preset(contract_db)

    document = contract_db.get(ManagedConfiguration, "rules").document
    # The rewritten document lists its rules without the document-level
    # framework, so each rule that inherited it now names it itself.
    assert "framework" not in document
    assert [
        (r["id"], r.get("framework"), r.get("frameworks")) for r in document["rules"]
    ] == [
        ("inherits", "iso_27001_2022", None),
        ("own", "soc2_tsc", None),
        ("several", None, ["pci_dss_v4"]),
        (RULE["id"], None, None),
    ]
    hits = evaluate_by_framework({"source": "test"}, parse_rules(document))
    assert hits == {
        "iso_27001_2022": ["A.5.1"],
        "soc2_tsc": ["CC6.1"],
        "pci_dss_v4": ["8.3.1"],
    }
