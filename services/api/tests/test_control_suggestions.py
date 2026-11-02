"""Suggestions are reviewable, framework-scoped and grounded in live catalogue text."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.api.routes import managed_configurations as routes
from app.core.config import settings
from app.db.models import ControlItem, Framework, ManagedConfiguration
from app.services.control_suggestions import activity_words, rank_controls
from app.services.errors import DomainError
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tests.db_helpers import create_sqlite_schema

SEEDS = json.loads(
    (Path(__file__).resolve().parents[1] / "alembic/seed_frameworks.json").read_text()
)


def seed_controls(slug="ISO27001:2022"):
    return [
        SimpleNamespace(
            id=c["ref"],
            framework_slug=slug,
            title=c["title"],
            ref=c["ref"],
            type=c["type"],
            tags=c.get("tags", {}),
            meta=c.get("metadata", {}),
        )
        for c in SEEDS[slug]["controls"]
    ]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Firewall activity", {"A.8.20", "A.8.21"}),
        ("NFTABLES packet filtering", {"A.8.20", "A.8.21"}),
        ("Package upgrades", {"A.8.8"}),
        ("SSHD logins", {"A.5.17", "A.8.5"}),
        ("Sudo commands", {"A.8.2", "A.8.18"}),
        ("Wazuh alerts", {"A.8.16"}),
        ("Backup restored", {"A.8.13"}),
        ("Supplier review", {"A.5.19", "A.5.20", "A.5.22"}),
        ("VLAN segmentation", {"A.8.22"}),
        ("TLS certificate renewal", {"A.8.24"}),
        ("NTP clock drift", {"A.8.17"}),
        ("Cron jobs", {"A.5.37", "A.8.16"}),
        ("Data redaction", {"A.8.11"}),
    ],
)
def test_common_operational_terms(name, expected):
    results = rank_controls(seed_controls(), description=name)
    assert expected <= {r["ref"] for r in results[:5]}
    assert all(
        r["reason"] and (r["matched_terms"] or r["matched_concepts"]) for r in results
    )


@pytest.mark.parametrize(
    "name",
    [
        "",
        "activity events evidence",
        "information security management",
        "quuxxy frobnicator",
        "rootstock apartment aptitude",
        "aptitude",
    ],
)
def test_generic_or_substring_words_do_not_invent_matches(name):
    assert rank_controls(seed_controls(), description=name) == []


@pytest.mark.parametrize(
    "name,expected",
    [
        ("PullRequestEvent", {"A.8.32"}),
        ("PushEvent", {"A.8.4", "A.8.25"}),
        ("CheckRunEvent", {"A.8.29"}),
        ("deploy", {"A.8.32"}),
        ("SUSPEND_USER", {"A.5.18"}),
        ("ASSIGN_ROLE super admin", {"A.8.2"}),
        ("ENFORCE_STRONG_AUTHENTICATION", {"A.5.17", "A.8.5"}),
        ("rootkit_sec_event", {"A.8.7"}),
        ("RevokeSecurityGroupIngress", {"A.8.20"}),
        ("guardduty_finding", {"A.8.16"}),
    ],
)
def test_collector_activity_names_read_as_words(name, expected):
    results = rank_controls(seed_controls(), description=activity_words(name))
    assert expected <= {r["ref"] for r in results[:4]}


def test_activity_words_split_code_style_names():
    assert activity_words("PullRequestReviewCommentEvent") == (
        "Pull Request Review Comment Event"
    )
    assert activity_words("TLSHandshakeRefused") == "TLS Handshake Refused"
    assert activity_words("CHANGE_USER_PASSWORD") == "CHANGE_USER_PASSWORD"
    assert activity_words(None) == ""


def test_monitoring_tools_do_not_match_physical_monitoring():
    refs = {r["ref"] for r in rank_controls(seed_controls(), description="OSSEC")}
    assert "A.8.16" in refs and "A.7.4" not in refs


def test_training_data_is_not_awareness_training():
    control = SimpleNamespace(
        id="ai",
        framework_slug="CUSTOM",
        title="Training data quality",
        ref="AI-1",
        type="custom",
        tags={},
        meta={},
    )
    (result,) = rank_controls([control], description="security awareness training")
    # The shared word still counts; the awareness concept does not.
    assert result["matched_terms"] == ["training"]
    assert result["matched_concepts"] == []


def test_a_source_address_is_not_source_code():
    refs = {r["ref"] for r in rank_controls(seed_controls(), description="source ip")}
    assert "A.8.4" not in refs


def test_context_counts_half_as_much_as_the_description():
    alone = {
        r["ref"]: r["score"]
        for r in rank_controls(seed_controls(), description="OSSEC")
    }
    results = rank_controls(seed_controls(), description="patching", context="OSSEC")
    # The description decides the order; the context adds half its weight.
    assert results[0]["ref"] == "A.8.8"
    assert {r["ref"]: r["score"] for r in results}["A.8.16"] == alone["A.8.16"] / 2


def test_firewall_reason_and_no_generic_security_matches():
    results = rank_controls(seed_controls(), description="Firewall activity")
    assert {r["ref"] for r in results} == {"A.8.20", "A.8.21"}
    assert all(
        any(c["query_term"] == "firewall" for c in r["matched_concepts"])
        for r in results
    )
    assert all("→" in r["reason"] for r in results)


def test_mapping_name_has_priority_over_noisy_sample():
    results = rank_controls(
        seed_controls(),
        description="Firewall activity",
        sample_summary="backup restore authentication password " * 40,
    )
    assert {r["ref"] for r in results[:2]} == {"A.8.20", "A.8.21"}


def test_repeat_words_do_not_boost_scores():
    assert rank_controls(seed_controls(), description="firewall") == rank_controls(
        seed_controls(), description="firewall " * 30
    )


def test_custom_framework_uses_live_descriptions_and_no_urls():
    control = SimpleNamespace(
        id="one",
        framework_slug="CUSTOM",
        title="Protect the perimeter",
        ref="NET",
        type="custom",
        tags={},
        meta={"description": "Network security including packet filtering"},
    )
    assert rank_controls([control], description="firewall")[0]["ref"] == "NET"
    control.meta = {
        "description": "Staff training",
        "upstream_url": "https://firewall.example",
    }
    assert rank_controls([control], description="firewall") == []
    control.meta = {}
    control.tags = {"keywords": ["network security"]}
    assert rank_controls([control], description="firewall")


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "enabled_frameworks", "")
    engine = create_engine("sqlite://")
    create_sqlite_schema(engine)
    with Session(engine) as db:
        db.add_all(
            [
                Framework(slug="CUSTOM"),
                Framework(slug="OTHER"),
                ControlItem(
                    framework_slug="CUSTOM",
                    type="custom",
                    ref="NET",
                    title="Network security",
                ),
                ControlItem(
                    framework_slug="CUSTOM",
                    type="custom",
                    ref="OUT",
                    title="Network security",
                    in_scope=False,
                ),
                ControlItem(
                    framework_slug="OTHER",
                    type="custom",
                    ref="OTHER",
                    title="Network security",
                ),
            ]
        )
        db.commit()
        yield db
    engine.dispose()


def test_endpoint_scope_live_edits_and_disabled_framework(db):
    payload = routes.ControlSuggestionInput(
        framework="CUSTOM", description="Firewall activity"
    )
    assert [r["ref"] for r in routes.suggest_controls(payload, db)["items"]] == ["NET"]
    control = db.query(ControlItem).filter_by(ref="NET").one()
    control.title = "Something else"
    control.meta = {"description": "Network services security"}
    db.commit()
    assert routes.suggest_controls(payload, db)["items"]
    control.meta = {}
    db.commit()
    assert routes.suggest_controls(payload, db)["items"] == []
    db.add(
        ManagedConfiguration(
            name="organisation-frameworks",
            document={"enabled": ["OTHER"], "default": "OTHER"},
            version=1,
        )
    )
    db.commit()
    with pytest.raises((HTTPException, DomainError)) as error:
        routes.suggest_controls(payload, db)
    assert error.value.status_code == 400


def test_bounded_input():
    with pytest.raises(ValidationError):
        routes.ControlSuggestionInput(framework="CUSTOM", description="x" * 4001)
