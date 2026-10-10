"""Exercise recommendations against the shipped catalogue and live ORM changes."""

import json
import os
import sys
from pathlib import Path

from app.core.datetime_utils import utc_now_naive

from tests.db_helpers import create_sqlite_schema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KEEN_DATABASE_URL", "postgresql://test:test@localhost/unused")
os.environ.setdefault("KEEN_BOOTSTRAP_ADMIN_USERNAME", "test")
os.environ.setdefault("KEEN_BOOTSTRAP_ADMIN_PASSWORD", "test-password")
import pytest
import yaml
from app.db.models import ControlItem, RiskLibraryEntry
from app.services.mitigator_catalogue import rank_library
from app.services.risk_mitigator import analyse_risk_mitigation
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

RISKS = json.loads((ROOT / "alembic/seed_assurance_risks.json").read_text())
FRAMEWORKS = json.loads((ROOT / "alembic/seed_frameworks.json").read_text())
RULES = yaml.safe_load((ROOT.parents[1] / "config/risk_mitigator.yml").read_text())


@pytest.mark.parametrize(
    "issue,reference",
    [
        ("Staff receive no awareness training and fall for phishing", "088"),
        ("We have no backup and cannot restore after ransomware", "038"),
        ("Our suppliers have inadequate contracts and service agreements", "017"),
        ("Company staff use unapproved AI tools", "137"),
        ("Prompt injection can manipulate our LLM behaviour", "136"),
        ("Data poisoning corrupts our AI training data", "134"),
        ("Overprivileged AI agents have excessive access", "141"),
        ("Visitors enter our premises without being checked", "091"),
        ("Former employees retain access after leaving", "046"),
    ],
)
def test_real_library_vocabulary(issue, reference):
    hits = rank_library(RISKS, issue=issue, rules=RULES)
    assert any(hit["id"] == "KEEN-AF-RISK-" + reference for hit in hits), [
        h["name"] for h in hits
    ]
    assert all(hit["matched_terms"] for hit in hits)


@pytest.mark.parametrize(
    "issue",
    [
        "",
        "the and our",
        "We need to improve our information security management",
        "quuxxyz frobnicator",
    ],
)
def test_generic_and_unknown_words_do_not_invent_library_matches(issue):
    assert rank_library(RISKS, issue=issue, asset_name="Staff", rules=RULES) == []


def test_current_mapping_overrides_legacy_seed_mapping():
    entry = dict(
        RISKS[37],
        suggested_assessment={
            "keen_af_control_refs": [],
            "auditor_control_refs": "BCP-1",
        },
    )
    assert rank_library([entry], issue="backup")[0]["control_refs"] == []


@compiles(JSONB, "sqlite")
def sqlite_json(*args, **kwargs):
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def functions(conn, record):
        conn.create_function("NOW", 0, lambda: utc_now_naive().isoformat(" "))

    create_sqlite_schema(engine)
    with Session(engine) as session:
        for slug, framework in FRAMEWORKS.items():
            for control in framework["controls"]:
                session.add(
                    ControlItem(
                        framework_slug=slug,
                        type=control["type"],
                        ref=control["ref"],
                        title=control["title"],
                        meta=control.get("metadata", {}),
                        in_scope=True,
                    )
                )
        for risk in RISKS:
            session.add(
                RiskLibraryEntry(
                    name=risk["name"],
                    threat_summary=risk["threat_summary"],
                    risk_types=risk["risk_types"],
                    suggested_assessment=risk["suggested_assessment"],
                )
            )
        session.commit()
        yield session
    engine.dispose()


def test_analysis_uses_library_links_and_reads_live_edits(db):
    result = analyse_risk_mitigation(
        db,
        framework="KEEN-AF:1.0",
        issue="Prompt injection can manipulate our LLM behaviour",
        limit=25,
    )
    assert result["library_suggestions"]
    refs = {ref for h in result["library_suggestions"] for ref in h["control_refs"]}
    suggestions = result["control_suggestions"]
    assert refs & {s["control"]["ref"] for s in suggestions[:10]}
    assert any("reusable risk scenario" in " ".join(s["reasons"]) for s in suggestions)
    assert all(s["control"]["framework"] == "KEEN-AF:1.0" for s in suggestions)
    entry = RiskLibraryEntry(
        name="Unique frobnicator failure",
        threat_summary="Frobnicator theft",
        suggested_assessment={"keen_af_control_refs": ["AST-1", "INVALID-999"]},
    )
    db.add(entry)
    db.commit()
    result = analyse_risk_mitigation(
        db, framework="KEEN-AF:1.0", issue="Frobnicator theft"
    )
    hit = next(h for h in result["library_suggestions"] if h["id"] == str(entry.id))
    assert hit["control_refs"] == ["AST-1"]
    assert result["draft_risk"]["threat_summary"] == "Frobnicator theft"
    assert "library_template_id" not in result["draft_risk"]


def test_other_framework_does_not_receive_keen_af_library_links(db):
    slug = next(slug for slug in FRAMEWORKS if slug != "KEEN-AF:1.0")
    result = analyse_risk_mitigation(
        db, framework=slug, issue="Staff use unapproved AI tools"
    )
    assert result["library_suggestions"] == []
    assert all(s["control"]["framework"] == slug for s in result["control_suggestions"])
