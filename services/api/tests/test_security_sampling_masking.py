"""Assembled-application regressions for masking, audit sampling and snapshots."""

import hashlib
import json
import subprocess
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import fakeredis
import pytest
from app import main
from app.core.config import settings
from app.db import session as db_module
from app.db.models import (
    Audit,
    AuditEvidence,
    BookStackSectionEvidence,
    ControlItem,
    Event,
    Framework,
    IsmsDocument,
    Permission,
    Risk,
    RiskAsset,
    RiskAssetSubcategory,
    RiskCategory,
    User,
    user_permissions,
)
from app.security import auth, csrf, permissions, sessions
from app.services import bookstack_sections
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def sampling_client(contract_db, monkeypatch, tmp_path):
    factory = sessionmaker(
        bind=contract_db.get_bind(),
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(auth, "get_valkey", lambda: redis)
    monkeypatch.setattr(permissions, "get_valkey", lambda: redis)
    for name, value in {
        "public_base_url": "https://keen.example",
        "trust_remote_user": False,
        "hosted_mode": False,
        "demo_mode": False,
        "mfa_policy": "optional",
        "artifact_storage_backend": "local",
        "artifact_local_dir": str(tmp_path),
    }.items():
        monkeypatch.setattr(settings, name, value)
    with factory() as db:
        user = User(
            username="audit-manager",
            password_hash="synthetic-unused",
            role="normal",
            is_active=True,
        )
        permission = Permission(code="audits.manage", description="Manage audits only")
        fw = Framework(slug="audit-test", name="Audit test")
        db.add_all([user, permission, fw])
        db.flush()
        db.execute(
            user_permissions.insert(),
            {"user_id": user.id, "permission_id": permission.id},
        )
        audit = Audit(
            title="Test audit", framework_slug=fw.slug, created_by_user_id=user.id
        )
        event = Event(
            timestamp=datetime(2026, 1, 1),
            source="loki",
            summary="SYNTHETIC RESTRICTED EVENT",
            external_id="audit-test",
            raw_pointer={},
            normalized_payload={},
        )
        control = ControlItem(
            framework_slug=fw.slug,
            ref="A.1",
            type="annex_control",
            title="Test control",
        )
        document = IsmsDocument(title="Restricted policy")
        db.add_all([audit, event, control, document])
        db.flush()
        section = BookStackSectionEvidence(
            document_id=document.id,
            target_control_id=control.id,
            page_id=1,
            permalink="https://bookstack.example/link/1",
            page_title="Restricted page",
            storage_uri="local://local/test-section.json",
            sha256="0" * 64,
        )
        db.add(section)
        db.commit()
        ids = SimpleNamespace(
            user=user.id,
            audit=audit.id,
            event=event.id,
            section=section.id,
            document=document.id,
            control=control.id,
        )
        assert permissions.get_effective_permission_codes(
            db, user, use_cache=False
        ) == {"audits.manage"}
    (tmp_path / "test-section.json").write_bytes(
        b'{"private_policy":"SYNTHETIC RESTRICTED SNAPSHOT"}'
    )
    client = TestClient(main.app, base_url="https://keen.example")
    sid = sessions.create_session(redis, user_id=str(ids.user), ttl_seconds=300)
    client.cookies.set(settings.session_cookie_name, sid)
    client.cookies.set(csrf.csrf_cookie_name(), "synthetic-csrf")
    client.headers.update(
        {"Origin": "https://keen.example", csrf.csrf_header_name(): "synthetic-csrf"}
    )
    try:
        yield client, factory, ids
    finally:
        client.close()


def grant(factory, user_id, code):
    with factory() as db:
        permission = db.query(Permission).filter_by(code=code).one_or_none()
        if permission is None:
            permission = Permission(code=code, description=code)
            db.add(permission)
            db.flush()
        db.execute(
            user_permissions.insert(),
            {"user_id": user_id, "permission_id": permission.id},
        )
        db.get(User, user_id).authz_version += 1
        db.commit()


@pytest.mark.parametrize("via_url", [False, True])
def test_event_sampling_requires_events_read(sampling_client, via_url):
    client, factory, ids = sampling_client
    target = (
        {"evidence_url": f"/event.html?id={ids.event}"}
        if via_url
        else {"event_id": str(ids.event)}
    )
    assert client.get(f"/v1/events/{ids.event}").status_code == 403
    url = f"/v1/audits/{ids.audit}/evidence"
    denied = client.post(url, json=target)
    assert denied.status_code == 403, denied.text
    assert "SYNTHETIC RESTRICTED EVENT" not in denied.text
    with factory() as db:
        assert db.query(AuditEvidence).count() == 0
    grant(factory, ids.user, "events.read")
    permitted = client.post(url, json=target)
    assert permitted.status_code == 200, permitted.text
    assert permitted.json()["event"]["summary"] == "SYNTHETIC RESTRICTED EVENT"


def test_sampling_cannot_grant_itself_bookstack_snapshot_read(sampling_client):
    client, factory, ids = sampling_client
    download_url = f"/v1/isms/bookstack-sections/{ids.section}/snapshot"
    sample_url = f"/v1/audits/{ids.audit}/evidence"
    target = {"entity_type": "bookstack_section", "entity_id": str(ids.section)}
    assert client.get(download_url).status_code == 403
    denied = client.post(sample_url, json=target)
    assert denied.status_code == 403, denied.text
    assert client.get(download_url).status_code == 403
    with factory() as db:
        assert db.query(AuditEvidence).count() == 0
    grant(factory, ids.user, "isms.read")
    permitted = client.post(sample_url, json=target)
    assert permitted.status_code == 200, permitted.text
    assert client.get(download_url).status_code == 200
    # Sharing an authorised sample still permits audit readers to download it.
    with factory() as db:
        permission = db.query(Permission).filter_by(code="isms.read").one()
        db.execute(
            user_permissions.delete().where(
                user_permissions.c.user_id == ids.user,
                user_permissions.c.permission_id == permission.id,
            )
        )
        db.get(User, ids.user).authz_version += 1
        db.commit()
    assert client.get(download_url).status_code == 200
    assert client.post(sample_url, json=target).status_code == 403


@pytest.mark.parametrize(
    "entity_type",
    [
        "cia_risk",
        "pestle",
        "interested-party",
        "objective",
        "document",
        "org",
        "asset",
        "access",
        "app_config",
        "meeting",
        "effectiveness",
        "metric",
    ],
)
def test_sampling_other_sources_requires_their_read_permission(
    sampling_client, entity_type
):
    client, factory, ids = sampling_client
    grant(factory, ids.user, "events.read")
    response = client.post(
        f"/v1/audits/{ids.audit}/evidence",
        json={"entity_type": entity_type, "entity_id": str(uuid.uuid4())},
    )
    assert response.status_code == 403, response.text
    with factory() as db:
        assert db.query(AuditEvidence).count() == 0


@pytest.mark.parametrize("mask", ["false", "true"])
def test_bookstack_capture_redacts_before_storage_and_checksum(
    sampling_client, monkeypatch, mask
):
    _, factory, ids = sampling_client
    monkeypatch.setattr(settings, "bookstack_enabled", True)
    monkeypatch.setattr(settings, "event_data_masking", mask)
    monkeypatch.setattr(settings, "bookstack_base_url", "https://bookstack.example")

    class Response:
        content = b"{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "id": 2,
                "name": "password=SYNTHETIC_TITLE_SECRET",
                "api_key": "SYNTHETIC_SECRET_VALUE",
                "html": "<p>password=SYNTHETIC_PASSWORD</p>",
                "contact": "person@example.test",
            }

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, *a, **kw):
            return Response()

    monkeypatch.setattr(bookstack_sections, "_client", Client)
    captured = []

    def storage(key, data, content_type):
        captured.append(data)
        return SimpleNamespace(
            uri="local://local/captured.json",
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )

    monkeypatch.setattr(bookstack_sections, "put_bytes", storage)
    with factory() as db:
        user = db.get(User, ids.user)
        result = bookstack_sections.capture_section(
            payload=bookstack_sections.SectionInput(
                document_id=ids.document, page_id=2, target_control_id=ids.control
            ),
            user=user,
            db=db,
        )
        assert result["page_id"] == 2
    assert b"SYNTHETIC_SECRET_VALUE" not in captured[0]
    assert b"SYNTHETIC_PASSWORD" not in captured[0]
    assert b"SYNTHETIC_TITLE_SECRET" not in captured[0]
    assert "SYNTHETIC_TITLE_SECRET" not in result["page_title"]
    assert json.loads(captured[0])["bookstack_page"]["api_key"] == "REDACTED"
    assert result["sha256"] == hashlib.sha256(captured[0]).hexdigest()
    if mask == "true":
        assert b"person@example.test" not in captured[0]
    else:
        assert b"person@example.test" in captured[0]


def test_ipv6_masking_malformed_input_finishes_in_bounded_child():
    # A killable child prevents a regex regression from hanging the test runner.
    from app.security import redaction

    source = Path(redaction.__file__).resolve()
    script = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location('masking', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for bad in (':' * 65536 + 'z', '.' * 65536 + 'z', 'a:' * 32768 + 'z'):
    assert module.mask_event_data_str(bad) == bad
for address in ('::1', '2001:db8::1', '2001:db8:0:0:0:0:0:1', '::ffff:192.0.2.1'):
    assert module.mask_event_data_str(address) == '[MASKED_IP]'
"""
    subprocess.run([sys.executable, "-c", script, str(source)], check=True, timeout=3)


@pytest.mark.parametrize("owner", [False, True])
def test_risk_sampling_preserves_owner_visibility(sampling_client, owner):
    client, factory, ids = sampling_client
    grant(factory, ids.user, "events.read")
    with factory() as db:
        category = RiskCategory(name="Test category")
        db.add(category)
        db.flush()
        subcategory = RiskAssetSubcategory(
            name="Test subcategory", category_id=category.id
        )
        db.add(subcategory)
        db.flush()
        asset = RiskAsset(
            name="Test asset", category_id=category.id, subcategory_id=subcategory.id
        )
        db.add(asset)
        db.flush()
        risk = Risk(asset_id=asset.id, risk_owner_user_id=ids.user if owner else None)
        db.add(risk)
        db.commit()
        risk_id = risk.id
    response = client.post(
        f"/v1/audits/{ids.audit}/evidence",
        json={"entity_type": "risk", "entity_id": str(risk_id)},
    )
    assert response.status_code == (200 if owner else 403), response.text
    with factory() as db:
        assert db.query(AuditEvidence).count() == int(owner)


@pytest.mark.parametrize("entity_type", [None, "", "   "])
def test_url_only_samples_also_require_events_read(sampling_client, entity_type):
    client, factory, ids = sampling_client
    url = f"/v1/audits/{ids.audit}/evidence"
    target = {
        "evidence_url": "https://evidence.example/report",
        "title": "External evidence",
        "entity_type": entity_type,
    }
    assert client.post(url, json=target).status_code == 403
    with factory() as db:
        assert db.query(AuditEvidence).count() == 0
    grant(factory, ids.user, "events.read")
    assert client.post(url, json=target).status_code == 200
    with factory() as db:
        permission = db.query(Permission).filter_by(code="audits.manage").one()
        db.execute(
            user_permissions.delete().where(
                user_permissions.c.user_id == ids.user,
                user_permissions.c.permission_id == permission.id,
            )
        )
        db.get(User, ids.user).authz_version += 1
        db.commit()
    assert client.post(url, json=target).status_code == 403


@pytest.mark.parametrize(
    "other_permission", ["events.read", "risk.read", "isms.manage"]
)
def test_isms_sampling_requires_explicit_isms_read(sampling_client, other_permission):
    client, factory, ids = sampling_client
    grant(factory, ids.user, other_permission)
    url = f"/v1/audits/{ids.audit}/evidence"
    target = {"entity_type": "bookstack_section", "entity_id": str(ids.section)}
    assert client.post(url, json=target).status_code == 403
    with factory() as db:
        assert db.query(AuditEvidence).count() == 0
    grant(factory, ids.user, "isms.read")
    assert client.post(url, json=target).status_code == 200


@pytest.fixture
def other_audits(sampling_client):
    _, factory, _ = sampling_client
    with factory() as db:
        other = User(
            username="another-auditor",
            password_hash="unused",
            role="normal",
            is_active=True,
        )
        db.add(other)
        db.flush()
        audit = Audit(
            title="Other audit",
            framework_slug="audit-test",
            created_by_user_id=other.id,
        )
        template = Audit(
            title="Other template",
            start_date=date(2026, 11, 1),
            framework_slug="audit-test",
            status="template",
            created_by_user_id=other.id,
        )
        db.add_all([audit, template])
        db.commit()
        return audit.id, template.id


@pytest.mark.parametrize(
    "method,suffix,payload",
    [
        ("PATCH", "", {"title": "Unauthorised edit"}),
        ("PUT", "/scope", {"controls": [], "clauses": [], "documents": []}),
        ("PUT", "/controls", {"controls": []}),
        ("POST", "/evidence", {"evidence_url": "https://evidence.example/report"}),
        ("PATCH", "/evidence/{id}", {"title": "Unauthorised edit"}),
        ("DELETE", "/evidence/{id}", None),
        ("POST", "/attendees", {"name": "New person"}),
        ("DELETE", "/attendees/{id}", None),
        ("POST", "/findings", {"kind": "ofi", "title": "Finding"}),
        ("PATCH", "/findings/{id}", {"title": "Unauthorised edit"}),
        ("DELETE", "/findings/{id}", None),
    ],
)
def test_audit_manager_cannot_mutate_other_audits(
    sampling_client, other_audits, method, suffix, payload
):
    client, factory, ids = sampling_client
    grant(factory, ids.user, "events.read")
    audit_id, _ = other_audits
    suffix = suffix.replace("{id}", str(uuid.uuid4()))
    response = client.request(method, f"/v1/audits/{audit_id}{suffix}", json=payload)
    assert response.status_code == 403, response.text
    with factory() as db:
        assert db.get(Audit, audit_id).title == "Other audit"
        assert db.query(AuditEvidence).count() == 0


def test_report_upload_and_schedule_edit_require_audit_ownership(
    sampling_client, other_audits
):
    client, factory, _ = sampling_client
    audit_id, template_id = other_audits
    assert (
        client.post(
            f"/v1/audits/{audit_id}/report",
            files={"file": ("report.txt", b"Synthetic report", "text/plain")},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/v1/audits/scheduled/{template_id}", json={"title": "Changed"}
        ).status_code
        == 403
    )
    with factory() as db:
        assert db.get(Audit, audit_id).final_report_storage_uri is None
        assert db.get(Audit, template_id).title == "Other template"


def test_audit_manager_runs_own_audit_but_cannot_delete_it(sampling_client):
    client, factory, ids = sampling_client
    assert (
        client.patch(
            f"/v1/audits/{ids.audit}", json={"title": "Own audit edited"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/v1/audits/{ids.audit}/findings",
            json={"kind": "ofi", "title": "Own finding"},
        ).status_code
        == 200
    )
    assert client.delete(f"/v1/audits/{ids.audit}").status_code == 403
    created = client.post(
        "/v1/audits", json={"title": "New own audit", "framework_slug": "audit-test"}
    )
    assert created.status_code == 200, created.text
    assert created.json()["created_by_user_id"] == str(ids.user)
    schedule = client.post(
        "/v1/audits/scheduled",
        json={
            "title": "Own template",
            "framework_slug": "audit-test",
            "start_date": "2026-11-01",
        },
    )
    assert schedule.status_code == 200, schedule.text
    template_id = schedule.json()["id"]
    assert (
        client.patch(
            f"/v1/audits/scheduled/{template_id}", json={"title": "Own template edited"}
        ).status_code
        == 200
    )
    assert client.delete(f"/v1/audits/scheduled/{template_id}").status_code == 403
    with factory() as db:
        assert db.get(Audit, uuid.UUID(template_id)).title == "Own template edited"


def test_audit_administrator_can_edit_and_delete_but_still_needs_source_read(
    sampling_client, other_audits
):
    client, factory, ids = sampling_client
    grant(factory, ids.user, "audits.admin")
    with factory() as db:
        permission = db.query(Permission).filter_by(code="audits.manage").one()
        db.execute(
            user_permissions.delete().where(
                user_permissions.c.user_id == ids.user,
                user_permissions.c.permission_id == permission.id,
            )
        )
        db.get(User, ids.user).authz_version += 1
        db.commit()
    audit_id, template_id = other_audits
    assert (
        client.patch(
            f"/v1/audits/{audit_id}", json={"title": "Administrator edited"}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/v1/audits/scheduled/{template_id}",
            json={"title": "Administrator edited template"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/v1/audits/{audit_id}/evidence", json={"event_id": str(ids.event)}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/v1/audits/{audit_id}/evidence",
            json={"entity_type": "bookstack_section", "entity_id": str(ids.section)},
        ).status_code
        == 403
    )
    assert client.delete(f"/v1/audits/scheduled/{template_id}").status_code == 200
    assert client.delete(f"/v1/audits/{audit_id}").status_code == 200
    with factory() as db:
        assert db.get(Audit, audit_id) is None
        assert db.get(Audit, template_id) is None


def test_audit_permission_migration_adds_no_implicit_grants(monkeypatch):
    import importlib.util

    from sqlalchemy import create_engine, text

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/0007_audit_permissions.py"
    )
    spec = importlib.util.spec_from_file_location("audit_permissions_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE permissions (id TEXT PRIMARY KEY, code TEXT UNIQUE, description TEXT, created_at TEXT)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO permissions VALUES ('manager', 'audits.manage', 'old', CURRENT_TIMESTAMP)"
            )
        )
        monkeypatch.setattr(migration.op, "execute", connection.execute)
        migration.upgrade()
        migration.upgrade()
        assert (
            connection.execute(
                text("SELECT count(*) FROM permissions WHERE code='audits.admin'")
            ).scalar()
            == 1
        )
        assert (
            "those you created"
            in connection.execute(
                text("SELECT description FROM permissions WHERE code='audits.manage'")
            ).scalar()
        )
        migration.downgrade()
        assert connection.execute(
            text("SELECT code FROM permissions")
        ).scalars().all() == ["audits.manage"]
    engine.dispose()
