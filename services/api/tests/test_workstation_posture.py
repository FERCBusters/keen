"""Person ownership, freshness and provenance through ingestion and assembled policy."""

import uuid
from datetime import timedelta, timezone

import pytest
from app.core.datetime_utils import utc_now_naive
from app.db.models import AgentPostureObservation, Event, IsmsPerson, KeenAgent, User
from app.security.auth import create_user
from app.services import workstation_posture as posture
from app.services.people import ensure_person_for_user

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_assembled_authority_regressions import unsafe
from tests.test_keen_agent import logs, record
from tests.test_keen_agent import setup as setup


def make_posture(check="antivirus", provider="clamav", scope="host", **facts):
    doc = record()
    log = logs(doc)[0]
    log["timeUnixNano"] = str(
        int(utc_now_naive().replace(tzinfo=timezone.utc).timestamp() * 10**9)
    )
    log["attributes"] = [a for a in log["attributes"] if a["key"] != "keen.action"]
    log["attributes"].append(
        {"key": "keen.action", "value": {"stringValue": "posture." + check}}
    )
    fields = {
        "schema_version": "1",
        "check": check,
        "provider": provider,
        "scope": scope,
        "status": "observed",
        "reason": "components_detected",
        **facts,
    }
    for k, v in fields.items():
        log["attributes"].append(
            {"key": "keen.field.posture." + k, "value": {"stringValue": v}}
        )
    return doc


def workstation(setup):
    client, factory, _, _, _, _ = setup
    with factory() as db:
        p = IsmsPerson(name="Test person")
        db.add(p)
        db.commit()
        pid = str(p.id)
    response = client.post(
        "/v1/admin/agents",
        json={"name": "laptop", "agent_type": "workstation", "person_id": pid},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return pid, body["agent"]["id"], {"Authorization": "Bearer " + body["token"]}


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


def test_two_devices_dedup_revocation_and_spoofed_owner(setup):
    pid, aid, h = workstation(setup)
    c, f, _, _, stored, _ = setup
    assert (
        c.post(
            "/v1/admin/agents",
            json=dict(name="second-laptop", agent_type="workstation", person_id=pid),
        ).status_code
        == 201
    )
    doc = make_posture(
        person_id=str(uuid.uuid4()), agent_id="forged", hostname="forged-host"
    )
    for _ in range(2):
        assert c.post("/v1/otlp/logs", json=doc, headers=h).status_code == 200
    with f() as db:
        result = summary(db, pid)
        assert result["total"] == 2
        assert [d["hostname"] for d in result["devices"]] == ["laptop", "second-laptop"]
        assert [d["met"] for d in result["devices"]] == [1, 0]
        assert db.query(Event).count() == db.query(AgentPostureObservation).count() == 1
        assert db.get(KeenAgent, aid).person_id == uuid.UUID(pid)
    assert len(stored) == 1
    assert c.post(f"/v1/admin/agents/{aid}/revoke", json={}).status_code == 200
    with f() as db:
        assert summary(db, pid)["devices"][0]["met"] == 0


def test_out_of_order_reassignment_and_old_replay(setup):
    pid, aid, h = workstation(setup)
    c, f, _, _, _, _ = setup
    current = make_posture("disk_encryption", "lsblk", "/", encrypted="false")
    assert c.post("/v1/otlp/logs", json=current, headers=h).status_code == 200
    old = make_posture("disk_encryption", "lsblk", "/", encrypted="true")
    logs(old)[0]["timeUnixNano"] = str(
        int(
            (utc_now_naive() - timedelta(days=2))
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        * 10**9
    )
    assert c.post("/v1/otlp/logs", json=old, headers=h).status_code == 200
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
    assert c.post("/v1/otlp/logs", json=current, headers=h).status_code == 200
    # A previously unseen delayed report also belongs to the earlier assignment.
    delayed = make_posture()
    logs(delayed)[0]["timeUnixNano"] = logs(old)[0]["timeUnixNano"]
    assert c.post("/v1/otlp/logs", json=delayed, headers=h).status_code == 200
    with f() as db:
        assert summary(db, pid)["total"] == 0
        assert summary(db, next_id)["devices"][0]["met"] == 0
        assert db.query(AgentPostureObservation).count() == 0
        assert db.query(Event).count() == 3


def test_qubes_false_values_and_expiry(setup):
    pid, aid, h = workstation(setup)
    c, f, *_ = setup
    for doc in [
        make_posture("disk_encryption", "qubes", "host", reason="managed_outside_qube"),
        make_posture(
            "screen_lock", "gnome", "session", automatic_lock_configured="false"
        ),
    ]:
        assert c.post("/v1/otlp/logs", json=doc, headers=h).status_code == 200
    with f() as db:
        checks = summary(db, pid)["devices"][0]["checks"]
        assert checks[0]["status"] == "external"
        assert checks[1]["status"] == "attention"
        for row in db.query(Event):
            row.timestamp = utc_now_naive() - timedelta(days=2)
        db.commit()
        assert summary(db, pid)["devices"][0]["checks"][1]["status"] == "stale"


def test_snapshot_cap(setup, monkeypatch):
    pid, aid, h = workstation(setup)
    c, f, *_ = setup
    monkeypatch.setattr(posture, "MAX_STREAMS", 1)
    for scope in ["one", "two"]:
        assert (
            c.post(
                "/v1/otlp/logs", json=make_posture(scope=scope), headers=h
            ).status_code
            == 200
        )
    with f() as db:
        assert db.query(AgentPostureObservation).count() == 1
        device = summary(db, pid)["devices"][0]
        assert device["overflow"] and device["met"] == 0


@pytest.mark.parametrize(
    "check,provider,facts,want",
    [
        (
            "disk_encryption",
            "bitlocker",
            {"fully_encrypted": "true", "protection_enabled": "false"},
            "attention",
        ),
        ("disk_encryption", "filevault", {"enabled": "true"}, "met"),
        (
            "screen_lock",
            "macos_screensaver",
            {"password_required": "true", "idle_seconds": "300"},
            "met",
        ),
        (
            "screen_lock",
            "windows_user_policy",
            {"password_required": "true"},
            "unknown",
        ),
        (
            "firewall",
            "iptables",
            {"input_policy": "ACCEPT", "input_rule_count": "0"},
            "attention",
        ),
        ("firewall", "nftables", {"input_drop_policy_chains": "1"}, "met"),
        (
            "automatic_updates",
            "apt",
            {"automatic_install_configured": "true", "scheduler_state": "inactive"},
            "attention",
        ),
        (
            "automatic_updates",
            "macos_software_update",
            {"automatic_os_install_configured": "true"},
            "met",
        ),
        (
            "antivirus",
            "microsoft_defender",
            {"healthy": "true", "real_time_protection_enabled": "false"},
            "attention",
        ),
    ],
)
def test_targets(check, provider, facts, want):
    assert (
        posture.evaluate(
            dict(check=check, provider=provider, status="observed", **facts)
        )
        == want
    )


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


def test_expiry_retention_and_ordinary_server_evidence(setup):
    pid, aid, headers = workstation(setup)
    client, factory, server_headers, *_ = setup
    assert (
        client.post(
            "/v1/otlp/logs", json=make_posture(), headers=server_headers
        ).status_code
        == 200
    )
    with factory() as db:
        assert db.query(AgentPostureObservation).count() == 0
    assert (
        client.post("/v1/otlp/logs", json=make_posture(), headers=headers).status_code
        == 200
    )
    with factory() as db:
        assert summary(db, pid)["devices"][0]["met"] == 1
        agent = db.get(KeenAgent, aid)
        agent.expires_at = utc_now_naive() - timedelta(seconds=1)
        db.commit()
        assert summary(db, pid)["devices"][0]["checks"][2]["status"] == "stale"
    assert (
        client.post("/v1/otlp/logs", json=make_posture(), headers=headers).status_code
        == 401
    )
    with factory() as db:
        # Even where a test DB lacks FK cascades, a removed event cannot survive
        # in the summary: facts are read from the retained Event, not a copy.
        db.query(Event).delete()
        db.commit()
        assert summary(db, pid)["devices"][0]["checks"][2]["observations"] == []


def test_scopes_and_supporting_component_metadata(setup):
    pid, _, headers = workstation(setup)
    client, factory, *_ = setup
    docs = [
        make_posture("disk_encryption", "lsblk", "/", encrypted="true"),
        make_posture("disk_encryption", "lsblk", "/home", encrypted="false"),
        make_posture(),
        make_posture(
            "antivirus",
            "clamav_signatures",
            "database",
            status="unknown",
            reason="version_unavailable",
        ),
    ]
    for doc in docs:
        assert (
            client.post("/v1/otlp/logs", json=doc, headers=headers).status_code == 200
        )
    with factory() as db:
        checks = summary(db, pid)["devices"][0]["checks"]
        assert checks[0]["status"] == "attention"
        assert checks[2]["status"] == "met"
        assert len(checks[0]["observations"]) == len(checks[2]["observations"]) == 2


def test_failed_batch_does_not_publish_partial_posture(setup, monkeypatch):
    from app.ingest import common

    pid, _, headers = workstation(setup)
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
    doc = make_posture()
    logs(doc).extend(
        logs(make_posture("disk_encryption", "lsblk", "/", encrypted="true"))
    )
    assert client.post("/v1/otlp/logs", json=doc, headers=headers).status_code == 500
    with factory() as db:
        assert db.query(Event).count() == db.query(AgentPostureObservation).count() == 0
        assert summary(db, pid)["devices"][0]["met"] == 0
    monkeypatch.setattr(common, "put_bytes", original)
    assert client.post("/v1/otlp/logs", json=doc, headers=headers).status_code == 200
    with factory() as db:
        assert summary(db, pid)["devices"][0]["met"] == 2


def test_malformed_posture_stays_raw_evidence(setup):
    pid, _, headers = workstation(setup)
    client, factory, *_ = setup
    for facts in [dict(schema_version="2"), dict(provider="<script>"), dict(scope="")]:
        assert (
            client.post(
                "/v1/otlp/logs", json=make_posture(**facts), headers=headers
            ).status_code
            == 200
        )
    with factory() as db:
        assert db.query(Event).count() == 3
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
