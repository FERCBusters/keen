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

VERSIONS = Path(__file__).resolve().parents[1] / "alembic/versions"
OWN = {"id": "keen-cloud-mfa", "when": {"source": "aws"}, "map_to": "IAC-203"}
OTHER = {"id": "admins-own", "when": {"source": "github"}, "map_to": "CHG-1"}
# The installation no longer has these controls.
MISSING = {"GOV-5", "PES-78"}
FAULT = "system.fault.detected"


def _rules(name):
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RULES


RULES = _rules("0037_builtin_rules")
HOST_RULES = _rules("0038_builtin_host_rules")
# Every built-in rule, in the order the migrations add them.
ALL_RULES = RULES + HOST_RULES


@pytest.fixture
def installation(contract_db):
    db = contract_db
    refs = {target["ref"] for rule in ALL_RULES for target in rule["map_to"]}
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
    for rule in ALL_RULES:
        rule = deepcopy(rule)
        rule["map_to"] = [t for t in rule["map_to"] if t["ref"] not in MISSING]
        if rule["map_to"]:
            _validate_rule(rule, installation)


def test_built_in_rules_match_keens_own_evidence():
    rules = parse_rules({"rules": RULES})
    assert len(rules) == len(RULES)

    def refs(source, action, outcome="info", asset="server"):
        event = {
            "source": source,
            "action": action,
            "outcome": outcome,
            "normalized_payload": {"ueu": {"asset": {"asset_type": asset}}},
        }
        return sorted(evaluate_by_framework(event, rules).get(FRAMEWORK, []))

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
    # Workstations' posture also counts for user devices and portable media;
    # servers' posture counts for the controls both share.
    disks = "endpoint.disk_encryption.observed"
    assert refs("keen-agent", disks, asset="workstation") == [
        "CRY-3",
        "CRY-4",
        "END-40",
    ]
    assert refs("keen-agent", disks) == ["CRY-4"]
    for asset, expected in (
        ("workstation", ["END-40", "NET-4"]),
        ("server", ["NET-4"]),
    ):
        assert refs("keen-agent", "endpoint.firewall.observed", asset=asset) == expected
    assert refs("keen-agent", "endpoint.automatic_updates.observed") == ["VPM-75"]
    assert refs("keen-agent", "endpoint.antivirus.observed") == ["END-1", "END-41"]
    sessions = "endpoint.session_inventory.observed"
    assert refs("keen-agent", sessions, asset="workstation") == ["END-40"]
    assert refs("keen-agent", sessions) == []
    assert refs("keen-agent", "endpoint.screen_lock.observed") == []
    assert refs("keen-agent", "user.authenticated", "failure") == ["IAC-203", "IAC-5"]
    assert refs("keen-agent", "security.alert.generated") == ["MON-1", "MON-121"]
    assert refs("keen-agent", "software.package.changed") == ["CHG-38"]
    assert refs("keen-agent", "log.line.collected") == []


def test_host_rules_match_the_later_catalogues_events():
    rules = parse_rules({"rules": HOST_RULES})
    assert len(rules) == len(HOST_RULES)

    def refs(action, outcome="failure", **attributes):
        event = {
            "source": "keen-agent",
            "action": action,
            "outcome": outcome,
            "normalized_payload": {"ueu": {"attributes": attributes}},
        }
        return sorted(evaluate_by_framework(event, rules).get(FRAMEWORK, []))

    assert refs(FAULT, fault="oom_kill") == ["CAP-7", "MON-4"]
    assert refs(FAULT, fault="io_error") == ["MNT-21", "MON-4"]
    assert refs(FAULT, fault="service_failed") == ["MON-4"]
    # Clocks are recorded synchronising and losing synchronisation alike.
    assert refs("clock.sync.changed", condition="offset") == ["SEA-54"]
    assert refs("clock.sync.changed", "success", condition="source_selected") == [
        "SEA-54"
    ]
    assert refs("dns.lookup.failed") == ["NET-92"]
    assert refs("application.error.logged") == ["MON-120"]
    assert refs("user.account.changed", "success") == ["IAC-198", "IAC-200"]
    # Only completed patching and renewal runs count; other tasks and failures
    # map nothing.
    task = "routine.task.completed"
    assert refs(task, "success", task="patching", tool="apt") == ["VPM-1"]
    assert refs(task, "failure", task="patching", tool="apt") == []
    assert refs(task, "success", task="certificate_renewal", tool="certbot") == [
        "CRY-92",
        "NET-1",
    ]
    assert refs(task, "failure", task="certificate_renewal") == []
    assert refs(task, "success", task="backup") == []
    assert refs(task, "success") == []
    protection = "system.protection.activated"
    assert refs(protection, "success", protection="mandatory_access_control") == [
        "NET-5"
    ]
    assert refs("user.authenticated") == []


def test_no_two_built_in_rules_for_one_source_share_a_control():
    # An event records each control under one rule, so Rule insights would
    # report the other rule's matches as going unrecorded.
    first = {}
    for rule in ALL_RULES:
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


def test_removing_rules_none_still_as_added_changes_nothing(installation):
    db = installation
    add_rules(db.connection(), RULES)
    row = _stored(db)
    # Every built-in rule has been edited since it was added.
    edited = [
        {**rule, "enabled": False}
        if rule["id"] not in {OWN["id"], OTHER["id"]}
        else rule
        for rule in deepcopy(row.document["rules"])
    ]
    row.document = {"rules": edited}
    db.commit()
    revisions = db.query(ManagedConfigurationRevision).filter_by(name="rules").count()

    assert remove_rules(db.connection(), RULES) == 0

    after = _stored(db)
    assert after.document["rules"] == edited
    assert after.version == 4
    assert (
        db.query(ManagedConfigurationRevision).filter_by(name="rules").count()
        == revisions
    )
