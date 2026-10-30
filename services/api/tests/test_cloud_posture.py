"""Cloud posture runs, the Vendor page and recorded risks, through the
assembled application against PostgreSQL."""

from __future__ import annotations

import dataclasses
import logging
import uuid
from datetime import datetime, timedelta

import pytest
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    CloudPostureScope,
    CloudProvider,
    Event,
    IngestionCursor,
    IsmsPerson,
    IsmsVendor,
    PostureRiskLink,
    Risk,
    RiskAsset,
    SourceIngestionState,
)
from app.ingest.cloud import KEYS, aws, providers, runner
from app.ingest.cloud.base import Activity, Check, Identity, ScopeResult, Unavailable

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_assembled_authority_regressions import unsafe

ACCOUNT = "111122223333"
READER = ("isms.read", "events.read")


def scope(mfa="attention", detail="MFA off for 1 of 2 console users", key=ACCOUNT):
    return ScopeResult(
        key,
        "Production",
        [
            Check("identity.mfa", "MFA for console users", mfa, detail, ["bob"]),
            Check(
                "aws.root", "Root user", "met", "Root user has MFA and no access keys"
            ),
            Check(
                "findings",
                "Security Hub findings",
                "unknown",
                "",
                reason="not_configured",
            ),
            Check("made.up", "Not a check of this provider", "met", "ignored"),
        ],
        [
            Identity(
                "bob", "Bob@Example.org", mfa=False, last_used=datetime(2026, 9, 1)
            ),
            Identity("root", kind="root", admin=True, mfa=True),
        ],
    )


@pytest.fixture
def cloud(keen, monkeypatch):
    """AWS turned on with a provider that answers from ``state``."""
    state = {"results": [scope()], "activity": ([], True), "calls": 0}

    def collect():
        state["calls"] += 1
        if isinstance(state["results"], Exception):
            raise state["results"]
        return state["results"]

    def activity(since, until):
        state.setdefault("windows", []).append((since, until))
        if isinstance(state["activity"], Exception):
            raise state["activity"]
        return state["activity"]

    monkeypatch.setattr(
        aws,
        "PROVIDER",
        dataclasses.replace(aws.PROVIDER, collect=collect, activity=activity),
    )
    monkeypatch.setattr(settings, "aws_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", False)
    return state


def posture_events(db):
    return (
        db.query(Event)
        .filter(Event.source == "aws", Event.action.like("posture.%"))
        .order_by(Event.timestamp, Event.action)
        .all()
    )


# --- refresh ----------------------------------------------------------------


def test_refresh_keeps_the_latest_result_and_records_changes(keen, cloud, monkeypatch):
    db = keen.db
    assert runner.refresh(db, "aws") == {"provider": "aws", "scopes": 1}
    row = db.get(CloudProvider, "aws")
    vendor = db.get(IsmsVendor, row.vendor_id)
    assert vendor.name == "Amazon Web Services"
    assert vendor.website == "https://aws.amazon.com"
    assert row.vendor_id == runner.vendor_id_for(aws.PROVIDER)
    stored = db.query(CloudPostureScope).one()
    assert stored.label == "Production"
    checks = {c["key"]: c for c in stored.checks}
    # Checks the provider does not define are dropped.
    assert set(checks) == {"identity.mfa", "aws.root", "findings"}
    assert checks["findings"]["event_id"] is None
    assert checks["identity.mfa"]["subjects"] == ["bob"]
    assert stored.identities[0]["email"] == "Bob@Example.org"
    events = posture_events(db)
    assert [e.action for e in events] == ["posture.aws.root", "posture.identity.mfa"]
    mfa = events[1]
    assert (mfa.outcome, mfa.system, mfa.connection_id) == (
        "attention",
        f"aws:{ACCOUNT}",
        "env:aws",
    )
    assert mfa.summary == (
        "MFA for console users · Production: MFA off for 1 of 2 console users"
    )
    assert mfa.normalized_payload["subjects"] == ["bob"]
    assert str(mfa.id) == checks["identity.mfa"]["event_id"]

    # The same result on the same day is not recorded again.
    runner.refresh(db, "aws")
    assert len(posture_events(db)) == 2
    # A changed detail is.
    cloud["results"] = [scope(detail="MFA off for 2 of 2 console users")]
    runner.refresh(db, "aws")
    assert len(posture_events(db)) == 3
    # Each judged result is restated once a day.
    tomorrow = utc_now_naive() + timedelta(days=1)
    monkeypatch.setattr(runner, "utc_now_naive", lambda: tomorrow)
    runner.refresh(db, "aws")
    assert len(posture_events(db)) == 5


def test_scopes_no_longer_returned_are_removed_and_old_ones_expire(keen, cloud):
    db = keen.db
    cloud["results"] = [scope(), scope(key="444455556666")]
    runner.refresh(db, "aws")
    assert db.query(CloudPostureScope).count() == 2
    cloud["results"] = [scope()]
    runner.refresh(db, "aws")
    assert [s.scope_key for s in db.query(CloudPostureScope).all()] == [ACCOUNT]
    assert runner.purge_stale(db, utc_now_naive() + timedelta(days=29)) == 0
    assert runner.purge_stale(db, utc_now_naive() + timedelta(days=31)) == 1
    assert db.query(CloudPostureScope).count() == 0


def test_vendors_are_matched_by_name_and_kept_when_renamed(keen, cloud):
    db = keen.db
    existing = IsmsVendor(name="aws")
    db.add(existing)
    db.commit()
    runner.refresh(db, "aws")
    assert db.get(CloudProvider, "aws").vendor_id == existing.id
    existing.name = "Our cloud supplier"
    db.commit()
    runner.refresh(db, "aws")
    assert db.get(CloudProvider, "aws").vendor_id == existing.id
    # A deleted Vendor is replaced by one with the vendor's own name.
    db.delete(existing)
    db.commit()
    db.expire_all()
    assert db.get(CloudProvider, "aws").vendor_id is None
    runner.refresh(db, "aws")
    replacement = db.get(IsmsVendor, db.get(CloudProvider, "aws").vendor_id)
    assert replacement.name == "Amazon Web Services"


def test_refresh_skips_disabled_and_paused_providers(keen, cloud, monkeypatch):
    db = keen.db
    monkeypatch.setattr(settings, "aws_enabled", False)
    assert runner.refresh(db, "aws")["reason"] == "disabled"
    monkeypatch.setattr(settings, "aws_enabled", True)
    db.add(SourceIngestionState(source="aws", paused=True))
    db.commit()
    assert runner.refresh(db, "aws")["reason"] == "paused"
    assert runner.collect_activity(db, "aws")["reason"] == "paused"
    assert cloud["calls"] == 0


def test_failed_reads_record_a_reason_and_keep_the_last_result(keen, cloud, caplog):
    db = keen.db
    runner.refresh(db, "aws")
    cloud["results"] = Unavailable("permission_denied")
    assert runner.refresh(db, "aws") == {
        "provider": "aws",
        "error": "permission_denied",
    }
    row = db.get(CloudProvider, "aws")
    assert row.error == "permission_denied"
    assert db.query(CloudPostureScope).count() == 1
    cloud["results"] = RuntimeError("token=AKIASECRET response body")
    with caplog.at_level(logging.WARNING):
        assert runner.refresh(db, "aws")["error"] == "unavailable"
    assert "AKIASECRET" not in caplog.text and "RuntimeError" in caplog.text
    cloud["results"] = [scope()]
    runner.refresh(db, "aws")
    db.expire_all()
    assert db.get(CloudProvider, "aws").error == ""


def test_due_refreshes_follow_the_interval(keen, cloud):
    db = keen.db
    now = utc_now_naive()
    assert runner.due(db, "aws", now)
    runner.refresh_all(db)
    assert cloud["calls"] == 1
    assert not runner.due(db, "aws", now + timedelta(hours=1))
    runner.refresh_all(db)
    assert cloud["calls"] == 1
    assert runner.due(
        db, "aws", now + timedelta(seconds=settings.cloud_posture_poll_seconds + 5)
    )


def test_demo_mode_answers_with_fictional_data(keen, cloud, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    assert runner.refresh(keen.db, "aws")["scopes"] == 2
    assert cloud["calls"] == 0
    labels = sorted(s.label for s in keen.db.query(CloudPostureScope).all())
    assert labels == ["Production", "Staging"]
    since = datetime(2026, 10, 1)
    entries, complete = runner._activity(
        aws.PROVIDER, since, since + timedelta(hours=3)
    )
    assert complete and len(entries) == 3
    assert (
        entries == runner._activity(aws.PROVIDER, since, since + timedelta(hours=3))[0]
    )


# --- activity -------------------------------------------------------------------


def entry(ident, at, outcome="success"):
    return Activity(
        id=ident,
        at=at,
        scope=ACCOUNT,
        action="ConsoleLogin",
        actor=f"arn:aws:iam::{ACCOUNT}:user/alice",
        outcome=outcome,
        summary="ConsoleLogin by alice (success)",
        detail={"mfa_used": True, "source_ip": "192.0.2.10"},
        raw={"eventName": "ConsoleLogin"},
    )


def test_activity_is_read_from_the_cursor_and_recorded_once(keen, cloud):
    db = keen.db
    now = utc_now_naive()
    cloud["activity"] = ([entry("a", now - timedelta(hours=2))], True)
    assert runner.collect_activity(db, "aws") == {
        "provider": "aws",
        "records": 1,
        "complete": True,
    }
    since, until = cloud["windows"][0]
    assert until - since == runner.MAX_WINDOW or until - since <= runner.FIRST_LOOKBACK
    stored = db.query(Event).filter_by(source="aws", action="ConsoleLogin").one()
    assert stored.actor.endswith("user/alice") and stored.connection_id == "env:aws"
    assert stored.normalized_payload["mfa_used"] is True
    cursor = (
        db.query(IngestionCursor).filter(IngestionCursor.name.like("env:aws:%")).one()
    )
    assert cursor.last_ts == until
    row = db.get(CloudProvider, "aws")
    assert row.vendor_id is not None and row.activity_truncated is False

    # The next read starts where the last ended; repeated entries are deduped.
    cloud["activity"] = ([entry("a", now - timedelta(hours=2)), entry("b", now)], False)
    runner.collect_activity(db, "aws")
    assert cloud["windows"][1][0] == until
    assert db.query(Event).filter_by(source="aws", action="ConsoleLogin").count() == 2
    db.expire_all()
    assert db.get(CloudProvider, "aws").activity_truncated is True

    cloud["activity"] = Unavailable("permission_denied")
    assert runner.collect_activity(db, "aws")["error"] == "permission_denied"
    db.expire_all()
    assert db.get(CloudProvider, "aws").activity_error == "permission_denied"


def test_a_long_gap_reads_at_most_a_day_back_in_bounded_windows(keen, cloud):
    db = keen.db
    with runner.connection_scope(runner.connection(aws.PROVIDER)):
        name = runner.namespace("cloud-activity:aws")
    db.add(IngestionCursor(name=name, last_ts=datetime(2020, 1, 1), meta={}))
    db.commit()
    runner.collect_activity(db, "aws")
    since, until = cloud["windows"][0]
    assert since >= utc_now_naive() - timedelta(days=1, minutes=30)
    assert until - since <= runner.MAX_WINDOW


# --- the Vendor page --------------------------------------------------------------


def vendor_of(keen):
    return keen.db.get(CloudProvider, "aws").vendor_id


def test_vendor_page_shows_posture_to_evidence_readers_only(keen, cloud):
    runner.refresh(keen.db, "aws")
    now = utc_now_naive()
    cloud["activity"] = ([entry("a", now - timedelta(hours=1), "failure")], True)
    runner.collect_activity(keen.db, "aws")
    person = IsmsPerson(name="Bob Example", email="bob@example.org")
    keen.db.add(person)
    keen.db.commit()
    vendor_id = vendor_of(keen)
    path = f"/v1/isms/vendors/{vendor_id}/cloud-posture"

    for codes in ((), ("isms.read",), ("events.read",)):
        client = keen.client(keen.user(*codes))
        assert client.get(path).status_code == 403, codes
    client = keen.client(keen.user(*READER))
    response = client.get(path)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["can_refresh"] is False and data["can_create_risk"] is False
    (provider,) = data["providers"]
    assert (provider["provider"], provider["label"], provider["enabled"]) == (
        "aws",
        "AWS",
        True,
    )
    (found,) = provider["scopes"]
    assert (found["met"], found["judged"]) == (1, 2)
    checks = {c["key"]: c for c in found["checks"]}
    assert checks["identity.mfa"]["subjects"] == [{"name": "bob", "risks": []}]
    assert checks["findings"]["status"] == "unknown"
    assert "not set up" in checks["findings"]["reason"]
    bob = next(i for i in found["identities"] if i["name"] == "bob")
    assert bob["person"] == {"id": str(person.id), "name": "Bob Example"}
    assert [a["action"] for a in provider["activity"]] == ["ConsoleLogin"]
    assert provider["activity"][0]["outcome"] == "failure"

    detail = client.get(f"/v1/isms/vendors/{vendor_id}")
    assert detail.status_code == 200 and detail.json()["has_cloud_posture"] is True
    other = IsmsVendor(name="Unrelated supplier")
    keen.db.add(other)
    keen.db.commit()
    assert (
        client.get(f"/v1/isms/vendors/{other.id}").json()["has_cloud_posture"] is False
    )
    assert (
        client.get(f"/v1/isms/vendors/{other.id}/cloud-posture").json()["providers"]
        == []
    )
    assert (
        client.get(f"/v1/isms/vendors/{uuid.uuid4()}/cloud-posture").status_code == 404
    )


def capture_tasks(monkeypatch) -> list:
    """Queued cloud tasks, as (task, keyword arguments)."""
    from app.worker import tasks

    queued = []
    for name in ("cloud_posture_refresh_task", "cloud_activity_task"):
        monkeypatch.setattr(
            getattr(tasks, name),
            "apply_async",
            lambda name=name, **kwargs: queued.append((name, kwargs)),
        )
    return queued


def test_refresh_requests_are_for_administrators_and_spaced(keen, cloud, monkeypatch):
    queued = capture_tasks(monkeypatch)
    runner.refresh(keen.db, "aws")
    path = f"/v1/isms/vendors/{vendor_of(keen)}/cloud-posture/refresh"
    reader = keen.client(keen.user(*READER))
    assert reader.post(path, headers=unsafe(reader)).status_code == 403
    admin = keen.client(keen.user(role="admin"))
    assert admin.post(path).status_code == 403  # CSRF and Origin are required.
    response = admin.post(path, headers=unsafe(admin))
    assert response.status_code == 202, response.text
    assert response.json() == {"queued": ["aws"]}
    assert queued == [
        ("cloud_posture_refresh_task", {"args": ["aws"], "expires": 300}),
        ("cloud_activity_task", {"args": ["aws"], "expires": 300}),
    ]
    again = admin.post(path, headers=unsafe(admin))
    assert again.status_code == 409
    monkeypatch.setattr(settings, "aws_enabled", False)
    keen.db.get(CloudProvider, "aws").refresh_requested_at = None
    keen.db.commit()
    assert admin.post(path, headers=unsafe(admin)).status_code == 409
    assert len(queued) == 2
    data = admin.get(f"/v1/isms/vendors/{vendor_of(keen)}/cloud-posture").json()
    assert data["can_refresh"] is True and data["providers"][0]["enabled"] is False


def test_admin_runners_queue_a_provider_before_any_vendor_shows_it(
    keen, cloud, monkeypatch
):
    queued = capture_tasks(monkeypatch)
    path = "/v1/admin/ingest/cloud/aws/run"
    reader = keen.client(keen.user(*READER, "risk.manage"))
    assert reader.post(path, headers=unsafe(reader)).status_code == 403
    admin = keen.client(keen.user(role="admin"))
    assert admin.post(path).status_code == 403  # CSRF and Origin are required.
    for bad in ("telepathy", "AWS", "aws%2Frun", "x" * 40):
        response = admin.post(
            f"/v1/admin/ingest/cloud/{bad}/run", headers=unsafe(admin)
        )
        assert response.status_code in (404, 422), (bad, response.status_code)
    monkeypatch.setattr(settings, "linode_enabled", False)
    response = admin.post("/v1/admin/ingest/cloud/linode/run", headers=unsafe(admin))
    assert response.status_code == 409
    assert keen.db.get(CloudProvider, "linode") is None
    # Requests share the test's connection; end this read before they write.
    keen.db.rollback()
    assert queued == []

    response = admin.post(path, headers=unsafe(admin))
    assert response.status_code == 202, response.text
    assert response.json()["providers"] == ["aws"]
    assert "AWS" in response.json()["note"]
    assert [name for name, _ in queued] == [
        "cloud_posture_refresh_task",
        "cloud_activity_task",
    ]
    # One request window per provider, shared with the Vendor page.
    assert admin.post(path, headers=unsafe(admin)).status_code == 409
    runner.refresh(keen.db, "aws")
    refresh = f"/v1/isms/vendors/{vendor_of(keen)}/cloud-posture/refresh"
    assert admin.post(refresh, headers=unsafe(admin)).status_code == 409
    keen.db.get(CloudProvider, "aws").refresh_requested_at = None
    keen.db.add(SourceIngestionState(source="aws", paused=True))
    keen.db.commit()
    assert admin.post(path, headers=unsafe(admin)).status_code == 409
    assert len(queued) == 2

    status = admin.get("/v1/admin/ingest/status").json()
    assert status["cloud"]["aws"] is True and status["cloud"]["linode"] is False
    assert set(status["cloud"]) == set(KEYS)


def test_a_failed_first_read_still_shows_its_reason_on_a_vendor(keen, cloud):
    cloud["results"] = Unavailable("not_configured")
    cloud["activity"] = Unavailable("permission_denied")
    assert runner.refresh(keen.db, "aws")["error"] == "not_configured"
    assert runner.collect_activity(keen.db, "aws")["error"] == "permission_denied"
    client = keen.client(keen.user(*READER))
    data = client.get(f"/v1/isms/vendors/{vendor_of(keen)}/cloud-posture").json()
    (provider,) = data["providers"]
    assert provider["scopes"] == []
    assert "credentials were not accepted" in provider["error"]
    assert "not allowed" in provider["activity_error"]


def test_risks_are_recorded_against_checks_and_subjects(keen, cloud):
    runner.refresh(keen.db, "aws")
    vendor_id = vendor_of(keen)
    path = f"/v1/isms/vendors/{vendor_id}/cloud-posture/risks"
    body = {
        "provider": "aws",
        "scope_key": ACCOUNT,
        "check": "identity.mfa",
        "title": "Console users without MFA",
        "framework": "A",
    }
    reader = keen.client(keen.user(*READER))
    assert reader.post(path, json=body, headers=unsafe(reader)).status_code == 403
    owner = keen.user(*READER, "risk.manage")
    client = keen.client(owner)
    for bad, status in (
        ({**body, "check": "telepathy"}, 400),
        ({**body, "scope_key": "999999999999"}, 404),
        ({**body, "subject_key": "mallory"}, 404),
        ({**body, "provider": "linode"}, 404),
        ({**body, "unexpected": 1}, 422),
    ):
        response = client.post(path, json=bad, headers=unsafe(client))
        assert response.status_code == status, (bad, response.text)
    other = IsmsVendor(name="Somebody else")
    keen.db.add(other)
    keen.db.commit()
    elsewhere = f"/v1/isms/vendors/{other.id}/cloud-posture/risks"
    assert client.post(elsewhere, json=body, headers=unsafe(client)).status_code == 404
    assert keen.db.query(Risk).count() == 0

    today = utc_now_naive().date()
    accept = {
        **body,
        "subject_key": "bob",
        "treatment_strategy": "accept",
        "risk_owner_user_id": str(owner.id),
        "treatment_due_at": (today + timedelta(days=30)).isoformat(),
    }
    response = client.post(path, json=accept, headers=unsafe(client))
    assert response.status_code == 201, response.text
    created = response.json()
    asset = keen.db.get(RiskAsset, uuid.UUID(created["asset_id"]))
    assert asset.vendor_id == vendor_id
    assert asset.name == f"AWS account · Production ({ACCOUNT})"
    link = keen.db.query(PostureRiskLink).one()
    assert (
        link.origin,
        link.origin_key,
        link.scope_key,
        link.check_key,
        link.subject_key,
    ) == (
        "cloud",
        "aws",
        ACCOUNT,
        "identity.mfa",
        "bob",
    )
    data = client.get(f"/v1/isms/vendors/{vendor_id}/cloud-posture").json()
    assert data["can_create_risk"] is True
    mfa = next(
        c
        for c in data["providers"][0]["scopes"][0]["checks"]
        if c["key"] == "identity.mfa"
    )
    # Every listed subject is accepted, so the check reads as accepted.
    assert mfa["status"] == "accepted"
    assert mfa["subjects"][0]["risks"][0]["title"] == "Console users without MFA"
    # A second risk in the same account reuses its asset.
    again = client.post(path, json=body, headers=unsafe(client))
    assert again.status_code == 201 and again.json()["asset_id"] == created["asset_id"]


# --- administration ---------------------------------------------------------------


def test_source_catalogue_lists_the_provider_and_it_can_be_paused(
    keen, cloud, monkeypatch, tmp_path
):
    from app.services.managed_configurations import CONNECTORS

    for name, (setting, _, _) in CONNECTORS.items():
        (tmp_path / f"{name}.yml").write_text("{}")
        monkeypatch.setattr(settings, setting, str(tmp_path / f"{name}.yml"))
    (tmp_path / "rules.yml").write_text("rules: []")
    monkeypatch.setattr(settings, "rules_path", str(tmp_path / "rules.yml"))
    runner.refresh(keen.db, "aws")
    admin = keen.client(keen.user(role="admin"))
    items = admin.get("/v1/admin/source-catalogue").json()["items"]
    item = next(i for i in items if i["adapter"] == "aws")
    assert item["enabled"] is True and item["collection_items"] == 1
    assert item["can_pause"] is True and item["observed_events"] == 2
    response = admin.put(
        "/v1/admin/sources/aws/ingestion-state",
        json={"paused": True},
        headers=unsafe(admin),
    )
    assert response.status_code == 200, response.text
    assert runner.refresh(keen.db, "aws")["reason"] == "paused"


def test_cloud_tasks_are_scheduled_on_the_http_ingestion_queue():
    from app.worker.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert schedule["cloud-posture-refresh"]["schedule"] == 900.0
    assert schedule["cloud-activity"]["schedule"] == float(
        settings.cloud_activity_poll_seconds
    )
    routes = celery_app.conf.task_routes
    for name in ("cloud_posture_refresh_task", "cloud_activity_task"):
        assert routes[f"app.worker.tasks.{name}"] == {"queue": "ingest-http"}


def test_help_documents_the_shared_settings_and_actions():
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[2]
        / "ui/public/help/administrator-cloud-posture.html"
    )
    text = page.read_text()
    for name in (
        "KEEN_CLOUD_POSTURE_POLL_SECONDS",
        "KEEN_CLOUD_ACTIVITY_POLL_SECONDS",
        "KEEN_CLOUD_INACTIVE_DAYS",
        "KEEN_CLOUD_KEY_AGE_DAYS",
    ):
        assert f"<code>{name}</code>" in text, name
    # Checks named after a provider are that provider's own.
    shared = {k for p in providers() for k in p.checks if k.split(".")[0] not in KEYS}
    for key in shared:
        assert f"<code>posture.{key}</code>" in text, key
    assert f"{runner.KEEP_DAYS} days" in text


def test_demo_seed_stores_every_provider_and_teardown_removes_it(keen, monkeypatch):
    from teardown_demo_data import DemoTeardown, TeardownConfig

    db = keen.db
    monkeypatch.setattr(settings, "demo_mode", True)
    assert runner.refresh(db, "aws", force=False)["reason"] == "disabled"
    from app.ingest.cloud import demo

    assert demo.seed(db) == len(KEYS)
    assert db.query(CloudProvider).count() == len(KEYS)
    assert db.query(CloudPostureScope).count() >= len(KEYS)
    activity = db.query(Event).filter(
        ~Event.action.like("posture.%"), Event.source == "aws"
    )
    assert activity.count() == 72
    # Seeding again adds nothing new.
    demo.seed(db)
    assert activity.count() == 72
    unrelated = Event(
        source="aws",
        external_id="someone-else",
        timestamp=utc_now_naive(),
        summary="Not from the demo seed",
    )
    db.add(unrelated)
    db.commit()
    DemoTeardown(db, TeardownConfig("iso_27001_2022", "Demo", False, True)).run()
    db.commit()
    assert db.query(CloudProvider).count() == 0
    assert db.query(CloudPostureScope).count() == 0
    assert db.query(Event).filter(Event.source.in_(KEYS)).all() == [unrelated]
    assert (
        db.query(IsmsVendor).filter(IsmsVendor.name == "Amazon Web Services").count()
        == 0
    )
    assert (
        db.query(IngestionCursor).filter(IngestionCursor.name.like("env:aws:%")).count()
        == 0
    )
    # Outside demo mode nothing is seeded.
    monkeypatch.setattr(settings, "demo_mode", False)
    assert demo.seed(db) == 0


def test_scopes_keep_their_kind_and_checks_their_own_label(keen, cloud):
    owners = Check("identity.admins", "Owners", "info", "2 principals", ["a", "b"])
    cloud["results"] = [
        scope(),
        ScopeResult("444455556666", "Shared services", [owners], kind="organisation"),
    ]
    runner.refresh(keen.db, "aws")
    client = keen.client(keen.user(*READER))
    data = client.get(f"/v1/isms/vendors/{vendor_of(keen)}/cloud-posture").json()
    found = {s["key"]: s for s in data["providers"][0]["scopes"]}
    assert found[ACCOUNT]["kind"] == "account"
    assert found["444455556666"]["kind"] == "organisation"
    assert found["444455556666"]["checks"][0]["label"] == "Owners"
