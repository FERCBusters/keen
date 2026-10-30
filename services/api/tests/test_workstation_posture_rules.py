"""Workstation posture rules applied to single observations, and the latest
observation pointer, without a database."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from app.services import workstation_posture as wp


def event(doc, when=datetime(2026, 10, 1)):
    return SimpleNamespace(id="e1", timestamp=when, normalized_payload={"ueu": doc})


def test_events_that_are_not_posture_are_ignored():
    for doc in (None, {"event_type": 5}):
        found = event(doc)
        assert wp.observation(found) is None and wp.coverage(found) is None
    run = {"event_type": wp.COVERAGE, "attributes": ["not", "an", "object"]}
    assert wp.coverage(event(run)) is None


def observed(check, facts, provider="", reason="observed"):
    return {"check": check, "facts": facts, "provider": provider, "reason": reason}


@pytest.mark.parametrize(
    "found, status",
    [
        (observed("disk_encryption", {"transition_in_progress": True}), "attention"),
        (
            observed("disk_encryption", {"fully_encrypted": True}, "bitlocker"),
            "unknown",
        ),
        (observed("firewall", {"enabled": True, "manager_active": True}), "met"),
        (observed("firewall", {"input_policy": "ACCEPT"}), "unknown"),
        (
            observed("automatic_updates", {"configuration_effective": "unknown"}),
            "unknown",
        ),
        (observed("battery_health", {}), "unknown"),
    ],
)
def test_rules_for_single_observations(found, status):
    assert wp.evaluate(found)[0] == status


def test_targets_without_a_comparable_unit_are_unknown():
    assert wp._target(5, (10, "lte", "days"), str) == ("met", "5; target lte 10 days")
    assert wp._target(5, (10, "about", "days"), str)[0] == "unknown"
    facts = {
        "antivirus_enabled": True,
        "real_time_protection_enabled": True,
        "signatures_age_days": 3,
    }
    assert wp.evaluate(observed("antivirus", facts)) == ("met", "")
    status, detail = wp.evaluate(
        observed("antivirus", facts), {"antivirus": (1, "lte", "days")}
    )
    assert status == "attention" and detail.startswith("Signatures 3 days old")


class Pointers:
    """The posture pointer table, held in memory."""

    def __init__(self, row=None):
        self.row = row
        self.flushed = 0

    def get(self, model, key):
        return self.row

    def flush(self):
        self.flushed += 1


def test_the_pointer_moves_only_to_a_later_observation():
    when = datetime(2026, 10, 1)
    row = SimpleNamespace(event_id="old", observed_at=when)
    db = Pointers(row)
    agent = SimpleNamespace(id="agent-1")
    wp._pointer(db, agent, event({}, when - timedelta(hours=1)), "k")
    assert row.event_id == "old" and db.flushed == 0
    wp._pointer(db, agent, event({}, when + timedelta(hours=1)), "k")
    assert row.event_id == "e1" and db.flushed == 1


def test_events_that_are_neither_observation_nor_coverage_move_nothing():
    db = Pointers()
    found = event({"event_type": "user.authenticated"})
    db.get = lambda model, key: found
    agent = SimpleNamespace(id="agent-1", posture_assigned_at=datetime(2026, 1, 1))
    assert wp.record_observation(db, agent, "e1") is None
    assert db.flushed == 0
