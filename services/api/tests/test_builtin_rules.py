"""Built-in mapping rules: added once beside an installation's own rules,
validated like any administrator's rule, and matching Keen's own evidence."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
from app.core.builtin_rules import FRAMEWORK, add_rules, remove_rules
from app.db.models import (
    ControlItem,
    Framework,
    ManagedConfiguration,
    ManagedConfigurationRevision,
)
from app.mapping.rules import evaluate_by_framework, parse_rules
from app.services.managed_configurations import _validate_rule

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic/versions/0037_builtin_rules.py"
)
OWN = {"id": "keen-cloud-mfa", "when": {"source": "aws"}, "map_to": "IAC-203"}
OTHER = {"id": "admins-own", "when": {"source": "github"}, "map_to": "CHG-1"}
# The installation no longer has these controls.
MISSING = {"GOV-5", "PES-78"}


def _rules():
    spec = importlib.util.spec_from_file_location("builtin_rules_0037", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RULES


RULES = _rules()


@pytest.fixture
def installation(contract_db):
    db = contract_db
    refs = {target["ref"] for rule in RULES for target in rule["map_to"]}
    db.add(Framework(slug=FRAMEWORK, name="Keen Assurance Framework"))
    db.add_all(
        ControlItem(framework_slug=FRAMEWORK, ref=ref, type="custom", title=ref)
        for ref in sorted(refs - MISSING)
    )
    db.add(
        ManagedConfiguration(name="rules", document={"rules": [OWN, OTHER]}, version=3)
    )
    db.commit()
    return db


def _stored(db):
    db.expire_all()
    return db.get(ManagedConfiguration, "rules")


def test_rules_are_added_once_beside_the_installations_own(installation):
    db = installation
    assert add_rules(db.connection(), RULES) == len(RULES) - 2
    row = _stored(db)
    assert row.version == 4 and row.updated_by is None
    rules = row.document["rules"]
    # The administrator's rules come first and stay as they were, including the
    # one that already uses a built-in rule's ID.
    assert rules[:2] == [OWN, OTHER]
    added = {rule["id"]: rule for rule in rules[2:]}
    assert set(added) == {rule["id"] for rule in RULES} - {
        "keen-cloud-mfa",
        "keen-workstation-screen-lock",
    }
    assert [t["ref"] for t in added["keen-cloud-backups"]["map_to"]] == ["BCD-69"]
    revision = (
        db.query(ManagedConfigurationRevision).filter_by(name="rules", version=4).one()
    )
    assert revision.document == row.document
    # A second run adds nothing.
    assert add_rules(db.connection(), RULES) == 0
    assert _stored(db).version == 4


def test_administrators_save_built_in_rules_unchanged(installation):
    for rule in RULES:
        rule = deepcopy(rule)
        rule["map_to"] = [t for t in rule["map_to"] if t["ref"] not in MISSING]
        if rule["map_to"]:
            _validate_rule(rule, installation)


def test_built_in_rules_match_keens_own_evidence():
    rules = parse_rules({"rules": RULES})
    assert len(rules) == len(RULES)

    def refs(source, action, outcome="info"):
        hits = evaluate_by_framework(
            {"source": source, "action": action, "outcome": outcome}, rules
        )
        return sorted(hits.get(FRAMEWORK, []))

    assert refs("aws", "posture.identity.mfa", "attention") == [
        "CLD-7",
        "IAC-12",
        "IAC-203",
    ]
    assert refs("digitalocean", "posture.resilience.backups", "met") == [
        "BCD-69",
        "CLD-7",
        "GOV-5",
    ]
    assert refs("google_workspace", "posture.identity.admins", "met") == [
        "CLD-7",
        "IAC-201",
    ]
    # Other sources and cloud activity map nothing.
    assert refs("github", "posture.identity.mfa", "met") == []
    assert refs("aws", "ConsoleLogin", "success") == []
    assert refs("keen-agent", "endpoint.disk_encryption.observed") == [
        "CRY-3",
        "CRY-4",
        "END-40",
    ]
    assert refs("keen-agent", "endpoint.session_inventory.observed") == ["END-40"]
    assert refs("keen-agent", "user.authenticated", "failure") == ["IAC-203", "IAC-5"]
    assert refs("keen-agent", "security.alert.generated") == ["MON-1", "MON-121"]
    assert refs("keen-agent", "log.line.collected") == []


def test_no_two_built_in_rules_for_one_source_share_a_control():
    # An event records each control under one rule, so Rule insights would
    # report the other rule's matches as going unrecorded.
    first = {}
    for rule in RULES:
        source = rule["when"].get("source") or rule["when"]["source_regex"]
        for target in rule["map_to"]:
            assert first.setdefault((source, target["ref"]), rule["id"]) == rule["id"]


def test_downgrading_removes_only_the_rules_still_as_added(installation):
    db = installation
    add_rules(db.connection(), RULES)
    row = _stored(db)
    rules = deepcopy(row.document["rules"])
    edited = next(rule for rule in rules if rule["id"] == "keen-server-logins")
    edited["enabled"] = False
    row.document = {"rules": rules}
    db.commit()
    assert remove_rules(db.connection(), RULES) == len(RULES) - 3
    after = _stored(db)
    assert after.document["rules"] == [OWN, OTHER, edited]
    assert after.version == 5


def test_nothing_changes_without_stored_rules_or_the_framework(contract_db):
    db = contract_db
    assert add_rules(db.connection(), RULES) == 0
    assert db.get(ManagedConfiguration, "rules") is None
    db.add(ManagedConfiguration(name="rules", document={"rules": []}, version=1))
    db.commit()
    assert add_rules(db.connection(), RULES) == 0
    assert _stored(db).version == 1
