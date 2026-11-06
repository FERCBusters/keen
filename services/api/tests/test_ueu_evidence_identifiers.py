"""An evidence identifier names one piece of evidence from one agent.

A signed UEU envelope that reuses an identifier with other content is
refused whole, whether the identifier belongs to earlier UEU evidence or to a
plain record the agent sent before it moved from a token to a key.
"""

import json

from app.db.models import Event, UeuBatch, UeuEvidence

# Imported fixtures: KEEN's agent routes with signing agents and stored objects.
from tests.test_keen_agent import record
from tests.test_ueu_evidence import enrolled, keen, setup  # noqa: F401
from tests.ueu_agent import SimAgent, uuid7

REUSED = {"code": 6, "message": "Evidence identifier reused with other content"}


def test_an_evidence_identifier_resent_with_other_content_is_refused(keen):  # noqa: F811
    agent, _ = enrolled(keen)
    first = agent.observation("firewall", "ufw", {"enabled": True})
    sent, _ = agent.send(first)
    assert sent.status_code == 200, sent.text

    other = agent.observation("firewall", "ufw", {"enabled": False})
    other["evidence_id"] = first["evidence_id"]
    refused, _ = agent.send(other)

    assert refused.status_code == 409, refused.text
    assert refused.json() == REUSED
    with keen.db() as db:
        (event,) = db.query(Event).all()
        assert event.normalized_payload["ueu"]["observation"]["facts"] == {
            "enabled": True
        }
        assert db.query(UeuEvidence).count() == 1
        assert db.query(UeuBatch).count() == 1


def test_evidence_reusing_an_earlier_records_identifier_is_refused(keen):  # noqa: F811
    registered = keen.client.post(
        "/v1/admin/agents", json={"name": "relay", "credential_type": "token"}
    )
    assert registered.status_code == 201, registered.text
    info = registered.json()
    agent_id = info["agent"]["id"]
    identifier = uuid7()
    plain = keen.client.post(
        "/v1/otlp/logs",
        json=record(identity=identifier),
        headers={"Authorization": "Bearer " + info["token"]},
    )
    assert (plain.status_code, plain.json()) == (200, {})
    rotated = keen.client.post(
        f"/v1/admin/agents/{agent_id}/rotate",
        json={"expires_days": 30, "credential_type": "key"},
    )
    assert rotated.status_code == 200, rotated.text
    agent = SimAgent(keen.client)
    assert agent.enroll(rotated.json()["enrollment_code"]).status_code == 201

    env = agent.observation("firewall", "ufw", {"enabled": True})
    env["evidence_id"] = identifier
    refused, _ = agent.send(env)

    assert refused.status_code == 409, refused.text
    assert refused.json() == REUSED
    with keen.db() as db:
        (event,) = db.query(Event).all()
        assert event.external_id == f"{agent_id}:{identifier}"
        assert "ueu" not in json.dumps(event.normalized_payload)
        assert db.query(UeuEvidence).count() == 0
        assert db.query(UeuBatch).count() == 0
