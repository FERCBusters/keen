"""Authority, availability and redaction regressions through the assembled app.

Requests pass through the production middleware, real password sign-in, CSRF,
session storage and permission resolution. The session store is an in-memory
Valkey substitute; PostgreSQL is used when KEEN_CONTRACT_TEST_DATABASE_URL is
set (SQLite otherwise, which does not establish locking behaviour).
"""

import json
import uuid
from unittest.mock import MagicMock, Mock

import fakeredis
import pytest
from app.core import valkey
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import (
    AuditLog,
    EntityChangelog,
    Event,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    Permission,
    User,
)
from app.security.passwords import hash_password
from app.services.errors import DomainError
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

BASE = "https://keen.example"
PASSWORD = "synthetic-password-for-tests"


@pytest.fixture
def keen(contract_db, tmp_path, monkeypatch):
    from app import main
    from app.db.session import get_db
    from sqlalchemy.orm import sessionmaker

    server = fakeredis.FakeServer()

    class FakeValkey:
        @staticmethod
        def from_url(*args, **kwargs):
            return fakeredis.FakeRedis(server=server, decode_responses=True)

    valkey.get_valkey.cache_clear()
    monkeypatch.setattr(valkey, "Redis", FakeValkey)
    # Match the application's SessionLocal (no autoflush, expire on commit) so
    # requests behave as they do in production.
    factory = sessionmaker(
        bind=contract_db.get_bind(),
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )

    def database():
        with factory() as session:
            yield session

    monkeypatch.setattr(main, "SessionLocal", factory)
    main.app.dependency_overrides[get_db] = database
    # Configuration readers open their own short session; point them at the
    # same test database so assembled request paths see the same data.
    from app.core import managed_configuration, source_meta

    monkeypatch.setattr(managed_configuration, "SessionLocal", factory)
    monkeypatch.setattr(settings, "public_base_url", BASE)
    monkeypatch.setattr(settings, "trust_remote_user", False)
    monkeypatch.setattr(settings, "mfa_policy", "optional")
    monkeypatch.setattr(settings, "local_auth_enabled", True)
    monkeypatch.setattr(settings, "hosted_mode", False)
    monkeypatch.setattr(settings, "demo_mode", False)
    # Saving an ISMS entity or risk with framework links records an automatic
    # diary entry; give its artifacts a writable local store and stub the
    # display metadata that reads plugin configuration files.
    monkeypatch.setattr(settings, "artifact_storage_backend", "local")
    monkeypatch.setattr(settings, "artifact_local_dir", str(tmp_path))
    monkeypatch.setattr(source_meta, "_configured_source_meta", lambda bucket: {})

    class Keen:
        db = contract_db
        store = valkey.get_valkey()
        # date_trunc aggregates run only on PostgreSQL; SQLite runs still cover
        # every rejection, which happens before any aggregate query.
        postgres = contract_db.get_bind().dialect.name == "postgresql"

        def user(self, *codes, role="normal"):
            row = User(
                username="user-" + uuid.uuid4().hex[:10],
                password_hash=hash_password(PASSWORD),
                role=role,
                is_active=True,
            )
            contract_db.add(row)
            for code in codes:
                permission = (
                    contract_db.query(Permission).filter_by(code=code).one_or_none()
                )
                if permission is None:
                    permission = Permission(code=code, description=code)
                    contract_db.add(permission)
                row.permissions.append(permission)
            contract_db.add(row)
            contract_db.commit()
            return row

        def client(self, user=None):
            client = TestClient(main.app, base_url=BASE, raise_server_exceptions=False)
            if user is not None:
                response = client.post(
                    "/v1/auth/login",
                    json={"username": user.username, "password": PASSWORD},
                    headers={"Origin": BASE},
                )
                assert response.status_code == 200, response.text
            return client

    yield Keen()
    main.app.dependency_overrides.pop(get_db, None)
    valkey.get_valkey.cache_clear()


def unsafe(client):
    return {"Origin": BASE, "X-CSRF-Token": client.cookies.get("keen_csrf")}


# --- Bounded chart work -------------------------------------------------------


def test_daily_bucket_budget_rejects_wide_windows_before_work(keen, monkeypatch):
    from app.services import stats

    client = keen.client(keen.user("events.read"))
    path = "/v1/charts/event_volume_timeseries"
    if keen.postgres:
        within = client.get(
            path, params={"start_date": "2016-01-01", "end_date": "2026-01-01"}
        )
        assert within.status_code == 200, within.text
        assert len(within.json()["items"]) <= stats.MAX_TIMESERIES_BUCKETS

    # Rejection happens before any aggregate query runs.
    monkeypatch.setattr(
        stats, "diary_filter_condition", Mock(side_effect=AssertionError)
    )
    for params in (
        {"start_date": "1000-01-01", "end_date": "1999-12-31"},
        {
            "start_ts": "1000-01-01T00:00:00",
            "end_ts": "2026-01-01T00:00:00",
            "interval": "second",
        },
        {"start_date": "2026-01-01", "end_date": "9999-12-31"},
    ):
        response = client.get(path, params=params)
        assert response.status_code == 400, (params, response.text)


# --- Evidence aggregates follow events.read -----------------------------------


def test_event_aggregates_require_events_read(keen):
    plain = keen.client(keen.user())
    assert plain.get("/v1/events").status_code == 403
    for path in (
        "/v1/charts/event_volume_timeseries",
        "/v1/graph/control_source",
        "/v1/graph/clause_source",
    ):
        assert plain.get(path).status_code == 403, path
    summary = plain.get("/v1/stats/summary").json()
    assert summary["events"] is None
    assert summary["controls"]["with_evidence_in_scope"] is None
    assert summary["controls"]["without_evidence_all"] is None
    assert all(
        item["evidence_count"] is None and item["last_evidence"] is None
        for item in plain.get("/v1/stats/controls").json()["items"]
    )

    reader = keen.client(keen.user("events.read"))
    assert reader.get("/v1/stats/summary").json()["events"] is not None
    if keen.postgres:
        for path in (
            "/v1/graph/control_source",
            "/v1/charts/event_volume_timeseries",
        ):
            assert reader.get(path).status_code == 200, path


# --- Organisation roles that own risks ----------------------------------------


def _risk_owned_by_new_role(keen):
    admin = keen.client(keen.user(role="admin"))
    node = admin.post(
        "/v1/isms/org-nodes",
        json={"name": "Owner " + uuid.uuid4().hex[:6], "node_type": "role"},
        headers=unsafe(admin),
    )
    assert node.status_code in (200, 201), node.text
    risk = admin.post(
        "/v1/risks",
        json={
            "asset_name": "Synthetic asset",
            "category_name": "Synthetic category",
            "subcategory_name": "Synthetic subcategory",
            "risk_types": ["confidentiality"],
            "threat_summary": "synthetic restricted risk",
            "risk_owner_role_id": node.json()["id"],
        },
        headers=unsafe(admin),
    )
    assert risk.status_code in (200, 201), risk.text
    return node.json()["id"], risk.json()["id"]


def test_isms_manager_cannot_join_or_remove_a_risk_owning_role(keen):
    node_id, risk_id = _risk_owned_by_new_role(keen)
    manager = keen.user("isms.manage")
    client = keen.client(manager)
    assert client.get(f"/v1/risks/{risk_id}").status_code == 403

    join = client.patch(
        f"/v1/isms/org-nodes/{node_id}",
        json={"user_ids": [str(manager.id)]},
        headers=unsafe(client),
    )
    assert join.status_code == 403, join.text
    assert client.get(f"/v1/risks/{risk_id}").status_code == 403
    assert client.get("/v1/me/risks").json().get("items", []) == []
    deleted = client.delete(f"/v1/isms/org-nodes/{node_id}", headers=unsafe(client))
    assert deleted.status_code == 403

    # Ordinary organisation chart edits remain available.
    renamed = client.patch(
        f"/v1/isms/org-nodes/{node_id}",
        json={"name": "Renamed role", "user_ids": []},
        headers=unsafe(client),
    )
    assert renamed.status_code == 200, renamed.text

    risk_manager = keen.user("isms.manage", "risk.manage")
    trusted = keen.client(risk_manager)
    assigned = trusted.patch(
        f"/v1/isms/org-nodes/{node_id}",
        json={"user_ids": [str(manager.id)]},
        headers=unsafe(trusted),
    )
    assert assigned.status_code == 200, assigned.text
    assert client.get(f"/v1/risks/{risk_id}").status_code == 200


def test_isms_manager_can_manage_members_of_roles_without_risks(keen):
    manager = keen.user("isms.manage")
    client = keen.client(manager)
    node = client.post(
        "/v1/isms/org-nodes",
        json={"name": "Plain role", "node_type": "role"},
        headers=unsafe(client),
    )
    assert node.status_code in (200, 201), node.text
    updated = client.patch(
        f"/v1/isms/org-nodes/{node.json()['id']}",
        json={"user_ids": [str(manager.id)]},
        headers=unsafe(client),
    )
    assert updated.status_code == 200, updated.text


# --- Sign-out --------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/v1/auth/logout", "/v1/auth/oidc/logout"])
def test_sign_out_fails_closed_when_revocation_fails(keen, monkeypatch, path):
    client = keen.client(keen.user())
    sid = client.cookies.get(settings.session_cookie_name)
    with monkeypatch.context() as patch:
        patch.setattr(
            type(keen.store), "delete", Mock(side_effect=ConnectionError("store down"))
        )
        failed = client.post(path, headers=unsafe(client))
    assert failed.status_code == 503, failed.text
    assert settings.session_cookie_name not in failed.headers.get("set-cookie", "")
    assert client.get("/v1/me").status_code == 200

    done = client.post(path, headers=unsafe(client))
    assert done.status_code in (200, 204), done.text
    replay = keen.client()
    replay.cookies.set(settings.session_cookie_name, sid)
    assert replay.get("/v1/me").status_code == 401


# --- Webhook persistence and replay claims -------------------------------------


def test_failed_webhook_persistence_releases_replay_claim(keen, monkeypatch):
    from app.ingest import webhooks

    monkeypatch.setenv("SYNTHETIC_WEBHOOK_SECRET", "synthetic-webhook-secret")
    monkeypatch.setattr(
        webhooks,
        "load_webhook_policies",
        lambda path: {
            "providers": {
                "synthetic": {
                    "secret_header": "X-Synthetic-Secret",
                    "secret_env": "SYNTHETIC_WEBHOOK_SECRET",
                }
            }
        },
    )
    calls = []

    def store(db, **kwargs):
        calls.append(kwargs["external_id"])
        if len(calls) == 1:
            raise OSError("artifact storage unavailable")
        return {"ok": True, "event_id": str(uuid.uuid4())}

    monkeypatch.setattr(webhooks, "store_event_with_artifact", store)
    client = keen.client()
    body = json.dumps({"delivery": uuid.uuid4().hex}).encode()
    headers = {
        "X-Synthetic-Secret": "synthetic-webhook-secret",
        "Content-Type": "application/json",
    }
    path = "/v1/webhooks/synthetic/alert"
    assert client.post(path, content=body, headers=headers).status_code == 500
    retried = client.post(path, content=body, headers=headers)
    assert retried.status_code == 200, retried.text
    assert len(calls) == 2
    # A delivery that did persist is still rejected as a replay.
    assert client.post(path, content=body, headers=headers).status_code == 400
    assert len(calls) == 2


# --- Metric webhook redaction ---------------------------------------------------


def test_metric_webhook_redacts_extracted_fields_and_history(keen, monkeypatch):
    from app.api.routes import webhooks as webhook_routes

    monkeypatch.setattr(
        webhook_routes,
        "verify_secret",
        lambda provider, headers: headers.get("x-keen-metrics-secret") == "synthetic",
    )
    key = "metric-" + uuid.uuid4().hex[:8]
    keen.db.add(
        IsmsEffectivenessMeasure(
            framework_slug=settings.default_framework_slug, metric_key=key
        )
    )
    keen.db.commit()
    secrets = ["SYNTH-QUERY-SECRET", "SYNTH-FRAGMENT-TOKEN", "SYNTH-NOTE-PASSWORD"]
    secrets += ["SYNTH-BEARER", "SYNTH-TITLE-SECRET", "SYNTH-REF-TOKEN"]
    response = keen.client().post(
        f"/v1/webhooks/isms/effectiveness-metrics/{key}",
        json={
            "value": 1,
            "source_url": "https://grafana.example/d?api_key=SYNTH-QUERY-SECRET"
            "#access_token=SYNTH-FRAGMENT-TOKEN",
            "notes": "rotated password=SYNTH-NOTE-PASSWORD",
            "text": "Authorization: Bearer SYNTH-BEARER",
            "source_title": "secret=SYNTH-TITLE-SECRET",
            "source_reference": "token=SYNTH-REF-TOKEN",
        },
        headers={"X-Keen-Metrics-Secret": "synthetic"},
    )
    assert response.status_code == 200, response.text
    entry_id = uuid.UUID(response.json()["metric_entry"]["id"])
    row = keen.db.get(IsmsEffectivenessMetricEntry, entry_id)
    stored = json.dumps(
        [
            row.source_url,
            row.notes,
            row.qualitative_value,
            row.source_title,
            row.source_reference,
            row.raw_payload,
            response.json(),
        ]
    )
    history = json.dumps(
        [
            [c.before_state, c.after_state, c.changes]
            for c in keen.db.query(EntityChangelog).filter_by(
                entity_type="isms_effectiveness_metric"
            )
        ],
        default=str,
    )
    for value in secrets:
        assert value not in stored and value not in history, value
    assert row.source_url.startswith("https://grafana.example/d?")


# --- Write-only integration credentials -----------------------------------------


def test_integration_secret_presentation_changes_require_reentry(keen, monkeypatch):
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    client = keen.client(keen.user(role="admin"))
    body = {
        "name": "Synthetic API",
        "base_url": "https://api.example.test",
        "auth_kind": "header",
        "auth_name": "X-API-Key",
        "secret": "synthetic-saved-secret",
    }
    created = client.post(
        "/v1/admin/integrations/connections", json=body, headers=unsafe(client)
    )
    assert created.status_code == 200, created.text
    key, version = created.json()["id"], created.json()["version"]
    path = f"/v1/admin/integrations/connections/{key}"

    def save(**changes):
        payload = {**body, "secret": None, "version": version, **changes}
        return client.put(path, json=payload, headers=unsafe(client))

    for changes in (
        {"auth_name": "X-Reflected-Header"},
        {"base_url": "https://other.example.test"},
        {"auth_kind": "query", "auth_name": "api_key"},
    ):
        rejected = save(**changes)
        assert rejected.status_code == 422, (changes, rejected.text)

    renamed = save(name="Renamed API")
    assert renamed.status_code == 200, renamed.text
    version = renamed.json()["version"]
    resupplied = save(auth_name="X-Other-Key", secret="synthetic-new-secret")
    assert resupplied.status_code == 200, resupplied.text


def test_oauth_token_path_change_requires_reentry(keen, monkeypatch):
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    client = keen.client(keen.user(role="admin"))
    body = {
        "name": "Synthetic OAuth",
        "base_url": "https://api.example.test",
        "auth_kind": "oauth_client_credentials",
        "username": "synthetic-client",
        "auth_options": {"token_path": "/oauth/token"},
        "secret": "synthetic-client-secret",
    }
    created = client.post(
        "/v1/admin/integrations/connections", json=body, headers=unsafe(client)
    )
    assert created.status_code == 200, created.text
    response = client.put(
        f"/v1/admin/integrations/connections/{created.json()['id']}",
        json={
            **body,
            "secret": None,
            "version": created.json()["version"],
            "auth_options": {"token_path": "/echo"},
        },
        headers=unsafe(client),
    )
    assert response.status_code == 422, response.text


# --- Audit trail -----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/v1/auth/sso/oidc/callback",
        "/v1/auth/sso/google/callback",
        "/v1/auth/oidc/callback",
    ],
)
def test_audit_trail_omits_authorisation_codes(keen, path):
    keen.client().get(path, params={"code": "SYNTH-AUTH-CODE", "state": "SYNTH-STATE"})
    rows = keen.db.query(AuditLog).filter(AuditLog.path == path).all()
    assert rows
    assert all(
        "SYNTH-AUTH-CODE" not in (r.query_string or "")
        and "SYNTH-STATE" not in (r.query_string or "")
        for r in rows
    )


# --- Link validation -------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["/\\outside.example/login", "/\t/outside.example", "/\n/outside.example"]
)
def test_evidence_paths_cannot_leave_the_origin(url):
    from app.services.admin import _validate_clause_evidence_url
    from app.services.audits.validation import clean_url

    for validate in (clean_url, _validate_clause_evidence_url):
        with pytest.raises(DomainError):
            validate(url)
    assert clean_url("/event.html?id=1") == "/event.html?id=1"
    assert _validate_clause_evidence_url("/clause.html?id=1") == "/clause.html?id=1"


@pytest.mark.parametrize(
    "value", ["javascript:alert(1)", "data:text/html,x", "/relative", "https://a\\b"]
)
def test_imported_upstream_urls_must_be_web_links(value):
    from types import SimpleNamespace

    from app.core.references import control_upstream_url
    from app.services.admin import _validate_upstream_url

    control = SimpleNamespace(meta={"upstream_url": value}, ref="", framework_slug="")
    assert control_upstream_url(control) is None
    with pytest.raises(DomainError):
        _validate_upstream_url(value)
    control.meta["upstream_url"] = "https://guidance.example/a"
    assert control_upstream_url(control) == "https://guidance.example/a"


# --- SMTP credentials --------------------------------------------------------------


def test_smtp_credentials_are_never_sent_without_tls(monkeypatch):
    from app.services import mailer

    for name, value in {
        "smtp_host": "smtp.example.test",
        "smtp_from_email": "keen@example.test",
        "smtp_username": "synthetic-user",
        "smtp_password": "synthetic-password",
        "smtp_use_tls": False,
        "smtp_use_ssl": False,
    }.items():
        monkeypatch.setattr(settings, name, value)
    plain, secure = MagicMock(), MagicMock()
    monkeypatch.setattr(mailer.smtplib, "SMTP", plain)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", secure)
    with pytest.raises(RuntimeError, match="TLS"):
        mailer.send_email(to_email="person@example.test", subject="s", body="b")
    plain.assert_not_called()
    secure.assert_not_called()

    # Unauthenticated relays may still be used without TLS.
    monkeypatch.setattr(settings, "smtp_username", "")
    monkeypatch.setattr(settings, "smtp_password", "")
    mailer.send_email(to_email="person@example.test", subject="s", body="b")
    plain.return_value.__enter__.return_value.login.assert_not_called()


def test_withheld_aggregates_never_reuse_a_readers_cache(keen):
    """A permitted reader's cached aggregates must not reach an unpermitted caller."""
    keen.db.add(
        Event(
            timestamp=utc_now_naive(),
            source="synthetic",
            summary="synthetic",
            external_id="x-" + uuid.uuid4().hex,
        )
    )
    keen.db.commit()
    reader = keen.client(keen.user("events.read"))
    assert reader.get("/v1/stats/summary").json()["events"] is not None
    assert reader.get("/v1/stats/controls").status_code == 200
    plain = keen.client(keen.user())
    assert plain.get("/v1/stats/summary").json()["events"] is None
    assert plain.get("/v1/stats/summary").json()["stats_source"] == "evidence_withheld"


# --- Question attachments stay out of collected evidence ---------------------


def test_question_attachments_are_not_presented_as_event_evidence(
    keen, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "artifact_storage_backend", "local")
    monkeypatch.setattr(settings, "artifact_local_dir", str(tmp_path))
    event = Event(
        timestamp=utc_now_naive(),
        source="synthetic",
        summary="synthetic evidence",
        external_id="q-" + uuid.uuid4().hex,
    )
    keen.db.add(event)
    keen.db.commit()
    asker = keen.client(keen.user("events.read", "question.create"))
    png = b"\x89PNG\r\n\x1a\n" + b"synthetic-image"
    created = asker.post(
        f"/v1/events/{event.id}/questions",
        data={"body": "Is this the right screenshot?"},
        files={"attachments": ("screenshot.png", png, "image/png")},
        headers=unsafe(asker),
    )
    assert created.status_code == 200, created.text

    reader = keen.client(keen.user("events.read"))
    detail = reader.get(f"/v1/events/{event.id}").json()
    assert detail["artifacts"] == []
    threads = reader.get(f"/v1/events/{event.id}/questions").json()["threads"]
    attachment = threads[0]["posts"][0]["attachments"][0]
    assert attachment["filename"] == "screenshot.png"
    download = reader.get(attachment["download_url"].removeprefix("/api"))
    assert download.status_code == 200 and download.content == png
    if keen.postgres:
        listed = reader.get("/v1/events", params={"source": "synthetic"}).json()
        row = next(i for i in listed["items"] if i["id"] == str(event.id))
        assert row["artifact_count"] == 0


# --- Password confirmation attempts per account -----------------------------


def test_password_change_attempts_are_limited_per_account(keen, monkeypatch):
    monkeypatch.setattr(settings, "login_rate_limit_per_ip", 100)
    monkeypatch.setattr(settings, "login_rate_limit_per_username", 2)
    client = keen.client(keen.user())
    wrong = {"current_password": "not-the-password", "new_password": "x" * 16}
    for _ in range(2):
        response = client.post("/v1/me/password", json=wrong, headers=unsafe(client))
        assert response.status_code == 400, response.text
    right = {"current_password": PASSWORD, "new_password": "y" * 16}
    limited = client.post("/v1/me/password", json=right, headers=unsafe(client))
    assert limited.status_code == 429, limited.text


# --- Unauthenticated webhook callers learn nothing before verification --------


def test_webhook_state_is_not_disclosed_before_secret_verification(keen, monkeypatch):
    from app.ingest import webhooks
    from app.services import ingestion_pause

    monkeypatch.setattr(webhooks, "load_webhook_policies", lambda path: {})
    monkeypatch.setattr(
        ingestion_pause,
        "require_receiving",
        Mock(side_effect=DomainError(503, "Webhook ingestion is paused")),
    )
    client = keen.client()
    for path in (
        "/v1/webhooks/synthetic/alert",
        f"/v1/webhooks/connections/{uuid.uuid4()}/synthetic/alert",
    ):
        response = client.post(path, content=b"{}", headers={"X-Webhook-Secret": "x"})
        assert response.status_code == 403, (path, response.text)
    ingestion_pause.require_receiving.assert_not_called()


# --- API documentation requires a session ------------------------------------

API_DOCUMENTATION = (
    "/openapi.json",
    "/v1/openapi.json",
    "/docs",
    "/v1/docs",
    "/redoc",
    "/v1/redoc",
    "/docs/oauth2-redirect",
    "/v1/docs/oauth2-redirect",
)


def test_api_documentation_requires_a_signed_in_session(keen):
    anonymous = keen.client()
    for path in API_DOCUMENTATION:
        response = anonymous.get(path)
        assert response.status_code == 401, (path, response.status_code)
        assert "paths" not in response.text
    signed_in = keen.client(keen.user())
    schema = signed_in.get("/openapi.json")
    assert schema.status_code == 200 and "/v1/me" in schema.json()["paths"]
    assert signed_in.get("/v1/docs").status_code == 200


# --- Evidence containing NUL characters ------------------------------------------


def test_collected_evidence_with_nul_characters_is_stored(contract_db, monkeypatch):
    """An upstream NUL must not make every collection run fail before its cursor moves."""
    from datetime import datetime
    from types import SimpleNamespace

    from app.ingest import common
    from app.security.redaction import prepare_event_fields

    monkeypatch.setattr(
        common,
        "put_bytes",
        lambda **kwargs: SimpleNamespace(
            uri="test://artifact", sha256="0" * 64, size_bytes=len(kwargs["data"])
        ),
    )
    monkeypatch.setattr(common, "apply_rules", lambda *args: 0)
    result = common.store_event_with_artifact(
        contract_db,
        timestamp=datetime(2026, 1, 1),
        source="synthetic",
        system="host\x00name",
        actor="user\x00",
        action="login",
        outcome="ok",
        severity=3,
        summary="line \x00 with NUL",
        raw_pointer={"query\x00": "x\x00"},
        normalized_payload={"line": ["a\x00b"], "key\x00": {"nested": "\x00"}},
        external_id="synthetic-\x00-1",
        artifact_kind="log_line",
        artifact_bytes=b"line \x00 with NUL",
        artifact_content_type="text/plain",
        artifact_key="synthetic/nul-1.log",
        captured_by="test",
    )
    stored = contract_db.get(Event, uuid.UUID(result["event_id"]))
    assert stored.summary == "line � with NUL"
    assert stored.actor == "user�" and stored.system == "host�name"
    assert stored.normalized_payload == {
        "line": ["a�b"],
        "key�": {"nested": "�"},
    }
    assert "\x00" not in json.dumps(stored.raw_pointer)
    bulk = prepare_event_fields(
        {"summary": "x\x00", "normalized_payload": {"k": "\x00"}}
    )
    assert bulk == {"summary": "x�", "normalized_payload": {"k": "�"}}


# --- Request decoding and value ranges -------------------------------------------


def test_invalid_json_and_validation_errors_fail_cleanly_without_echo(keen):
    client = keen.client()
    login = "/v1/auth/login"
    headers = {"Origin": BASE, "Content-Type": "application/json"}
    for body in (
        b"NaN",
        b"1e999999",
        b'{"username": Infinity}',
        b"[" * 5000 + b"]" * 5000,
    ):
        response = client.post(login, content=body, headers=headers)
        assert response.status_code == 400, (body[:20], response.status_code)
    echoed = client.post(
        login,
        json={"username": "synthetic", "password": ["synthetic-secret-value"]},
        headers={"Origin": BASE},
    )
    assert echoed.status_code == 422
    assert "synthetic-secret-value" not in echoed.text
    assert echoed.json()["detail"][0]["loc"] == ["body", "password"]

    user = keen.user()
    signed_in = keen.client(user)
    nested = (
        b'{"name":"n","url":"/events.html","x":' + b'{"x":' * 200 + b"1" + b"}" * 201
    )
    response = signed_in.post(
        "/v1/me/saved-searches",
        content=nested,
        headers={**unsafe(signed_in), "Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_nul_characters_in_requests_are_refused_or_stored_safely(keen):
    user = keen.user("events.read")
    client = keen.client(user)
    assert client.get("/v1/events", params={"q": "a\x00b"}).status_code == 400
    assert client.get("/v1/events/a%00b").status_code == 400
    saved = client.post(
        "/v1/me/saved-searches",
        content=b'{"name": "synthetic\\u0000name", "url": "/events.html"}',
        headers={**unsafe(client), "Content-Type": "application/json"},
    )
    assert saved.status_code in (200, 201), saved.text
    assert "synthetic�name" in json.dumps(
        client.get("/v1/me/saved-searches").json(), ensure_ascii=False
    )


def test_out_of_range_request_values_are_client_errors(keen):
    client = keen.client(keen.user("events.read", "isms.read", "audits.read"))
    huge = "9" * 30
    assert client.get("/v1/isms/assets", params={"offset": huge}).status_code == 400
    assert (
        client.get(
            "/v1/audits/scope-coverage", params={"end": "0001-01-01"}
        ).status_code
        == 400
    )
    if keen.postgres:
        export = client.get(
            "/v1/events/export",
            params={"start_date": "9999-12-01", "end_date": "9999-12-31"},
        )
        assert export.status_code == 200, export.status_code
        chart = client.get(
            "/v1/charts/event_volume_timeseries", params={"start_ts": "-1"}
        )
        assert chart.status_code in (200, 400), chart.status_code


def test_document_managers_reach_the_bookstack_catalogue(keen, monkeypatch):
    monkeypatch.setattr(settings, "bookstack_enabled", False)
    manager = keen.client(keen.user("isms.manage"))
    response = manager.get("/v1/isms/bookstack-catalog", params={"kind": "books"})
    assert response.status_code == 409, response.text
    reader = keen.client(keen.user("isms.read"))
    assert reader.get("/v1/isms/bookstack-catalog").status_code == 403


def test_hand_parsed_bodies_must_be_json_objects(keen, contract_db):
    asker = keen.user("events.read", "question.create")
    client = keen.client(asker)
    event = Event(
        timestamp=utc_now_naive(),
        source="synthetic",
        summary="synthetic event",
        raw_pointer={},
        normalized_payload={},
        external_id="synthetic-" + uuid.uuid4().hex,
    )
    contract_db.add(event)
    contract_db.commit()
    path = f"/v1/events/{event.id}/questions"
    listed = client.post(path, json=["not", "an", "object"], headers=unsafe(client))
    assert listed.status_code == 400, listed.text
    form = client.post(
        path, data={"body": "synthetic \x00 question"}, headers=unsafe(client)
    )
    assert form.status_code < 500, form.text
