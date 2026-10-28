"""Person ownership, freshness and provenance through ingestion and assembled policy."""

import uuid
from datetime import datetime, timedelta, timezone

import app.services.agent_enrollment as enrollment_operations
import pytest
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    AgentPostureObservation,
    Event,
    IsmsEffectivenessMeasure,
    IsmsPerson,
    KeenAgent,
    User,
)
from app.security.auth import create_user
from app.services import workstation_posture as posture
from app.services.people import ensure_person_for_user

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_assembled_authority_regressions import unsafe
from tests.test_keen_agent import setup as setup
from tests.ueu_agent import FakeValkey, SimAgent


@pytest.fixture(autouse=True)
def enrolment_limits(monkeypatch):
    monkeypatch.setattr(enrollment_operations, "get_valkey", lambda: None)
    proofs = FakeValkey()
    monkeypatch.setattr(enrollment_operations, "proof_store", lambda: proofs)
    monkeypatch.setattr(
        enrollment_operations, "fixed_window_allow", lambda *a, **k: (True, 0)
    )


def make_posture(
    sim,
    check="antivirus",
    provider="clamav",
    scope="host",
    reason="components_detected",
    when=None,
    subject=None,
    **facts,
):
    if check == "antivirus" and not facts:
        facts = {"installed": True}
    return sim.observation(
        check,
        provider,
        facts,
        scope=scope,
        reason=reason,
        when=when or datetime.now(timezone.utc),
        subject=subject,
    )


def send(sim, *envelopes):
    return sim.send(*envelopes)[0]


def device(client, name, agent_type="workstation", person_id=None):
    body = {"name": name, "agent_type": agent_type}
    if person_id:
        body["person_id"] = person_id
    response = client.post("/v1/admin/agents", json=body)
    assert response.status_code == 201, response.text
    sim = SimAgent(client, platform="windows")
    assert sim.enroll(response.json()["enrollment_code"]).status_code == 201
    return sim


def workstation(setup):
    client, factory, _, _, _, _ = setup
    with factory() as db:
        p = IsmsPerson(name="Test person")
        db.add(p)
        db.commit()
        pid = str(p.id)
    sim = device(client, "laptop", person_id=pid)
    return pid, sim.agent_id, sim


def summary(db, pid):
    return posture.person_posture(
        db=db,
        user=User(id=uuid.uuid4(), role="admin", is_active=True),
        person_id=uuid.UUID(pid),
    )


def test_assignment_validation_and_defaults(setup):
    c, f, _, info, _, _ = setup
    assert info["agent"]["agent_type"] == "server"
    assert info["agent"]["person_id"] is None
    for body in [
        dict(name="a", agent_type="workstation"),
        dict(name="b", agent_type="server", person_id=str(uuid.uuid4())),
        dict(name="c", agent_type="other"),
    ]:
        assert c.post("/v1/admin/agents", json=body).status_code == 422
    assert (
        c.post(
            "/v1/admin/agents",
            json=dict(name="a", agent_type="workstation", person_id=str(uuid.uuid4())),
        ).status_code
        == 400
    )
    with f() as db:
        assert db.query(KeenAgent).count() == 1


def test_two_devices_dedup_and_revocation(setup):
    pid, aid, sim = workstation(setup)
    c, f, _, _, stored, _ = setup
    device(c, "second-laptop", person_id=pid)
    sealed = [sim.seal(make_posture(sim))]
    body = sim.otlp(sealed)
    for _ in range(2):
        assert sim.post("/v1/otlp/logs", body, "otlp-logs").status_code == 200
    with f() as db:
        result = summary(db, pid)
        assert result["total"] == 2
        assert [d["hostname"] for d in result["devices"]] == ["laptop", "second-laptop"]
        assert [d["met"] for d in result["devices"]] == [1, 0]
        assert db.query(Event).count() == db.query(AgentPostureObservation).count() == 1
        assert db.get(KeenAgent, aid).person_id == uuid.UUID(pid)
    assert c.post(f"/v1/admin/agents/{aid}/revoke", json={}).status_code == 200
    with f() as db:
        assert summary(db, pid)["devices"][0]["met"] == 0


def test_out_of_order_reassignment_and_old_replay(setup):
    pid, aid, sim = workstation(setup)
    c, f, _, _, _, _ = setup
    current = make_posture(sim, "disk_encryption", "lsblk", "/", encrypted=False)
    assert send(sim, current).status_code == 200
    earlier = datetime.now(timezone.utc) - timedelta(days=2)
    old = make_posture(
        sim, "disk_encryption", "lsblk", "/", encrypted=True, when=earlier
    )
    assert send(sim, old).status_code == 200
    with f() as db:
        assert summary(db, pid)["devices"][0]["checks"][0]["status"] == "attention"
        p = IsmsPerson(name="Next owner")
        db.add(p)
        db.commit()
        next_id = str(p.id)
    assert (
        c.patch(
            f"/v1/admin/agents/{aid}",
            json=dict(agent_type="workstation", person_id=next_id),
        ).status_code
        == 200
    )
    # A previously unseen delayed report also belongs to the earlier assignment.
    assert send(sim, make_posture(sim, when=earlier)).status_code == 200
    with f() as db:
        assert summary(db, pid)["total"] == 0
        assert summary(db, next_id)["devices"][0]["met"] == 0
        assert db.query(AgentPostureObservation).count() == 0
        assert db.query(Event).count() == 3


def test_qubes_false_values_and_expiry(setup):
    pid, aid, sim = workstation(setup)
    c, f, *_ = setup
    assert (
        send(
            sim,
            make_posture(
                sim, "disk_encryption", "qubes", "host", reason="managed_outside_qube"
            ),
            make_posture(
                sim, "screen_lock", "gnome", "session", automatic_lock_configured=False
            ),
        ).status_code
        == 200
    )
    with f() as db:
        checks = summary(db, pid)["devices"][0]["checks"]
        assert checks[0]["status"] == "external"
        assert checks[1]["status"] == "attention"
        for row in db.query(Event):
            row.timestamp = utc_now_naive() - timedelta(days=2)
        db.commit()
        assert summary(db, pid)["devices"][0]["checks"][1]["status"] == "stale"


def test_snapshot_cap(setup, monkeypatch):
    pid, aid, sim = workstation(setup)
    c, f, *_ = setup
    monkeypatch.setattr(posture, "MAX_STREAMS", 1)
    for scope in ["one", "two"]:
        assert send(sim, make_posture(sim, scope=scope)).status_code == 200
    with f() as db:
        assert db.query(AgentPostureObservation).count() == 1
        device_summary = summary(db, pid)["devices"][0]
        assert device_summary["overflow"] and device_summary["met"] == 0


@pytest.mark.parametrize(
    "check,provider,facts,want",
    [
        (
            "disk_encryption",
            "bitlocker",
            {"fully_encrypted": True, "protection_enabled": False},
            "attention",
        ),
        ("disk_encryption", "filevault", {"enabled": True}, "met"),
        (
            "screen_lock",
            "macos_screensaver",
            {"password_required": True, "idle_seconds": 300},
            "met",
        ),
        ("screen_lock", "windows_user_policy", {"password_required": True}, "unknown"),
        (
            "screen_lock",
            "windows_user_policy",
            {"saver_enabled": True, "password_required": True, "idle_seconds": 600},
            "unknown",
        ),
        ("screen_lock", "windows_user_policy", {"saver_enabled": False}, "unknown"),
        (
            "screen_lock",
            "windows_user_policy",
            {"saver_enabled": False, "automatic_lock_configured": True},
            "met",
        ),
        (
            "screen_lock",
            "macos_screensaver",
            {"password_required": False, "idle_seconds": 300},
            "attention",
        ),
        (
            "firewall",
            "iptables",
            {"input_policy": "ACCEPT", "input_rule_count": 0},
            "attention",
        ),
        ("firewall", "nftables", {"input_drop_policy_chains": 1}, "met"),
        (
            "automatic_updates",
            "apt",
            {"automatic_install_configured": True, "scheduler_state": "inactive"},
            "attention",
        ),
        (
            "automatic_updates",
            "windows_update",
            {"automatic_install_configured": True, "policy_no_auto_update": 1},
            "attention",
        ),
        (
            "automatic_updates",
            "macos_software_update",
            {"automatic_os_install_configured": True},
            "met",
        ),
        (
            "antivirus",
            "microsoft_defender",
            {"healthy": True, "real_time_protection_enabled": False},
            "attention",
        ),
        # Strings are not booleans: a fact sent with the wrong type proves nothing.
        ("disk_encryption", "lsblk", {"encrypted": "true"}, "unknown"),
    ],
)
def test_targets(check, provider, facts, want):
    o = dict(check=check, provider=provider, reason="observed", facts=facts)
    assert posture.evaluate(o)[0] == want


@pytest.mark.parametrize(
    "facts,operator,target,want",
    [
        (
            {"automatic_lock_configured": True, "lock_after_seconds": 1800},
            "lte",
            900,
            "attention",
        ),
        (
            {"automatic_lock_configured": True, "lock_after_seconds": 600},
            "lte",
            900,
            "met",
        ),
        ({"automatic_lock_configured": True}, "lte", 900, "unknown"),
        (
            {"automatic_lock_configured": True, "total_idle_to_lock_seconds": 360},
            "lt",
            600,
            "met",
        ),
        (
            {
                "password_required": True,
                "idle_seconds": 300,
                "password_delay_seconds": 60,
            },
            "lte",
            300,
            "attention",
        ),
    ],
)
def test_screen_lock_targets_come_from_effectiveness_measures(
    facts, operator, target, want
):
    o = dict(check="screen_lock", provider="gnome", reason="observed", facts=facts)
    status, detail = posture.evaluate(o, {"screen_lock": (target, operator, "seconds")})
    assert status == want
    if want != "unknown":
        assert "target" in detail


def test_effectiveness_thresholds_apply_to_people_summaries(setup):
    pid, _, sim = workstation(setup)
    client, factory, *_ = setup
    with factory() as db:
        db.add_all(
            [
                IsmsEffectivenessMeasure(
                    framework_slug="iso27001",
                    summary="Screens lock within 15 minutes",
                    metric_key="keen.workstation.screen_lock_seconds",
                    target_value=900,
                    target_unit="seconds",
                    threshold_operator="lte",
                ),
                IsmsEffectivenessMeasure(
                    framework_slug="iso27001",
                    summary="Signatures at most 3 days old",
                    metric_key="keen.workstation.antivirus_signature_age_days",
                    target_value=3,
                    target_unit="hours",
                    threshold_operator="lte",
                ),
            ]
        )
        db.commit()
    assert (
        send(
            sim,
            make_posture(
                sim,
                "screen_lock",
                "windows_user_policy",
                "user",
                subject=("windows_sid", "S-1-5-21-1-2-3-1001"),
                automatic_lock_configured=True,
                lock_after_seconds=1800,
            ),
            make_posture(
                sim,
                "antivirus",
                "microsoft_defender",
                antivirus_enabled=True,
                signatures_age_days=30,
            ),
        ).status_code
        == 200
    )
    with factory() as db:
        result = summary(db, pid)
        checks = {c["check"]: c for c in result["devices"][0]["checks"]}
        assert checks["screen_lock"]["status"] == "attention"
        assert "1800" in checks["screen_lock"]["observations"][0]["detail"]
        # A target in another unit is not applied.
        assert checks["antivirus"]["status"] == "met"
        assert result["targets"] == {
            "screen_lock": {"operator": "lte", "value": 900, "unit": "seconds"}
        }
        assert result["rules_version"] == posture.RULES_VERSION


def test_coverage_marks_checks_the_device_could_not_collect(setup):
    pid, _, sim = workstation(setup)
    client, factory, *_ = setup
    run = sim.event(
        "evidence.collection.completed",
        "collection",
        {
            "collector": "native_posture",
            "run_id": "r1",
            "checks_attempted": ["firewall", "antivirus"],
            "checks_observed": ["antivirus"],
            "checks_unavailable": ["firewall"],
        },
        outcome="info",
    )
    assert send(sim, make_posture(sim), run).status_code == 200
    with factory() as db:
        checks = {
            c["check"]: c["status"] for c in summary(db, pid)["devices"][0]["checks"]
        }
        assert checks["firewall"] == "not_collected"
        assert checks["antivirus"] == "met"
        assert checks["disk_encryption"] == "unknown"


def test_provisioning_idempotently_creates_person(contract_db):
    u = create_user(
        contract_db,
        username="new-user",
        password="test-only",
        role="normal",
        email="person@example.test",
    )
    p = contract_db.query(IsmsPerson).filter_by(user_id=u.id).one()
    assert (p.name, p.email) == ("new-user", "person@example.test")
    assert ensure_person_for_user(contract_db, u).id == p.id
    assert contract_db.query(IsmsPerson).filter_by(user_id=u.id).count() == 1


def test_assembled_permissions_and_csrf(keen):
    admin = keen.user(role="admin")
    reader = keen.user("isms.read", "events.read")
    p = IsmsPerson(name="Associated person")
    keen.db.add(p)
    keen.db.commit()
    pid = str(p.id)
    path = f"/v1/isms/people/{pid}/workstation-posture"
    assert keen.client().get(path).status_code == 401
    for codes in [("isms.read",), ("events.read",), ()]:
        assert keen.client(keen.user(*codes)).get(path).status_code == 403
    assert keen.client(reader).get(path).status_code == 200
    c = keen.client(admin)
    body = dict(name="test-device", agent_type="workstation", person_id=pid)
    assert c.post("/v1/admin/agents", json=body).status_code == 403
    r = c.post("/v1/admin/agents", json=body, headers=unsafe(c))
    assert r.status_code == 201, r.text
    aid = r.json()["agent"]["id"]
    rc = keen.client(reader)
    assert (
        rc.patch(
            f"/v1/admin/agents/{aid}",
            json=dict(agent_type="server"),
            headers=unsafe(rc),
        ).status_code
        == 403
    )
    assert (
        c.patch(f"/v1/admin/agents/{aid}", json=dict(agent_type="server")).status_code
        == 403
    )
    assert (
        c.patch(
            f"/v1/admin/agents/{aid}", json=dict(agent_type="server"), headers=unsafe(c)
        ).status_code
        == 200
    )
    assert c.get(path + "?offset=-1").status_code == 400
    assert (
        c.get(
            "/v1/isms/people/" + str(uuid.uuid4()) + "/workstation-posture"
        ).status_code
        == 404
    )


def test_assembled_permissions_for_evidence_bundles_and_policy(keen):
    admin = keen.user(role="admin")
    reader = keen.user("events.read")
    c = keen.client(admin)
    r = c.post(
        "/v1/admin/agents",
        json=dict(name="bundle-host", agent_type="server"),
        headers=unsafe(c),
    )
    assert r.status_code == 201, r.text
    aid = r.json()["agent"]["id"]
    bundle = f"/v1/admin/agents/{aid}/evidence-bundle"
    window = {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}
    policy = f"/v1/admin/agents/{aid}/evidence"
    event_bundle = f"/v1/events/{uuid.uuid4()}/ueu-bundle"
    anonymous = keen.client()
    assert anonymous.get(bundle, params=window).status_code == 401
    assert anonymous.get(event_bundle).status_code == 401
    assert anonymous.patch(policy, json={}).status_code in (401, 403)
    rc = keen.client(reader)
    assert rc.get(bundle, params=window).status_code == 403
    assert (
        rc.patch(
            policy, json={"evidence_mode_ceiling": "all_lines"}, headers=unsafe(rc)
        ).status_code
        == 403
    )
    assert rc.get(event_bundle).status_code == 404
    assert keen.client(keen.user()).get(event_bundle).status_code == 403
    assert c.get(bundle, params=window).status_code == 200
    body = {"evidence_mode_ceiling": "all_lines"}
    assert c.patch(policy, json=body).status_code == 403
    r = c.patch(policy, json=body, headers=unsafe(c))
    assert r.status_code == 200 and r.json()["agent"]["evidence_mode_ceiling"] == (
        "all_lines"
    )


def test_expiry_retention_and_ordinary_server_evidence(setup):
    pid, aid, sim = workstation(setup)
    client, factory, *_ = setup
    server = device(client, "web-01", agent_type="server")
    assert send(server, make_posture(server)).status_code == 200
    with factory() as db:
        assert db.query(AgentPostureObservation).count() == 0
    assert send(sim, make_posture(sim)).status_code == 200
    with factory() as db:
        assert summary(db, pid)["devices"][0]["met"] == 1
        agent = db.get(KeenAgent, aid)
        agent.expires_at = utc_now_naive() - timedelta(seconds=1)
        db.commit()
        assert summary(db, pid)["devices"][0]["checks"][2]["status"] == "stale"
    assert send(sim, make_posture(sim)).status_code == 401
    with factory() as db:
        # Even where a test DB lacks FK cascades, a removed event cannot survive
        # in the summary: facts are read from the retained Event, not a copy.
        from app.db.models import UeuEvidence

        db.query(UeuEvidence).delete()
        db.query(Event).delete()
        db.commit()
        assert summary(db, pid)["devices"][0]["checks"][2]["observations"] == []


def test_scopes_and_supporting_component_metadata(setup):
    pid, _, sim = workstation(setup)
    client, factory, *_ = setup
    assert (
        send(
            sim,
            make_posture(sim, "disk_encryption", "lsblk", "/", encrypted=True),
            make_posture(sim, "disk_encryption", "lsblk", "/home", encrypted=False),
            make_posture(sim),
            make_posture(
                sim,
                "antivirus",
                "clamav_signatures",
                "database",
                reason="version_unavailable",
                signatures_updated="2026-10-26T16:57:49Z",
            ),
        ).status_code
        == 200
    )
    with factory() as db:
        checks = summary(db, pid)["devices"][0]["checks"]
        assert checks[0]["status"] == "attention"
        assert checks[2]["status"] == "met"
        assert len(checks[0]["observations"]) == len(checks[2]["observations"]) == 2


def test_failed_batch_does_not_publish_partial_posture(setup, monkeypatch):
    from app.ingest import common

    pid, _, sim = workstation(setup)
    client, factory, *_ = setup
    original = common.put_bytes
    calls = 0

    def fail_second(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic storage failure")
        return original(**kwargs)

    monkeypatch.setattr(common, "put_bytes", fail_second)
    sealed = [
        sim.seal(make_posture(sim)),
        sim.seal(make_posture(sim, "disk_encryption", "lsblk", "/", encrypted=True)),
    ]
    body = sim.otlp(sealed)
    assert sim.post("/v1/otlp/logs", body, "otlp-logs").status_code == 500
    with factory() as db:
        assert db.query(Event).count() == db.query(AgentPostureObservation).count() == 0
        assert db.get(KeenAgent, sim.agent_id).ueu_last_sequence is None
        assert summary(db, pid)["devices"][0]["met"] == 0
    monkeypatch.setattr(common, "put_bytes", original)
    assert sim.post("/v1/otlp/logs", body, "otlp-logs").status_code == 200
    with factory() as db:
        assert summary(db, pid)["devices"][0]["met"] == 2


def test_other_catalogues_and_invalid_envelopes_never_reach_posture(setup):
    pid, _, sim = workstation(setup)
    client, factory, *_ = setup
    foreign = make_posture(sim)
    foreign["catalogue"] = {"id": "acme", "version": "1"}
    assert send(sim, foreign).status_code == 200
    broken = make_posture(sim, "screen_lock", "gnome", automatic_lock_configured="yes")
    assert send(sim, broken).status_code == 400
    with factory() as db:
        assert db.query(Event).count() == 1
        assert db.query(AgentPostureObservation).count() == 0
        assert summary(db, pid)["devices"][0]["met"] == 0


def test_account_does_not_claim_an_unlinked_matching_person(contract_db):
    existing = IsmsPerson(name="same-name", email="same@example.test")
    contract_db.add(existing)
    contract_db.commit()
    account = create_user(
        contract_db,
        username="same-name",
        password="test-only",
        role="normal",
        email="same@example.test",
    )
    linked = ensure_person_for_user(contract_db, account)
    assert linked.id != existing.id
    assert existing.user_id is None


@pytest.mark.parametrize("feature", ["people", "evidence", "isms"])
def test_disabled_features_hide_posture_even_from_admin(keen, feature):
    admin = keen.user(role="admin")
    client = keen.client(admin)
    person = IsmsPerson(name="Person")
    keen.db.add(person)
    keen.db.commit()
    pid = str(person.id)
    assert (
        client.put(
            "/v1/admin/features",
            json={"version": 0, "changes": {feature: False}},
            headers=unsafe(client),
        ).status_code
        == 200
    )
    assert client.get(f"/v1/isms/people/{pid}/workstation-posture").status_code == 404
    if feature != "evidence":
        response = client.post(
            "/v1/admin/agents",
            json=dict(name="laptop", agent_type="workstation", person_id=pid),
            headers=unsafe(client),
        )
        assert response.status_code == 404


def test_device_pagination_and_person_isolation(setup):
    pid, _, _ = workstation(setup)
    client, factory, *_ = setup
    for i in range(21):
        assert (
            client.post(
                "/v1/admin/agents",
                json=dict(
                    name=f"device-{i:02}", agent_type="workstation", person_id=pid
                ),
            ).status_code
            == 201
        )
    with factory() as db:
        first = summary(db, pid)
        second = posture.person_posture(
            db=db,
            user=User(id=uuid.uuid4(), role="admin", is_active=True),
            person_id=uuid.UUID(pid),
            offset=20,
        )
        assert first["total"] == 22
        assert len(first["devices"]) == 20 and first["next_offset"] == 20
        assert len(second["devices"]) == 2 and second["next_offset"] is None
        assert not (
            {d["agent_id"] for d in first["devices"]}
            & {d["agent_id"] for d in second["devices"]}
        )
        other = IsmsPerson(name="Other person")
        db.add(other)
        db.commit()
        assert summary(db, str(other.id))["total"] == 0


def test_postgres_backfill_preserves_links_and_person_deletion(
    contract_db, monkeypatch
):
    if contract_db.get_bind().dialect.name != "postgresql":
        pytest.skip("Migration and FK semantics require disposable PostgreSQL")
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/0023_workstation_people.py"
    )
    spec = importlib.util.spec_from_file_location("posture_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(
        migration,
        "op",
        Operations(MigrationContext.configure(contract_db.connection())),
    )
    migration.downgrade()
    users = [
        User(username="existing-linked", password_hash="test", role="normal"),
        User(
            username="existing-unlinked",
            password_hash="test",
            role="normal",
            email="existing@example.test",
        ),
    ]
    contract_db.add_all(users)
    contract_db.flush()
    person = IsmsPerson(name="Preserved custom name", user_id=users[0].id)
    contract_db.add(person)
    contract_db.flush()
    old_id = str(uuid.uuid4())
    contract_db.execute(
        text(
            "INSERT INTO keen_agents (id,name,token_hash,enabled,created_at,expires_at,health,enrollment_labels) VALUES (:id,'old-server','test',true,now(),now() + interval '1 day','{}','{}')"
        ),
        {"id": old_id},
    )
    migration.upgrade()
    contract_db.expire_all()
    assert contract_db.get(KeenAgent, old_id).agent_type == "server"
    assert (
        contract_db.query(IsmsPerson).filter_by(user_id=users[0].id).one().name
        == "Preserved custom name"
    )
    assert (
        contract_db.query(IsmsPerson).filter_by(user_id=users[1].id).one().email
        == "existing@example.test"
    )
    agent = KeenAgent(
        id=str(uuid.uuid4()),
        name="workstation",
        token_hash="test",
        expires_at=utc_now_naive() + timedelta(days=1),
        agent_type="workstation",
        person_id=person.id,
    )
    contract_db.add(agent)
    contract_db.flush()
    contract_db.delete(person)
    contract_db.flush()
    contract_db.expire(agent)
    assert agent.person_id is None
    assert contract_db.get(User, users[0].id) is not None


def test_measured_lock_facts_survive_ingestion(setup):
    """Typed facts named like secrets reach evaluation through redaction."""
    pid, aid, sim = workstation(setup)
    c, f, *_ = setup
    assert (
        send(
            sim,
            make_posture(
                sim,
                "screen_lock",
                "macos_screensaver",
                "user",
                subject=("unix_uid", "501"),
                password_required=True,
                idle_seconds=300,
            ),
            make_posture(
                sim,
                "screen_lock",
                "macos_screensaver",
                "user",
                subject=("unix_uid", "502"),
                password_required=True,
                password_delay_seconds=0,
            ),
        ).status_code
        == 200
    )
    with f() as db:
        lock = summary(db, pid)["devices"][0]["checks"][1]
        assert sorted(o["status"] for o in lock["observations"]) == ["met", "unknown"]
        assert all(o["scope"].startswith("user · ") for o in lock["observations"])
        facts = [
            e.normalized_payload["ueu"]["observation"]["facts"] for e in db.query(Event)
        ]
        assert {x["password_required"] for x in facts} == {True}


def test_devices_report_the_platform_their_key_sends(setup):
    pid, aid, sim = workstation(setup)
    client, factory, *_ = setup
    with factory() as db:
        # Before any evidence the platform is not known.
        assert summary(db, pid)["devices"][0]["platform"] is None
    assert send(sim, make_posture(sim)).status_code == 200
    listed = client.get("/v1/admin/agents").json()["agents"]
    assert next(a for a in listed if a["id"] == aid)["health"]["platform"] == "windows"
    with factory() as db:
        assert db.get(KeenAgent, aid).health["platform"] == "windows"
        assert summary(db, pid)["devices"][0]["platform"] == "windows"
        # Older agents' evidence still shows the platform before the next batch.
        row = db.get(KeenAgent, aid)
        row.health = {k: v for k, v in row.health.items() if k != "platform"}
        db.commit()
        assert summary(db, pid)["devices"][0]["platform"] == "windows"
