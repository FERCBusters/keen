"""A closed risk treats the posture failure it was recorded for, until it changes.

Through the assembled application against PostgreSQL: a failing cloud check,
cloud resource or workstation check whose linked risk closed after the failure
began reads as Risk treated. KEEN dates each failure by its own clock as it
reads results, so the result needs attention again when it fails for a
resource or scope it did not list when the risk closed, or fails again after
passing. A result KEEN could not read keeps its date.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    AgentPostureObservation,
    CloudPostureScope,
    Event,
    IsmsEffectivenessMeasure,
)
from app.ingest.cloud import runner
from app.ingest.cloud.base import Check, ScopeResult, attention_record
from app.services import posture_risks, workstation_posture
from app.services import risks as risk_service

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_assembled_authority_regressions import unsafe
from tests.test_cloud_posture import ACCOUNT, READER, vendor_of
from tests.test_cloud_posture import cloud as cloud
from tests.test_posture_risks import integration_secret as integration_secret
from tests.test_posture_risks import person_with_workstation

MFA = "identity.mfa"


class Clock:
    """KEEN's clock for refreshes, observations and closing risks."""

    def __init__(self, monkeypatch, start):
        self.now = start
        for module in (runner, workstation_posture):
            monkeypatch.setattr(module, "utc_now_naive", lambda: self.now)
        monkeypatch.setattr(risk_service, "_utcnow", lambda: self.now)

    def tick(self) -> datetime:
        self.now += timedelta(minutes=5)
        return self.now


@pytest.fixture
def clock(monkeypatch):
    return Clock(monkeypatch, datetime(2026, 11, 2, 9, 0))


def mfa_scope(status, subjects, detail=""):
    return ScopeResult(
        ACCOUNT,
        "Production",
        [Check(MFA, "MFA for console users", status, detail, subjects)],
    )


def refresh(keen, cloud, clock, status, subjects=()):
    clock.tick()
    cloud["results"] = [mfa_scope(status, list(subjects))]
    # The refresh starts by rolling its session back, as the worker's does. The
    # test shares one connection with the application, so end the test's own
    # transaction first and the rollback reaches nothing the requests saved.
    keen.db.commit()
    runner.refresh(keen.db, "aws")


def mfa_check(client, vendor_id):
    response = client.get(f"/v1/isms/vendors/{vendor_id}/cloud-posture")
    assert response.status_code == 200, response.text
    (scope,) = response.json()["providers"][0]["scopes"]
    (check,) = scope["checks"]
    return scope, check


def stored_record(keen):
    keen.db.expire_all()
    row = keen.db.query(CloudPostureScope).filter_by(scope_key=ACCOUNT).one()
    return row.checks[0]["attention"]


def set_status(keen, client, risk_id, status, clock):
    clock.tick()
    response = client.patch(
        f"/v1/risks/{risk_id}",
        json={"treatment_status": status},
        headers=unsafe(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["treatment_status"] == status


def record_cloud_risk(client, vendor_id, subject=""):
    response = client.post(
        f"/v1/isms/vendors/{vendor_id}/cloud-posture/risks",
        json={
            "provider": "aws",
            "scope_key": ACCOUNT,
            "check": MFA,
            "subject_key": subject,
            "title": f"Console users without MFA {subject}".strip(),
            "framework": "A",
        },
        headers=unsafe(client),
    )
    assert response.status_code == 201, response.text
    return response.json()["risk_id"]


def linked(risk_id, title, *, closed, treated):
    return {
        "id": risk_id,
        "title": title,
        "accepted": False,
        "acceptance_expired": False,
        "due": None,
        "closed": closed,
        "treated": treated,
    }


def test_a_closed_risk_treats_a_cloud_check_until_it_fails_for_more(keen, cloud, clock):
    refresh(keen, cloud, clock, "attention", ["bob"])
    first_failure = clock.now
    vendor_id = vendor_of(keen)
    client = keen.client(keen.user(*READER, "risk.manage"))
    risk_id = record_cloud_risk(client, vendor_id)
    title = "Console users without MFA"

    scope, check = mfa_check(client, vendor_id)
    assert check["status"] == "attention"
    assert check["risks"] == [linked(risk_id, title, closed=False, treated=False)]

    set_status(keen, client, risk_id, "closed", clock)
    scope, check = mfa_check(client, vendor_id)
    assert check["status"] == "treated"
    assert check["risks"] == [linked(risk_id, title, closed=True, treated=True)]
    # A treated check is judged, and is not met.
    assert (scope["met"], scope["judged"]) == (0, 1)

    # The same failure read again stays treated, and so does one KEEN could
    # not read for a while.
    refresh(keen, cloud, clock, "attention", ["bob"])
    assert mfa_check(client, vendor_id)[1]["status"] == "treated"
    refresh(keen, cloud, clock, "unknown")
    assert mfa_check(client, vendor_id)[1]["status"] == "unknown"
    refresh(keen, cloud, clock, "attention", ["bob"])
    assert mfa_check(client, vendor_id)[1]["status"] == "treated"
    assert stored_record(keen) == {
        "since": first_failure.isoformat(),
        "subjects": {"bob": first_failure.isoformat()},
        "total": 1,
    }

    # A second console user without MFA is a change the closed risk predates.
    refresh(keen, cloud, clock, "attention", ["bob", "carol"])
    grew = clock.now
    scope, check = mfa_check(client, vendor_id)
    assert check["status"] == "attention"
    assert check["risks"] == [linked(risk_id, title, closed=True, treated=False)]
    assert stored_record(keen) == {
        "since": grew.isoformat(),
        "subjects": {"bob": first_failure.isoformat(), "carol": grew.isoformat()},
        "total": 2,
    }

    # Reopened and closed again, the risk treats the larger failure.
    set_status(keen, client, risk_id, "in_progress", clock)
    assert mfa_check(client, vendor_id)[1]["status"] == "attention"
    set_status(keen, client, risk_id, "closed", clock)
    assert mfa_check(client, vendor_id)[1]["status"] == "treated"
    # One user fixed leaves the rest treated.
    refresh(keen, cloud, clock, "attention", ["carol"])
    assert mfa_check(client, vendor_id)[1]["status"] == "treated"
    assert stored_record(keen) == {
        "since": grew.isoformat(),
        "subjects": {"carol": grew.isoformat()},
        "total": 1,
    }

    # Passing ends the failure; failing again starts a new one.
    refresh(keen, cloud, clock, "met")
    assert mfa_check(client, vendor_id)[1]["status"] == "met"
    assert stored_record(keen) is None
    refresh(keen, cloud, clock, "attention", ["carol"])
    again = clock.now
    scope, check = mfa_check(client, vendor_id)
    assert check["status"] == "attention"
    assert check["risks"] == [linked(risk_id, title, closed=True, treated=False)]
    assert stored_record(keen)["since"] == again.isoformat()


def test_closed_risks_treat_the_resources_they_were_recorded_for(keen, cloud, clock):
    refresh(keen, cloud, clock, "attention", ["bob", "carol"])
    vendor_id = vendor_of(keen)
    owner = keen.user(*READER, "risk.manage")
    client = keen.client(owner)
    bob = record_cloud_risk(client, vendor_id, "bob")
    set_status(keen, client, bob, "closed", clock)

    _, check = mfa_check(client, vendor_id)
    # Carol still has no risk, so the check needs attention.
    assert check["status"] == "attention"
    assert [(s["name"], s["treated"]) for s in check["subjects"]] == [
        ("bob", True),
        ("carol", False),
    ]
    assert check["subjects"][0]["risks"] == [
        linked(bob, "Console users without MFA bob", closed=True, treated=True)
    ]

    # An accepted risk for the other covers the rest, so the check is treated.
    today = utc_now_naive().date()
    response = client.post(
        f"/v1/isms/vendors/{vendor_id}/cloud-posture/risks",
        json={
            "provider": "aws",
            "scope_key": ACCOUNT,
            "check": MFA,
            "subject_key": "carol",
            "title": "Carol is waiting for a key",
            "treatment_strategy": "accept",
            "risk_owner_user_id": str(owner.id),
            "treatment_due_at": (today + timedelta(days=30)).isoformat(),
            "framework": "A",
        },
        headers=unsafe(client),
    )
    assert response.status_code == 201, response.text
    _, check = mfa_check(client, vendor_id)
    assert check["status"] == "treated"
    assert [(s["name"], s["treated"]) for s in check["subjects"]] == [
        ("bob", True),
        ("carol", False),
    ]
    assert check["subjects"][1]["risks"][0]["accepted"] is True

    # A third user is listed with no risk of their own.
    refresh(keen, cloud, clock, "attention", ["bob", "carol", "dave"])
    _, check = mfa_check(client, vendor_id)
    assert check["status"] == "attention"
    assert [(s["name"], s["treated"]) for s in check["subjects"]] == [
        ("bob", True),
        ("carol", False),
        ("dave", False),
    ]

    # Bob passing and failing again is a new failure his closed risk predates.
    refresh(keen, cloud, clock, "attention", ["carol", "dave"])
    refresh(keen, cloud, clock, "attention", ["bob", "carol", "dave"])
    _, check = mfa_check(client, vendor_id)
    assert [(s["name"], s["treated"]) for s in check["subjects"]] == [
        ("bob", False),
        ("carol", False),
        ("dave", False),
    ]


def test_a_check_naming_only_some_of_its_resources_needs_attention(keen, cloud, clock):
    refresh(keen, cloud, clock, "attention", ["bob"])
    # The vendor counted more users without MFA than the result names, as it
    # does beyond the names a result keeps.
    row = keen.db.query(CloudPostureScope).filter_by(scope_key=ACCOUNT).one()
    row.checks = [{**row.checks[0], "subjects_total": 2}]
    keen.db.commit()
    vendor_id = vendor_of(keen)
    client = keen.client(keen.user(*READER, "risk.manage"))
    bob = record_cloud_risk(client, vendor_id, "bob")
    set_status(keen, client, bob, "closed", clock)
    _, found = mfa_check(client, vendor_id)
    assert [(s["name"], s["treated"]) for s in found["subjects"]] == [("bob", True)]
    assert found["status"] == "attention"


# --- workstations -----------------------------------------------------------------


def observe(
    keen,
    agent,
    clock,
    scope="/",
    encrypted=False,
    *,
    check="disk_encryption",
    component="lsblk",
    facts=None,
):
    """An observation the receiver records for the agent, at the next tick.

    Disk encryption by default; ``encrypted=None`` is a reading the device
    could not establish.
    """
    when = clock.tick()
    if facts is None:
        facts = {} if encrypted is None else {"encrypted": encrypted, "enabled": True}
    doc = {
        "event_type": f"endpoint.{check}.observed",
        "catalogue": {"id": "keen", "version": "2026.10"},
        "source": {"component": component},
        "subject": {},
        "event": {"reason_code": "measured", "outcome": "observed"},
        "observation": {"scope": scope, "facts": facts},
    }
    event = Event(
        timestamp=when,
        source="keen-agent",
        system=agent.name,
        action=doc["event_type"],
        outcome="observed",
        severity=4,
        summary="posture",
        raw_pointer={},
        normalized_payload={"ueu": doc},
        external_id=agent.id + ":" + uuid.uuid4().hex,
    )
    keen.db.add(event)
    keen.db.flush()
    workstation_posture.record_observation(keen.db, agent, event.id)
    keen.db.commit()
    return when


def device_check(client, person, key="disk_encryption"):
    response = client.get(f"/v1/isms/people/{person.id}/workstation-posture")
    assert response.status_code == 200, response.text
    checks = response.json()["devices"][0]["checks"]
    return next(c for c in checks if c["check"] == key)


def record_device_risk(client, person, agent, check="disk_encryption", **fields):
    response = client.post(
        f"/v1/isms/people/{person.id}/workstation-posture/risks",
        json={
            "agent_id": agent.id,
            "check": check,
            "title": "Laptop disk is not encrypted",
            "framework": "A",
            **fields,
        },
        headers=unsafe(client),
    )
    assert response.status_code == 201, response.text
    return response.json()["risk_id"]


def failure_starts(keen, agent):
    keen.db.expire_all()
    rows = keen.db.query(AgentPostureObservation).filter_by(agent_id=agent.id).all()
    return sorted(row.attention_since for row in rows if row.attention_since)


@pytest.fixture
def workstation(keen, monkeypatch):
    clock = Clock(monkeypatch, utc_now_naive() - timedelta(hours=2))
    admin, client, person, agent = person_with_workstation(keen)
    return clock, client, person, agent, admin


def test_a_closed_risk_treats_a_workstation_check_until_a_scope_fails_anew(
    keen, workstation
):
    clock, client, person, agent, admin = workstation
    root_failed = observe(keen, agent, clock, "/")
    risk_id = record_device_risk(client, person, agent)
    title = "Laptop disk is not encrypted"
    assert device_check(client, person)["status"] == "attention"

    set_status(keen, client, risk_id, "closed", clock)
    check = device_check(client, person)
    assert check["status"] == "treated"
    assert check["risks"] == [linked(risk_id, title, closed=True, treated=True)]

    # The same failure observed again stays treated, and so does a reading the
    # device could not establish in between.
    observe(keen, agent, clock, "/")
    observe(keen, agent, clock, "/", encrypted=None)
    assert device_check(client, person)["status"] == "unknown"
    observe(keen, agent, clock, "/")
    assert device_check(client, person)["status"] == "treated"
    assert failure_starts(keen, agent) == [root_failed]

    # A second unencrypted volume is a failure the closed risk predates.
    data_failed = observe(keen, agent, clock, "/data")
    check = device_check(client, person)
    assert check["status"] == "attention"
    assert check["risks"] == [linked(risk_id, title, closed=True, treated=False)]
    assert failure_starts(keen, agent) == [root_failed, data_failed]

    # Encrypting it leaves the earlier failure, which the risk treats.
    observe(keen, agent, clock, "/data", encrypted=True)
    assert device_check(client, person)["status"] == "treated"
    assert failure_starts(keen, agent) == [root_failed]

    # Passing ends the failure; failing again starts a new one.
    observe(keen, agent, clock, "/", encrypted=True)
    assert device_check(client, person)["status"] == "met"
    assert failure_starts(keen, agent) == []
    again = observe(keen, agent, clock, "/")
    assert device_check(client, person)["status"] == "attention"
    assert failure_starts(keen, agent) == [again]

    # An accepted risk alongside reads as accepted, whatever a closed one treats.
    set_status(keen, client, risk_id, "in_progress", clock)
    set_status(keen, client, risk_id, "closed", clock)
    assert device_check(client, person)["status"] == "treated"
    record_device_risk(
        client,
        person,
        agent,
        title="Encryption waits for the new laptop",
        treatment_strategy="accept",
        risk_owner_user_id=str(admin.id),
        treatment_due_at=(utc_now_naive().date() + timedelta(days=30)).isoformat(),
    )
    check = device_check(client, person)
    assert check["status"] == "accepted"
    assert [(r["accepted"], r["treated"]) for r in check["risks"]] == [
        (False, True),
        (True, False),
    ]


def test_a_failure_with_no_recorded_start_needs_attention(keen, workstation):
    clock, client, person, agent, _ = workstation
    observe(keen, agent, clock, "/")
    observe(keen, agent, clock, "/data")
    # A result read before KEEN dated failures has no start; the other
    # volume's start leaves the check undated all the same.
    keen.db.expire_all()
    rows = keen.db.query(AgentPostureObservation).filter_by(agent_id=agent.id).all()
    assert len(rows) == 2
    undated = min(rows, key=lambda row: row.attention_since)
    undated.attention_since = None
    keen.db.commit()
    risk_id = record_device_risk(client, person, agent)
    set_status(keen, client, risk_id, "closed", clock)
    check = device_check(client, person)
    assert check["status"] == "attention"
    assert [(r["closed"], r["treated"]) for r in check["risks"]] == [(True, False)]


def test_a_check_with_a_target_is_dated_by_the_organisations_target(keen, workstation):
    clock, client, person, agent, _ = workstation
    keen.db.add(
        IsmsEffectivenessMeasure(
            framework_slug="iso27001",
            summary="Screens lock within 15 minutes",
            metric_key="keen.workstation.screen_lock_seconds",
            target_value=900,
            target_unit="seconds",
            threshold_operator="lte",
        )
    )
    keen.db.commit()
    # Locking after 30 minutes is configured, and fails the target alone.
    failed = observe(
        keen,
        agent,
        clock,
        "session",
        check="screen_lock",
        component="gnome",
        facts={"automatic_lock_configured": True, "lock_after_seconds": 1800},
    )
    assert device_check(client, person, "screen_lock")["status"] == "attention"
    assert failure_starts(keen, agent) == [failed]
    risk_id = record_device_risk(client, person, agent, "screen_lock", title="Lock")
    set_status(keen, client, risk_id, "closed", clock)
    assert device_check(client, person, "screen_lock")["status"] == "treated"


def test_a_check_listing_no_resources_is_treated_by_its_own_risk(keen, cloud, clock):
    refresh(keen, cloud, clock, "attention")
    vendor_id = vendor_of(keen)
    client = keen.client(keen.user(*READER, "risk.manage"))
    _, check = mfa_check(client, vendor_id)
    assert (check["status"], check["subjects"], check["risks"]) == ("attention", [], [])
    risk_id = record_cloud_risk(client, vendor_id)
    assert mfa_check(client, vendor_id)[1]["status"] == "attention"
    set_status(keen, client, risk_id, "closed", clock)
    assert mfa_check(client, vendor_id)[1]["status"] == "treated"


def test_a_check_failing_for_more_resources_than_it_names_starts_afresh():
    earlier, now = datetime(2026, 11, 1, 8, 0), datetime(2026, 11, 2, 8, 0)
    before = {
        "since": earlier.isoformat(),
        "subjects": {"bob": earlier.isoformat()},
        "total": 60,
    }
    # The named resources are the same, but the vendor counts one more.
    assert attention_record(before, "attention", ["bob"], 61, now) == {
        "since": now.isoformat(),
        "subjects": {"bob": earlier.isoformat()},
        "total": 61,
    }
    assert attention_record(before, "attention", ["bob"], 59, now) == {
        **before,
        "total": 59,
    }
    # The same count, failing for a resource it did not name before.
    assert attention_record(before, "attention", ["carol"], 60, now) == {
        "since": now.isoformat(),
        "subjects": {"carol": now.isoformat()},
        "total": 60,
    }
    assert attention_record(before, "unknown", [], 0, now) is before
    assert attention_record(before, "info", [], 0, now) is None


def test_a_risk_closed_as_the_failure_began_treats_it():
    began = datetime(2026, 11, 2, 8, 0)

    def settled(since, *closed):
        risks = [{"id": str(n), "closed_at": at} for n, at in enumerate(closed)]
        return posture_risks.settle(risks, since), [r["treated"] for r in risks]

    earlier, later = began - timedelta(seconds=1), began + timedelta(seconds=1)
    assert settled(began, began) == (True, [True])
    assert settled(began, earlier) == (False, [False])
    # One treating risk among others treats the result, wherever it is listed.
    assert settled(began, later, earlier, None) == (True, [True, False, False])
    assert settled(began, None, earlier, later) == (True, [False, False, True])
    # A failure with no recorded start is treated by none.
    assert settled(None, later, began) == (False, [False, False])
