"""Vendor asset links, record detail access and cloud posture refusals.

A vendor keeps the assets an edit still selects; record details are refused
to accounts without ISMS read access however they are reached; a provider
without an activity log queues its posture refresh alone; and recording a
cloud risk needs risk.manage on top of reading and managing ISMS records.
"""

import uuid

import pytest
from app.core.config import settings
from app.db.models import IsmsVendor, PostureRiskLink, Risk, RiskAsset
from app.ingest.cloud import runner
from app.services import assurance
from app.services.errors import DomainError

# Imported fixtures: the assembled application and the registers built on it,
# and AWS posture answered by a test provider.
from tests.test_assembled_authority_regressions import keen as keen
from tests.test_assembled_authority_regressions import unsafe
from tests.test_assurance_api_workflows import registers as registers
from tests.test_cloud_posture import ACCOUNT, READER, capture_tasks, vendor_of
from tests.test_cloud_posture import cloud as cloud


def test_editing_a_vendor_keeps_the_assets_it_still_selects(registers):
    vendor = registers.manage(
        "POST",
        "/v1/isms/vendors",
        {
            "name": "Acme",
            "asset_ids": [str(registers.laptop.id), str(registers.phone.id)],
        },
        status=201,
    )
    edited = registers.manage(
        "PATCH",
        f"/v1/isms/vendors/{vendor['id']}",
        {
            "name": "Acme",
            "asset_ids": [str(registers.laptop.id), str(registers.server.id)],
        },
    )
    assert sorted(edited["asset_ids"]) == sorted(
        [str(registers.laptop.id), str(registers.server.id)]
    )
    registers.db.expire_all()
    owners = {
        asset.name: asset.vendor_id
        for asset in registers.db.query(RiskAsset).order_by(RiskAsset.name)
    }
    acme = uuid.UUID(vendor["id"])
    assert owners == {"Laptop": acme, "Phone": None, "Server": acme}


@pytest.mark.parametrize("kind", ["vendor", "person"])
def test_record_details_need_isms_read_whoever_calls_them(registers, kind):
    keen = registers.keen
    path = "/v1/isms/vendors" if kind == "vendor" else "/v1/isms/people"
    record = registers.manage("POST", path, {"name": "Acme"}, status=201)
    record_id = uuid.UUID(record["id"])

    outsider = keen.user()
    with pytest.raises(DomainError) as refused:
        assurance.record_detail(
            db=keen.db, user=outsider, record_id=record_id, kind=kind
        )
    assert (refused.value.status_code, refused.value.detail) == (
        403,
        "ISMS read access required",
    )
    # Risk readers read ISMS records, as the route guard admits them.
    risk_reader = keen.user("risk.read")
    shown = assurance.record_detail(
        db=keen.db, user=risk_reader, record_id=record_id, kind=kind
    )
    assert (shown["id"], shown["name"]) == (record["id"], "Acme")
    response = keen.client(outsider).get(f"{path}/{record_id}")
    assert response.status_code == 403


def test_a_provider_without_an_activity_log_queues_its_refresh_alone(keen, monkeypatch):
    queued = capture_tasks(monkeypatch)
    monkeypatch.setattr(settings, "google_workspace_posture_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", False)
    admin = keen.client(keen.user(role="admin"))
    response = admin.post(
        "/v1/admin/ingest/cloud/google_workspace/run", headers=unsafe(admin)
    )
    assert response.status_code == 202, response.text
    assert response.json()["providers"] == ["google_workspace"]
    assert queued == [
        (
            "cloud_posture_refresh_task",
            {"args": ["google_workspace"], "expires": 300},
        )
    ]


def test_recording_a_cloud_risk_needs_risk_manage_beyond_isms_management(keen, cloud):
    runner.refresh(keen.db, "aws")
    vendor_id = vendor_of(keen)
    path = f"/v1/isms/vendors/{vendor_id}/cloud-posture/risks"
    body = {
        "provider": "aws",
        "scope_key": ACCOUNT,
        "check": "identity.mfa",
        "title": "Console users without MFA",
    }
    # An ISMS manager passes the write gate for ISMS paths; recording a risk
    # still needs risk.manage.
    manager = keen.client(keen.user(*READER, "isms.manage"))
    shown = manager.get(f"/v1/isms/vendors/{vendor_id}/cloud-posture")
    assert shown.status_code == 200 and shown.json()["can_create_risk"] is False
    refused = manager.post(path, json=body, headers=unsafe(manager))
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "risk.manage is required to record a risk",
    )
    keen.db.expire_all()
    assert keen.db.query(Risk).count() == 0
    assert keen.db.query(PostureRiskLink).count() == 0

    owner = keen.client(keen.user(*READER, "risk.manage"))
    recorded = owner.post(path, json=body, headers=unsafe(owner))
    assert recorded.status_code == 201, recorded.text
    assert keen.db.query(IsmsVendor).filter_by(id=vendor_id).count() == 1
    assert keen.db.query(PostureRiskLink).count() == 1
