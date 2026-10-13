"""Authorisation matrix through the real deployment, using synthetic accounts.

Each row names the roles that must be allowed; every other role must be denied.
The permission model follows SECURITY.md: risk.read implies PESTLE, interested
party and ISMS read access, and risk.manage implies their management.
"""

from __future__ import annotations

import pytest
from conftest import ROLES

EVENTS = {"events_reader", "questioner"}
RISK = {"risk_reader", "risk_manager"}
ISMS = {"isms_reader", "isms_manager"} | RISK
PESTLE = {"pestle_reader"} | RISK
PARTIES = {"parties_reader"} | RISK
AUDITS = {"audit_reader", "audit_manager"}

READS = [
    ("/api/v1/events", EVENTS),
    ("/api/v1/events/facets", EVENTS),
    ("/api/v1/sources", EVENTS),
    ("/api/v1/sources/meta", EVENTS),
    ("/api/v1/charts/event_volume_timeseries", EVENTS),
    ("/api/v1/graph/control_source", EVENTS),
    ("/api/v1/graph/clause_source", EVENTS),
    ("/api/v1/graph/risk_control", RISK),
    ("/api/v1/risks", RISK),
    ("/api/v1/risks/register", RISK),
    ("/api/v1/risks/register/export.csv", RISK),
    ("/api/v1/risks/{risk}", RISK),
    ("/api/v1/risks/{risk}/changelog", RISK),
    ("/api/v1/risks/users", {"risk_manager"}),
    ("/api/v1/pestle/items", PESTLE),
    ("/api/v1/pestle/items/{pestle}", PESTLE),
    ("/api/v1/interested-parties", PARTIES),
    ("/api/v1/interested-parties/{party}", PARTIES),
    ("/api/v1/isms/objectives", ISMS),
    ("/api/v1/isms/objectives/{objective}", ISMS),
    ("/api/v1/isms/documents/{document}", ISMS),
    ("/api/v1/isms/meetings/{meeting}", ISMS),
    ("/api/v1/isms/people", ISMS),
    ("/api/v1/isms/org-nodes", ISMS),
    ("/api/v1/audits", AUDITS),
    ("/api/v1/audits/{audit}", AUDITS),
    ("/api/v1/audits/users", {"audit_manager"}),
    ("/api/v1/users", set()),
    ("/api/v1/admin/groups", set()),
    ("/api/v1/admin/permissions", set()),
    ("/api/v1/admin/integrations", set()),
    ("/api/v1/admin/source-connections", set()),
    ("/api/v1/admin/agents", set()),
    ("/api/v1/admin/agent-enrollment-profiles", set()),
    ("/api/v1/admin/managed-configurations", set()),
    ("/api/v1/admin/evidence-retention", set()),
    ("/api/v1/admin/audit", {"audit_trail_reader"}),
    ("/api/v1/admin/questions", set()),
]


def _fill(path, seed):
    for key, value in seed.ids.items():
        path = path.replace("{" + key + "}", value)
    return path


@pytest.mark.parametrize("path,allowed", READS, ids=[r[0] for r in READS])
def test_read_matrix(accounts, seed, path, allowed):
    concrete = _fill(path, seed)
    wrong = []
    for role in ROLES:
        status = accounts(role).get(concrete).status_code
        if role in allowed and status != 200:
            wrong.append((role, "expected 200", status))
        if role not in allowed and status not in (403, 404):
            wrong.append((role, "expected denial", status))
    assert not wrong, (path, wrong)


def test_event_aggregates_withhold_evidence_without_events_read(accounts):
    plain = accounts("plain")
    summary = plain.ok(plain.get("/api/v1/stats/summary"), 200).json()
    assert summary["events"] is None
    items = plain.ok(plain.get("/api/v1/stats/controls"), 200).json()["items"]
    assert all(i["evidence_count"] is None for i in items)


WRITES = [
    # (label, method, path, body, roles allowed, verify path, field that must not change)
    (
        "risk",
        "PATCH",
        "/api/v1/risks/{risk}",
        {"threat_summary": "e2e-changed"},
        {"risk_manager"},
    ),
    (
        "pestle",
        "PATCH",
        "/api/v1/pestle/items/{pestle}",
        {"item": "e2e-changed"},
        {"risk_manager"},
    ),
    (
        "party",
        "PATCH",
        "/api/v1/interested-parties/{party}",
        {"note": "e2e-changed"},
        {"risk_manager"},
    ),
    (
        "objective",
        "PATCH",
        "/api/v1/isms/objectives/{objective}",
        {"goal": "e2e-changed"},
        {"isms_manager", "risk_manager"},
    ),
    ("audit", "PATCH", "/api/v1/audits/{audit}", {"title": "e2e-changed"}, set()),
]


@pytest.mark.parametrize(
    "label,method,path,body,allowed", WRITES, ids=[w[0] for w in WRITES]
)
def test_write_matrix_denials(
    accounts, admin, seed, label, method, path, body, allowed
):
    concrete = _fill(path, seed)
    before = admin.ok(admin.get(concrete), 200).json()
    wrong = []
    for role in ROLES:
        if role in allowed:
            continue
        status = accounts(role).request(method, concrete, json=body).status_code
        if status not in (403, 404):
            wrong.append((role, status))
    after = admin.ok(admin.get(concrete), 200).json()
    assert not wrong, (label, wrong)
    assert before == after, f"{label} changed after denied writes"


def test_accounts_cannot_grant_themselves_authority(accounts, admin):
    for role in ("plain", "isms_manager", "audit_manager"):
        session = accounts(role)
        uid = session.user_id
        attempts = [
            session.put(
                f"/api/v1/users/{uid}/access",
                json={"explicit_permission_codes": ["risk.manage"]},
            ),
            session.patch(f"/api/v1/users/{uid}", json={"role": "admin"}),
            session.post(
                "/api/v1/admin/groups", json={"name": "e2e-escalate", "role": "admin"}
            ),
            session.post(
                "/api/v1/admin/permissions", json={"code": "e2e.x", "description": "x"}
            ),
        ]
        assert all(a.status_code in (403, 404) for a in attempts), (
            role,
            [a.status_code for a in attempts],
        )
        access = admin.ok(admin.get(f"/api/v1/users/{uid}/access"), 200).json()
        assert "risk.manage" not in access["explicit_permission_codes"]


def test_isms_manager_cannot_join_a_risk_owning_role(accounts, admin, seed):
    session = accounts("isms_manager")
    node = seed.ids["org_role"]
    response = session.patch(
        f"/api/v1/isms/org-nodes/{node}", json={"user_ids": [session.user_id]}
    )
    assert response.status_code == 403, response.status_code
    assert session.get(f"/api/v1/risks/{seed.ids['risk']}").status_code == 403


def test_audit_managers_cannot_edit_or_delete_other_audits(accounts, seed):
    session = accounts("audit_manager")
    audit = seed.ids["audit"]
    assert session.delete(f"/api/v1/audits/{audit}").status_code in (403, 404)
    assert session.post(
        f"/api/v1/audits/{audit}/attendees",
        json={"name": "e2e", "email": "e2e@example.org"},
    ).status_code in (403, 404)


def test_questions_need_permission_and_visibility(accounts, seed, evidence_writes):
    if not evidence_writes:
        pytest.skip(
            "Needs KEEN_E2E_ALLOW_EVIDENCE_WRITES=1 for a synthetic diary event"
        )
    event = seed.ids["event"]
    reader = accounts("events_reader")
    response = reader.post(f"/api/v1/events/{event}/questions", json={"body": "e2e"})
    assert response.status_code == 403
    plain = accounts("plain")
    assert plain.get(f"/api/v1/events/{event}/questions").status_code == 403
