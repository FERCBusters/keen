"""Refusals when recording risks from workstation posture.

Recording a workstation risk needs risk.manage even for an ISMS manager who
passes the write gate for ISMS paths, and the shared recorder links risks to
the known posture origins only.
"""

import uuid

import pytest
from app.db.models import PostureRiskLink, Risk
from app.services import posture_risks
from app.services.errors import DomainError

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_assembled_authority_regressions import unsafe
from tests.test_posture_risks import body, observe, person_with_workstation

# Imported fixture: KEEN's agent keys need the integration secret key.
from tests.test_posture_risks import integration_secret as integration_secret


def test_recording_a_workstation_risk_needs_risk_manage_beyond_isms_management(keen):
    _, admin_client, person, agent = person_with_workstation(keen)
    observe(keen, agent)
    path = f"/v1/isms/people/{person.id}/workstation-posture/risks"
    manager = keen.client(keen.user("isms.read", "events.read", "isms.manage"))
    shown = manager.get(f"/v1/isms/people/{person.id}/workstation-posture")
    assert shown.status_code == 200 and shown.json()["can_create_risk"] is False
    refused = manager.post(path, json=body(agent), headers=unsafe(manager))
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "risk.manage is required to record a risk",
    )
    keen.db.expire_all()
    assert keen.db.query(Risk).count() == 0
    assert keen.db.query(PostureRiskLink).count() == 0

    recorded = admin_client.post(path, json=body(agent), headers=unsafe(admin_client))
    assert recorded.status_code == 201, recorded.text
    assert keen.db.query(PostureRiskLink).one().origin == "workstation"


def test_a_risk_is_never_linked_to_an_unknown_posture_origin(keen):
    owner = keen.user("risk.manage")
    with pytest.raises(DomainError) as refused:
        posture_risks.record(
            keen.db,
            owner,
            posture_risks.PostureRiskInput(title="Telepathy risk"),
            origin="telepathy",
            origin_key="mind",
            scope_key="all",
            check_key="thoughts",
            asset_id=uuid.uuid4(),
            controls=[],
            framework="A",
            request_path=f"/v1/isms/vendors/{uuid.uuid4()}/cloud-posture/risks",
        )
    assert (refused.value.status_code, refused.value.detail) == (
        400,
        "Unknown posture origin",
    )
    keen.db.rollback()
    assert keen.db.query(Risk).count() == 0
    assert keen.db.query(PostureRiskLink).count() == 0
