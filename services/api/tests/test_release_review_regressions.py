"""Regressions for pre-release review findings, through assembled entry points."""

import json
import uuid
from datetime import datetime

import fakeredis
import pytest
from app.db.models import (
    Audit,
    EventQuestionThread,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    SourceConnection,
)
from app.ingest import connections as sc
from app.services.context import ChangeContext
from app.services.errors import DomainError
from cryptography.fernet import Fernet

from tests.test_security_sampling_masking import (  # noqa: F401
    grant,
    sampling_client,
)


@pytest.fixture
def queued(monkeypatch):
    """Record cleanup intents; the PostgreSQL queue itself has separate coverage."""
    from app.services import evidence_retention

    uris = []
    monkeypatch.setattr(
        evidence_retention, "queue_object", lambda db, uri: uri and uris.append(uri)
    )
    return uris


# --- Agent ingestion: shared proxy addresses -------------------------------


@pytest.fixture
def agent_client(monkeypatch):
    from app.api.routes import agents as api
    from app.security import rate_limit

    from tests.test_keen_agent import setup

    generator = setup.__wrapped__(monkeypatch)
    client, _factory, headers, _info, _stored, _app = next(generator)
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(api, "get_valkey", lambda: redis)
    monkeypatch.setattr(api, "fixed_window_allow", rate_limit.fixed_window_allow)
    try:
        yield client, headers
    finally:
        generator.close()


@pytest.mark.parametrize("path", ["/v1/otlp/logs", "/v1/agents/heartbeat"])
def test_unauthenticated_traffic_cannot_exhaust_agents_shared_address(
    agent_client, path
):
    from tests.test_keen_agent import record

    client, headers = agent_client
    junk = {"Authorization": "Bearer ka_" + str(uuid.uuid4()) + "." + "x" * 43}
    statuses = [
        client.post(path, json={}, headers=junk).status_code for _ in range(121)
    ]
    # Failed credentials from one address are still bounded.
    assert statuses[:120] == [401] * 120 and statuses[120] == 429
    body = record() if path == "/v1/otlp/logs" else {"version": "1"}
    accepted = client.post(path, json=body, headers=headers)
    assert accepted.status_code != 429, accepted.text


# --- Webhook secrets ---------------------------------------------------------


def test_non_ascii_webhook_secret_is_rejected_without_server_error(monkeypatch):
    from app.ingest import webhooks

    monkeypatch.setenv("SYNTHETIC_WEBHOOK_SECRET", "expected-secret")
    monkeypatch.setattr(
        webhooks,
        "load_webhook_policies",
        lambda _path: {
            "providers": {
                "demo": {
                    "secret_header": "X-Webhook-Secret",
                    "secret_env": "SYNTHETIC_WEBHOOK_SECRET",
                }
            }
        },
    )
    assert not webhooks.verify_secret("demo", {"x-webhook-secret": "\xe9" * 15})
    assert webhooks.verify_secret("demo", {"x-webhook-secret": "expected-secret"})
    connection = sc.Connection(
        "named",
        "webhooks",
        "named",
        {"provider": "demo"},
        {"secret": "named-secret"},
        {},
    )
    with sc.connection_scope(connection):
        assert not webhooks.verify_secret("demo", {"X-Webhook-Secret": "caf\xe9"})
        assert webhooks.verify_secret("demo", {"X-Webhook-Secret": "named-secret"})


def test_metric_webhook_redacts_recognised_secrets_before_storage(contract_db):
    from app.core.config import settings
    from app.services import webhooks

    measure = IsmsEffectivenessMeasure(
        framework_slug=settings.default_framework_slug, metric_key="patching"
    )
    contract_db.add(measure)
    contract_db.commit()
    webhooks.record_metric(
        db=contract_db,
        metric_key="patching",
        payload={"value": 3, "api_key": "SYNTHETIC-SECRET", "detail": {"token": "x"}},
        context=ChangeContext(method="POST", path="/test"),
    )
    entry = contract_db.query(IsmsEffectivenessMetricEntry).one()
    assert "SYNTHETIC-SECRET" not in json.dumps(entry.raw_payload)
    assert entry.raw_payload["value"] == 3


# --- Saved credentials follow the destination they were entered for ---------


def test_source_connection_destination_change_requires_credentials(
    contract_db, monkeypatch
):
    from app.api.routes import source_connections as api

    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    payload = api.ConnectionInput(
        source="gitea",
        name="Forge",
        configuration={"base_url": "https://git.example", "username": "svc"},
        credentials={"token": "SYNTHETIC-TOKEN"},
    )
    row = api.create(payload, contract_db)

    def edit(**changes):
        current = contract_db.get(SourceConnection, row["id"]).version
        return api.update(
            row["id"],
            payload.model_copy(update={"version": current, **changes}),
            contract_db,
        )

    for configuration in (
        {"base_url": "https://collector.attacker.example", "username": "svc"},
        {"base_url": "https://git.example", "username": "svc", "auth_mode": "query"},
    ):
        with pytest.raises(DomainError) as rejected:
            edit(configuration=configuration, credentials={})
        assert rejected.value.status_code == 422
        assert "Re-enter or clear the saved credentials" in rejected.value.detail
    resolved = sc.resolve(
        next(c for c in sc.connections(contract_db, "gitea") if c.id == row["id"])
    )
    assert resolved.configuration["base_url"] == "https://git.example"
    assert resolved.credentials == {"token": "SYNTHETIC-TOKEN"}

    # Permitted: unrelated edits, editor defaults, trailing slashes, re-entry
    # and clearing.
    edit(name="Renamed", credentials={})
    edit(
        configuration={
            "base_url": "https://git.example/",
            "username": "other",
            "auth_mode": "token",
        },
        credentials={"token": "", "cookie": ""},
    )
    edit(
        configuration={"base_url": "https://new.example", "username": "svc"},
        credentials={"token": "NEW-TOKEN"},
    )
    edit(
        configuration={"base_url": "https://other.example", "username": "svc"},
        credentials={},
        clear_credentials=["token"],
    )
    assert not contract_db.get(SourceConnection, row["id"]).encrypted_credentials


def test_integration_origin_change_requires_credential(monkeypatch):
    from app.api.routes import integrations as api
    from app.db.models import IntegrationConnection

    from tests.test_integration_builder import database

    generator = database.__wrapped__(monkeypatch)
    db, _factory = next(generator)
    try:
        monkeypatch.setenv(
            "KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode()
        )
        body = api.ConnectionInput(
            name="Secret",
            base_url="https://api.example.org",
            auth_kind="bearer",
            secret="SYNTHETIC",
        )
        created = api.create_connection(body, db)

        def save(**changes):
            version = db.get(IntegrationConnection, created["id"]).version
            update = {"version": version, "secret": "", **changes}
            return api.save_connection(
                created["id"], body.model_copy(update=update), db
            )

        with pytest.raises(DomainError) as rejected:
            save(base_url="https://other.example.org")
        assert rejected.value.status_code == 422
        save(base_url="https://api.example.org/v2")  # same origin
        save(base_url="https://other.example.org", secret="REPLACEMENT")
    finally:
        generator.close()


# --- Question deletion, storage cleanup and routed-path policy ---------------


def test_question_delete_permission_works_and_queues_attachment_cleanup(
    sampling_client,  # noqa: F811
    queued,
    monkeypatch,
):
    from app.worker import tasks

    monkeypatch.setattr(tasks.send_question_webhooks_task, "delay", lambda *a: None)
    client, factory, ids = sampling_client
    for code in ("events.read", "question.create"):
        grant(factory, ids.user, code)
    png = b"\x89PNG\r\n\x1a\n" + b"SYNTHETIC SCREENSHOT"
    created = client.post(
        f"/v1/events/{ids.event}/questions",
        data={"body": "please review"},
        files={"attachments": ("screenshot.png", png, "image/png")},
    )
    assert created.status_code == 200, created.text
    thread = created.json()["thread_id"]
    for path in (f"/v1/questions/{thread}", f"/v1/questions/{thread}/extra"):
        assert client.delete(path).status_code in {403, 404, 405}

    grant(factory, ids.user, "question.delete")
    deleted = client.delete(f"/v1/questions/{thread}")
    assert deleted.status_code == 200, deleted.text
    with factory() as db:
        assert db.query(EventQuestionThread).count() == 0
    assert len(queued) == 1 and queued[0].endswith(".png")


def test_encoded_query_separator_cannot_choose_a_different_write_policy(
    sampling_client,  # noqa: F811
):
    client, factory, ids = sampling_client
    grant(factory, ids.user, "events.read")
    for path in (
        f"/v1/events/{ids.event}/incident",
        "/v1/events/questions%3F/incident",
    ):
        response = client.post(path, json={"title": "x"})
        assert response.status_code == 403, (path, response.text)
        assert response.json()["detail"] == "incident.create permission required"


@pytest.mark.parametrize(
    "path,method",
    [
        ("/v1/questions/thread/posts/extra", "POST"),
        ("/v1/events/event/questions/extra", "POST"),
        ("/v1/questions/thread", "PATCH"),
        ("/v1/events/event/incident", "POST"),
    ],
)
def test_question_write_exemptions_are_exact(path, method):
    from app.security.route_policy import is_permission_checked_write

    from tests.test_security_design_contracts import request

    assert not is_permission_checked_write(request(path, method))


def test_replaced_and_deleted_files_are_queued_for_cleanup(
    contract_db, tmp_path, queued
):
    from app.core.config import settings
    from app.db.models import User
    from app.services.audits import reports
    from app.services.isms import documents

    old_local_dir = settings.artifact_local_dir
    old_backend = settings.artifact_storage_backend
    settings.artifact_local_dir = str(tmp_path)
    settings.artifact_storage_backend = "local"
    try:
        user = User(username="owner", password_hash="unused", role="admin")
        contract_db.add(user)
        contract_db.flush()
        doc = IsmsDocument(title="Policy")
        audit = Audit(title="Audit", framework_slug="fw", created_by_user_id=user.id)
        contract_db.add_all([doc, audit])
        contract_db.commit()
        context = ChangeContext(method="POST", path="/test")
        uris = []
        for content in (b"first", b"second"):
            documents.upload_document_file(
                document_id=str(doc.id),
                data=content,
                filename="policy.pdf",
                content_type="application/pdf",
                framework="fw",
                user=user,
                db=contract_db,
                context=context,
            )
            uris.append(contract_db.get(IsmsDocument, doc.id).storage_uri)
            reports.save_final_report(
                audit_id=str(audit.id),
                db=contract_db,
                user=user,
                filename="report.pdf",
                content_type="application/pdf",
                data=content,
            )
            uris.append(contract_db.get(Audit, audit.id).final_report_storage_uri)
        documents.delete_document(
            document_id=str(doc.id),
            context=context,
            framework="fw",
            user=user,
            db=contract_db,
        )
        # Both replaced files and the deleted document's current file.
        assert sorted(queued) == sorted([uris[0], uris[1], uris[2]])
    finally:
        settings.artifact_local_dir = old_local_dir
        settings.artifact_storage_backend = old_backend


# --- Administrator lockout guards are serialised -----------------------------


def test_admin_removing_operations_take_the_lock_before_counting(monkeypatch):
    from app.services import admin, users

    events = []
    for module in (users, admin):
        monkeypatch.setattr(
            module, "lock_admin_changes", lambda db: events.append("lock")
        )
        monkeypatch.setattr(
            module,
            "count_effective_admins",
            lambda db, **kw: events.append("count") or 1,
        )
    import inspect

    for function in (
        users.update_user_admin,
        users.delete_user_admin,
        users.set_user_access_admin,
        admin.admin_update_group,
        admin.admin_delete_group,
        admin.admin_set_group_members,
    ):
        source = inspect.getsource(function)
        assert source.index("lock_admin_changes(db)") < min(
            source.index(token)
            for token in ("db.query(", "count_effective_admins(")
            if token in source
        ), function.__name__
    assert datetime  # imported for fixtures that seed timestamps
