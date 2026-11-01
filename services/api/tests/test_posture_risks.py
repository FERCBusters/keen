"""Risks recorded from workstation posture, through the assembled application."""

import uuid
from datetime import timedelta

import pytest
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    AgentPostureObservation,
    ControlItem,
    Event,
    IsmsPerson,
    IsmsPersonAsset,
    KeenAgent,
    Mapping,
    PostureRiskLink,
    Risk,
    RiskAsset,
    RiskControlLink,
)
from app.services import posture_risks
from cryptography.fernet import Fernet

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_assembled_authority_regressions import unsafe

FRAMEWORK = "A"


@pytest.fixture(autouse=True)
def integration_secret(monkeypatch):
    # Keen Agent identities need KEEN's agent keys, stored with this key.
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())


def person_with_workstation(keen, name="laptop-01"):
    admin = keen.user(role="admin")
    client = keen.client(admin)
    person = IsmsPerson(name="Pat Example")
    keen.db.add(person)
    keen.db.commit()
    response = client.post(
        "/v1/admin/agents",
        json=dict(name=name, agent_type="workstation", person_id=str(person.id)),
        headers=unsafe(client),
    )
    assert response.status_code == 201, response.text
    agent_id = response.json()["agent"]["id"]
    agent = keen.db.get(KeenAgent, agent_id)
    agent.posture_assigned_at = utc_now_naive() - timedelta(days=1)
    keen.db.commit()
    return admin, client, person, agent


def observe(keen, agent, check="disk_encryption", **facts):
    """Store a current posture observation as the receiver would."""
    facts = facts or {"encrypted": False, "enabled": True}
    doc = {
        "event_type": f"endpoint.{check}.observed",
        "catalogue": {"id": "keen", "version": "2026.10"},
        "source": {"component": "lsblk"},
        "subject": {},
        "event": {"reason_code": "measured", "outcome": "observed"},
        "observation": {"scope": "/", "facts": facts},
    }
    now = utc_now_naive()
    event = Event(
        timestamp=now,
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
    keen.db.add(
        AgentPostureObservation(
            agent_id=agent.id,
            stream_key=uuid.uuid4().hex,
            event_id=event.id,
            observed_at=now,
        )
    )
    keen.db.commit()
    return event


def check(client, person, key="disk_encryption"):
    response = client.get(f"/v1/isms/people/{person.id}/workstation-posture")
    assert response.status_code == 200, response.text
    data = response.json()
    found = next(c for c in data["devices"][0]["checks"] if c["check"] == key)
    return data, found


def body(agent, **extra):
    base = {
        "agent_id": agent.id,
        "check": "disk_encryption",
        "title": "Laptop disk is not encrypted",
        "threat_summary": "A lost laptop exposes its disk.",
        "framework": FRAMEWORK,
    }
    base.update(extra)
    return base


def test_recorded_risk_is_linked_and_its_acceptance_lapses(keen):
    admin, client, person, agent = person_with_workstation(keen)
    event = observe(keen, agent)
    control = ControlItem(
        framework_slug=FRAMEWORK, ref="A.8.24", type="annex_control", title="Crypto"
    )
    keen.db.add(control)
    keen.db.flush()
    keen.db.add(Mapping(event_id=event.id, control_item_id=control.id, method="rule"))
    keen.db.commit()

    data, found = check(client, person)
    assert data["can_create_risk"] is True
    assert found["status"] == "attention" and found["risks"] == []

    path = f"/v1/isms/people/{person.id}/workstation-posture/risks"
    today = utc_now_naive().date()
    accept = body(
        agent,
        treatment_strategy="accept",
        risk_owner_user_id=str(admin.id),
        treatment_due_at=(today + timedelta(days=30)).isoformat(),
    )
    # Unsafe requests need the CSRF token and Origin.
    assert client.post(path, json=accept).status_code == 403
    for bad, status in (
        ({**accept, "risk_owner_user_id": None}, 400),
        ({**accept, "treatment_due_at": None}, 400),
        (
            {**accept, "treatment_due_at": (today - timedelta(days=1)).isoformat()},
            400,
        ),
        ({**accept, "treatment_strategy": "ignore"}, 400),
        ({**accept, "check": "telepathy"}, 400),
        ({**accept, "title": "   "}, 400),
        ({**accept, "agent_id": str(uuid.uuid4())}, 404),
        ({**accept, "unexpected": 1}, 422),
    ):
        response = client.post(path, json=bad, headers=unsafe(client))
        assert response.status_code == status, (bad, response.text)
    assert keen.db.query(Risk).count() == 0

    response = client.post(path, json=accept, headers=unsafe(client))
    assert response.status_code == 201, response.text
    created = response.json()
    risk = keen.db.get(Risk, uuid.UUID(created["risk_id"]))
    asset = keen.db.get(RiskAsset, uuid.UUID(created["asset_id"]))
    assert risk.asset_id == asset.id and asset.name == "laptop-01 (workstation)"
    assert (risk.treatment_strategy, risk.risk_owner_user_id) == ("accept", admin.id)
    assert keen.db.get(IsmsPersonAsset, (person.id, asset.id)) is not None
    links = keen.db.query(RiskControlLink).filter_by(risk_id=risk.id).all()
    assert [link.control_item_id for link in links] == [control.id]
    link = keen.db.query(PostureRiskLink).one()
    assert (link.origin, link.origin_key, link.check_key, link.subject_key) == (
        "workstation",
        agent.id,
        "disk_encryption",
        "",
    )

    _, found = check(client, person)
    assert found["status"] == "accepted"
    assert found["risks"] == [
        {
            "id": str(risk.id),
            "title": "Laptop disk is not encrypted",
            "accepted": True,
            "acceptance_expired": False,
            "due": (today + timedelta(days=30)).isoformat(),
        }
    ]

    # A second risk for the same workstation reuses its asset.
    again = client.post(
        path, json=body(agent, title="Second look"), headers=unsafe(client)
    )
    assert again.status_code == 201
    assert again.json()["asset_id"] == created["asset_id"]
    assert keen.db.query(RiskAsset).count() == 1

    # Acceptance lapses at its review date; the result needs attention again.
    keen.db.expire_all()
    risk = keen.db.get(Risk, risk.id)
    risk.treatment_due_at = today - timedelta(days=1)
    keen.db.commit()
    _, found = check(client, person)
    assert found["status"] == "attention"
    assert found["risks"][0]["acceptance_expired"] is True
    # Closing an accepted risk also ends the acceptance.
    risk.treatment_due_at = today + timedelta(days=5)
    risk.treatment_status = "closed"
    keen.db.commit()
    assert check(client, person)[1]["status"] == "attention"


def test_risk_recording_permissions_and_title_visibility(keen):
    admin, client, person, agent = person_with_workstation(keen)
    observe(keen, agent)
    path = f"/v1/isms/people/{person.id}/workstation-posture/risks"
    assert keen.client().post(path, json=body(agent)).status_code in (401, 403)
    for codes in (
        ("isms.read", "events.read"),
        ("risk.manage",),
        ("risk.read", "events.read"),
    ):
        user_client = keen.client(keen.user(*codes))
        response = user_client.post(path, json=body(agent), headers=unsafe(user_client))
        assert response.status_code == 403, codes
    manager = keen.client(keen.user("risk.manage", "events.read"))
    response = manager.post(path, json=body(agent), headers=unsafe(manager))
    assert response.status_code == 201, response.text

    reader = keen.client(keen.user("isms.read", "events.read"))
    data, found = check(reader, person)
    assert data["can_create_risk"] is False
    # Without risk access the risk is acknowledged, but its title is not shown.
    assert found["risks"][0]["title"] is None
    risk_reader = keen.client(keen.user("isms.read", "events.read", "risk.read"))
    assert check(risk_reader, person)[1]["risks"][0]["title"] == (
        "Laptop disk is not encrypted"
    )

    # Another person's workstation cannot be named through this person.
    other = IsmsPerson(name="Someone else")
    keen.db.add(other)
    keen.db.commit()
    elsewhere = f"/v1/isms/people/{other.id}/workstation-posture/risks"
    response = client.post(elsewhere, json=body(agent), headers=unsafe(client))
    assert response.status_code == 404
    missing = f"/v1/isms/people/{uuid.uuid4()}/workstation-posture/risks"
    assert (
        client.post(missing, json=body(agent), headers=unsafe(client)).status_code
        == 404
    )


@pytest.mark.parametrize("feature", ["risks", "risk_cia"])
def test_disabled_risk_management_hides_posture_risks(keen, feature):
    admin, client, person, agent = person_with_workstation(keen)
    observe(keen, agent)
    path = f"/v1/isms/people/{person.id}/workstation-posture/risks"
    assert (
        client.post(path, json=body(agent), headers=unsafe(client)).status_code == 201
    )
    response = client.put(
        "/v1/admin/features",
        json={"version": 0, "changes": {feature: False}},
        headers=unsafe(client),
    )
    assert response.status_code == 200, response.text
    data, found = check(client, person)
    assert data["can_create_risk"] is False and found["risks"] == []
    assert (
        client.post(path, json=body(agent), headers=unsafe(client)).status_code == 404
    )


def test_links_are_bounded_to_known_origins(contract_db):
    assert posture_risks.linked(contract_db, "elsewhere", ["a"], titles=True) == {}
    assert posture_risks.linked(contract_db, "workstation", [], titles=True) == {}
    assert posture_risks.mapped_controls(contract_db, [], FRAMEWORK) == []
