"""Agent administration through the assembled application: unknown agents and
changes to what an agent may send.

Requests use real password sign-in, CSRF and the administrator check.
Moving an agent to public subjects creates the shared subject key, which is
stored encrypted with the integration secret key.
"""

import uuid
from types import SimpleNamespace

import pytest
from app.db.models import KeenAgent, UeuSettings
from app.integrations.transport import decrypt
from app.security.agent_signatures import unb64url
from cryptography.fernet import Fernet

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)


@pytest.fixture
def agents(keen, monkeypatch):  # noqa: F811 - fixture imported above
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    admin = keen.client(keen.user(role="admin"))

    def call(client, method, path, body=None):
        return client.request(
            method, "/v1/admin/agents" + path, json=body, headers=unsafe(client)
        )

    def register(name="policy-host"):
        response = call(admin, "POST", "", {"name": name, "agent_type": "server"})
        assert response.status_code == 201, response.text
        return response.json()["agent"]

    return SimpleNamespace(keen=keen, admin=admin, call=call, register=register)


CHANGES = [
    ("PATCH", "", {"agent_type": "server"}),
    ("POST", "/rotate", {"expires_days": 30}),
    ("PATCH", "/evidence", {"evidence_mode_ceiling": "all_lines"}),
    ("POST", "/revoke", None),
]


@pytest.mark.parametrize(
    "method,suffix,body", CHANGES, ids=[c[1] or "/" for c in CHANGES]
)
def test_changes_to_an_unknown_agent_are_refused(agents, method, suffix, body):
    missing = agents.call(agents.admin, method, f"/{uuid.uuid4()}{suffix}", body)

    assert (missing.status_code, missing.json()["detail"]) == (404, "Agent not found")
    # Members without the administrator role are refused before any lookup.
    member = agents.keen.client(agents.keen.user("events.read", "isms.manage"))
    refused = agents.call(member, method, f"/{uuid.uuid4()}{suffix}", body)
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "Admin role required",
    )


def test_an_agent_moves_to_public_subjects_and_keeps_its_ceiling(agents):
    db, admin, call = agents.keen.db, agents.admin, agents.call
    agent = agents.register()
    assert (agent["subject_type"], agent["evidence_mode_ceiling"]) == (
        "pairwise",
        "events",
    )

    moved = call(admin, "PATCH", f"/{agent['id']}/evidence", {"subject_type": "public"})

    assert moved.status_code == 200, moved.text
    changed = moved.json()["agent"]
    # Only the setting sent changes.
    assert (changed["subject_type"], changed["evidence_mode_ceiling"]) == (
        "public",
        "events",
    )
    db.expire_all()
    assert db.get(KeenAgent, agent["id"]).subject_type == "public"
    # The shared key exists before any public-subject agent receives it.
    key = decrypt(db.get(UeuSettings, 1).public_subject_key)["key"]
    assert len(unb64url(key)) == 32

    back = call(
        admin, "PATCH", f"/{agent['id']}/evidence", {"subject_type": "pairwise"}
    )
    assert back.json()["agent"]["subject_type"] == "pairwise"
    db.expire_all()
    # The shared key stays for the other agents that may use it.
    assert decrypt(db.get(UeuSettings, 1).public_subject_key)["key"] == key


def test_public_subjects_are_refused_without_the_integration_key(agents, monkeypatch):
    db = agents.keen.db
    agent = agents.register()
    monkeypatch.delenv("KEEN_INTEGRATION_SECRET_KEY")

    refused = agents.call(
        agents.admin, "PATCH", f"/{agent['id']}/evidence", {"subject_type": "public"}
    )

    assert (refused.status_code, refused.json()["detail"]) == (
        503,
        "Public subjects need KEEN_INTEGRATION_SECRET_KEY on the API",
    )
    db.expire_all()
    assert db.get(KeenAgent, agent["id"]).subject_type == "pairwise"
    assert db.get(UeuSettings, 1) is None
