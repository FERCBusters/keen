"""Connection isolation contracts."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.api.routes import source_connections as api
from app.db.models import Event, SourceConnection
from app.ingest import connections as sc
from app.ingest.common import store_event_with_artifact
from app.mapping.rules import evaluate_by_framework, parse_rules
from app.services.errors import DomainError
from cryptography.fernet import Fernet


def connection(key, source="loki", **kwargs):
    return sc.Connection(
        key,
        source,
        key,
        kwargs.get("configuration", {}),
        kwargs.get("credentials", {}),
        kwargs.get("inputs", {}),
    )


def test_scope_restores_and_does_not_inherit_credentials(monkeypatch):
    monkeypatch.setattr(sc.deployment_settings, "loki_password", "environment-secret")
    with sc.connection_scope(
        connection("one", configuration={"base_url": "https://one.example"})
    ):
        assert sc.settings.loki_base_url == "https://one.example"
        assert sc.settings.loki_password == ""
        with sc.connection_scope(
            connection("two", credentials={"password": "two-secret"})
        ):
            assert sc.settings.loki_password == "two-secret"
        assert sc.identity()["connection_id"] == "one"
    assert sc.identity() == {}
    assert sc.settings.loki_password == "environment-secret"


def test_async_tasks_have_independent_scope():
    async def task(key):
        with sc.connection_scope(connection(key)):
            await asyncio.sleep(0)
            return sc.identity()["connection_id"], sc.namespace("upstream-1")

    async def run():
        return await asyncio.gather(task("one"), task("two"))

    result = asyncio.run(run())
    assert [r[0] for r in result] == ["one", "two"]
    assert result[0][1] != result[1][1]


def test_database_credentials_encrypted_masked_and_preserved(contract_db, monkeypatch):
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    payload = api.ConnectionInput(
        source="loki",
        name="Production",
        configuration={"base_url": "https://logs.example"},
        credentials={"password": "secret"},
    )
    row = api.create(payload, contract_db)
    assert row["has_credentials"] and "secret" not in str(row)
    stored = contract_db.get(SourceConnection, row["id"])
    assert "secret" not in stored.encrypted_credentials
    changed = api.update(
        row["id"],
        payload.model_copy(
            update={"version": row["version"], "credentials": {"password": ""}}
        ),
        contract_db,
    )
    assert sc.resolve(
        next(c for c in sc.connections(contract_db, "loki") if c.id == row["id"])
    ).credentials == {"password": "secret"}
    api.update(
        row["id"],
        payload.model_copy(
            update={
                "version": changed["version"],
                "credentials": {},
                "clear_credentials": ["password"],
            }
        ),
        contract_db,
    )
    assert not stored.encrypted_credentials


def test_same_upstream_event_is_distinct_per_connection(contract_db, monkeypatch):
    from app.ingest import common

    keys = []

    def put(**kwargs):
        keys.append(kwargs["key"])
        return SimpleNamespace(
            uri="s3://bucket/" + kwargs["key"], sha256="a" * 64, size_bytes=2
        )

    monkeypatch.setattr(common, "put_bytes", put)
    monkeypatch.setattr(common, "apply_rules", lambda *a: 0)

    def ingest():
        return store_event_with_artifact(
            contract_db,
            timestamp=datetime(2026, 1, 1),
            source="loki",
            system=None,
            actor=None,
            action=None,
            outcome=None,
            severity=3,
            summary="same",
            raw_pointer={},
            normalized_payload={},
            external_id="42",
            artifact_kind="json",
            artifact_bytes=b"{}",
            artifact_content_type="application/json",
            artifact_key="same.json",
            captured_by="test",
        )

    for key in ("one", "two"):
        with sc.connection_scope(connection(key)):
            assert not ingest().get("deduped")
            assert ingest()["deduped"]
    assert len(set(keys)) == 2
    assert {e.connection_id for e in contract_db.query(Event)} == {"one", "two"}


def test_rule_connection_condition():
    rules = parse_rules(
        {
            "rules": [
                {
                    "id": "one",
                    "when": {"source": "loki", "connection_id": "one"},
                    "map_to": [{"framework": "iso_27001_2022", "ref": "A.5.1"}],
                }
            ]
        }
    )
    assert evaluate_by_framework({"source": "loki", "connection_id": "one"}, rules)
    assert not evaluate_by_framework({"source": "loki", "connection_id": "two"}, rules)
    assert not evaluate_by_framework({"source": "loki"}, rules)


def test_failed_connection_does_not_block_others(monkeypatch):
    monkeypatch.setattr(sc.deployment_settings, "loki_enabled", True)
    monkeypatch.setattr(
        sc, "connections", lambda *a: [connection("bad"), connection("good")]
    )

    @sc.connection_runs("loki")
    def run(db):
        if sc.identity()["connection_id"] == "bad":
            raise RuntimeError("sensitive URL")
        return [{"created_events": 1}]

    db = Mock()
    result = run(db)
    assert result[0]["error"] == "Connection ingestion failed"
    assert result[1]["created_events"] == 1 and result[1]["connection_id"] == "good"
    db.rollback.assert_called_once()


def test_disabled_type_does_not_resolve_secrets(monkeypatch):
    monkeypatch.setattr(sc.deployment_settings, "loki_enabled", False)
    loader = Mock(side_effect=AssertionError("must not load credentials"))
    monkeypatch.setattr(sc, "connections", loader)
    assert sc.connection_runs("loki")(lambda db: [])(None)[0]["skipped"]
    loader.assert_not_called()


def test_aws_named_connection_requires_own_credentials():
    with sc.connection_scope(connection("aws", "cloudwatch_logs")):
        with pytest.raises(ValueError):
            sc.aws_credentials()
    with sc.connection_scope(
        connection(
            "aws",
            "cloudwatch_logs",
            credentials={"aws_access_key_id": "id", "aws_secret_access_key": "secret"},
        )
    ):
        assert sc.aws_credentials()["aws_access_key_id"] == "id"


def test_file_secrets_resolved_only_on_use(tmp_path, monkeypatch):
    path = tmp_path / "connections.yml"
    path.write_text(
        "connections:\n- id: prod\n  source: loki\n  name: Production\n  credentials:\n    password: {env: TEST_LOKI_PASSWORD}\n"
    )
    monkeypatch.setenv("KEEN_SOURCE_CONNECTIONS_FILE", str(path))
    item = next(c for c in sc.connections(None, "loki") if c.managed_by == "file")
    with pytest.raises(ValueError):
        sc.resolve(item)
    monkeypatch.setenv("TEST_LOKI_PASSWORD", "secret")
    assert sc.resolve(item).credentials["password"] == "secret"


def test_named_webhook_auth_and_replay_isolation(monkeypatch):
    from app.ingest import webhooks

    keys = []
    monkeypatch.setattr(
        webhooks,
        "get_valkey",
        lambda: SimpleNamespace(set=lambda key, *a, **kw: keys.append(key) or True),
    )
    for key in ("one", "two"):
        with sc.connection_scope(
            connection(
                key,
                "webhooks",
                configuration={"provider": "git", "secret_header": "X-Secret"},
                credentials={"secret": key},
            )
        ):
            assert webhooks.verify_secret("git", {"x-secret": key})
            assert not webhooks.verify_secret("other", {"x-secret": key})
            assert not webhooks.verify_secret("git", {"x-secret": "wrong"})
            assert not webhooks._check_replay_protection("git", "push", b"{}", {})
    assert len(set(keys)) == 2


def test_loki_uses_each_connections_endpoint_and_optional_auth():
    from app.ingest import loki

    for key in ("one", "two"):
        with sc.connection_scope(
            connection(
                key,
                configuration={
                    "base_url": "https://" + key + ".example",
                    "username": key,
                },
                credentials={"password": "secret-" + key},
            )
        ):
            assert loki.settings.loki_base_url == "https://" + key + ".example"
            assert loki.settings.loki_username == key
            assert loki.settings.loki_password == "secret-" + key
            assert sc.load_document("loki", "unused") == {}


def test_connection_delete_keeps_evidence_identity(contract_db, monkeypatch):
    row = api.create(api.ConnectionInput(source="loki", name="Production"), contract_db)
    event = Event(
        timestamp=datetime(2026, 1, 1),
        source="loki",
        summary="event",
        external_id="id",
        connection_id=row["id"],
        connection_name=row["name"],
    )
    contract_db.add(event)
    contract_db.commit()
    from fastapi import HTTPException

    with pytest.raises((HTTPException, DomainError)) as conflict:
        api.delete(row["id"], row["version"] + 1, contract_db)
    assert conflict.value.status_code == 409
    api.delete(row["id"], row["version"], contract_db)
    assert contract_db.get(Event, event.id).connection_name == "Production"
    assert contract_db.get(SourceConnection, row["id"]) is None


def test_cloudwatch_direct_insert_scopes_events_cursors_and_artifacts(
    contract_db, monkeypatch
):
    from datetime import timezone

    from app.db.models import IngestionCursor
    from app.ingest import cloudwatch_logs as cw

    monkeypatch.setattr(sc.deployment_settings, "event_data_masking", "true")
    stamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    client = SimpleNamespace(
        filter_log_events=lambda **kw: {
            "events": [
                {
                    "eventId": "same-id",
                    "timestamp": stamp,
                    "message": "user=person@example.test from 192.0.2.1 api-key=SECRET_VALUE",
                    "logStreamName": "stream",
                }
            ]
        }
    )
    monkeypatch.setattr(cw, "_cloudwatch_client", lambda region: client)
    monkeypatch.setattr(cw, "_apply_rules_and_store_mappings", lambda *a: 0)
    keys = []

    def store(**kw):
        assert b"SECRET_VALUE" not in kw["data"]
        assert b"person@example.test" not in kw["data"]
        assert b"192.0.2.1" not in kw["data"]
        keys.append(kw["key"])
        return SimpleNamespace(uri="test://" + kw["key"], sha256="0" * 64, size_bytes=9)

    monkeypatch.setattr(cw, "put_bytes", store)
    for key in ("one", "two"):
        with sc.connection_scope(connection(key, "cloudwatch_logs")):
            assert (
                cw.ingest_cloudwatch_logs_once(
                    contract_db, "query", {"log_group": "logs"}, {}
                )["created_events"]
                == 1
            )
            assert (
                cw.ingest_cloudwatch_logs_once(
                    contract_db, "query", {"log_group": "logs"}, {}
                )["created_events"]
                == 0
            )
    for event in contract_db.query(Event):
        for value in (
            event.summary,
            str(event.normalized_payload),
            str(event.raw_pointer),
        ):
            assert "SECRET_VALUE" not in value
            assert "person@example.test" not in value
            assert "192.0.2.1" not in value
    assert {e.connection_id for e in contract_db.query(Event)} == {"one", "two"}
    assert contract_db.query(IngestionCursor).count() == 2
    assert keys[0].startswith("connections/one/") and keys[1].startswith(
        "connections/two/"
    )


def test_loki_direct_insert_and_http_credentials_are_connection_scoped(
    contract_db, monkeypatch
):
    import base64
    from datetime import timezone

    import httpx
    from app.db.models import IngestionCursor
    from app.ingest import loki

    monkeypatch.setattr(sc.deployment_settings, "event_data_masking", "true")
    stamp = str(int(datetime.now(timezone.utc).timestamp() * 1_000_000_000))
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "streams",
                    "result": [
                        {
                            "stream": {"app": "auth"},
                            "values": [
                                [
                                    stamp,
                                    "user=person@example.test from 192.0.2.1 api-key=SECRET_VALUE",
                                ]
                            ],
                        }
                    ],
                },
            },
        )

    client_class = httpx.Client
    monkeypatch.setattr(
        loki.httpx,
        "Client",
        lambda **kw: client_class(
            **{k: v for k, v in kw.items() if k != "transport"},
            transport=httpx.MockTransport(handle),
        ),
    )
    monkeypatch.setattr(loki, "is_safe_url", lambda url: True)
    monkeypatch.setattr(loki, "_apply_rules_and_store_mappings", lambda *a: 0)
    monkeypatch.setattr(
        loki,
        "put_bytes",
        lambda **kw: SimpleNamespace(
            uri="test://" + kw["key"], sha256="0" * 64, size_bytes=9
        ),
    )
    for key in ("one", "two"):
        with sc.connection_scope(
            connection(
                key,
                configuration={
                    "base_url": "https://" + key + ".example",
                    "username": key,
                },
                credentials={"password": "password-" + key},
            )
        ):
            loki.ingest_loki_once(contract_db, "auth", '{app="auth"}', {})
            loki.ingest_loki_once(contract_db, "auth", '{app="auth"}', {})
    for event in contract_db.query(Event):
        for value in (
            event.summary,
            str(event.normalized_payload),
            str(event.raw_pointer),
        ):
            assert "SECRET_VALUE" not in value
            assert "person@example.test" not in value
            assert "192.0.2.1" not in value
    assert {e.connection_id for e in contract_db.query(Event)} == {"one", "two"}
    assert contract_db.query(Event).count() == 2
    assert contract_db.query(IngestionCursor).count() == 2
    assert {r.url.host for r in requests} == {"one.example", "two.example"}
    for req in requests:
        key = req.url.host.split(".")[0]
        assert (
            req.headers["Authorization"]
            == "Basic " + base64.b64encode((key + ":password-" + key).encode()).decode()
        )


def test_global_webhook_gate_includes_measurement_receipts(monkeypatch):
    from app.services.ingestion_pause import require_receiving
    from fastapi import HTTPException

    monkeypatch.setattr(sc.deployment_settings, "webhooks_enabled", False)
    with pytest.raises((HTTPException, DomainError)) as blocked:
        require_receiving(None, "webhooks")
    assert blocked.value.status_code == 503


@pytest.mark.parametrize("source,sections", sc.INPUT_SECTIONS.items())
def test_empty_environment_defaults_are_omitted(monkeypatch, source, sections):
    monkeypatch.setattr(sc.deployment_settings, source + "_enabled", True)
    default = sc.Connection(
        "env:" + source,
        source,
        "Environment default",
        {},
        {},
        None,
        managed_by="environment",
    )
    monkeypatch.setattr(
        sc, "connections", lambda *args: [default, connection("named", source)]
    )
    document = {section: [] for section in sections} | {
        "page_size": 100,
        "ui_name": "Label",
    }
    monkeypatch.setattr(sc, "deployment_document", lambda *args, **kwargs: document)
    seen = []

    @sc.connection_runs(source)
    def run(db):
        seen.append(sc.identity()["connection_id"])
        return [{"created_events": 1}]

    assert [row["connection_id"] for row in run(None)] == ["named"]
    assert seen == ["named"]
    for section in sections:
        document[section] = [{"name": "configured"}]
        assert [row["connection_id"] for row in run(None)] == ["env:" + source, "named"]
        document[section] = []


def test_broken_environment_config_does_not_block_named_source(monkeypatch):
    monkeypatch.setattr(sc.deployment_settings, "forgejo_enabled", True)
    default = sc.Connection(
        "env:forgejo",
        "forgejo",
        "Environment default",
        {},
        {},
        None,
        managed_by="environment",
    )
    monkeypatch.setattr(
        sc, "connections", lambda *args: [default, connection("named", "forgejo")]
    )

    def broken(*args, **kwargs):
        raise ValueError("private configuration details")

    monkeypatch.setattr(sc, "deployment_document", broken)
    result = sc.connection_runs("forgejo")(lambda db: [{"created_events": 1}])(None)
    assert result[0]["error"] == "Connection ingestion failed"
    assert "private" not in str(result)
    assert result[1]["connection_id"] == "named"


def test_bookstack_account_wide_environment_polling_is_preserved(monkeypatch):
    for field in ("base_url", "token_id", "token_secret"):
        monkeypatch.setattr(sc.deployment_settings, "bookstack_" + field, "configured")
    assert sc.environment_has_inputs("bookstack", None)


def test_a_named_google_connection_never_inherits_the_environment_key_files(
    monkeypatch,
):
    for field, value in {
        "google_workspace_sa_keyfile": "/run/secrets/environment-account.json",
        "google_workspace_sa_json_b64": "ZW52aXJvbm1lbnQ=",
        "google_workspace_sa_json": "environment-json",
        "google_workspace_config_path": "/app/config/google_workspace.yml",
    }.items():
        monkeypatch.setattr(sc.deployment_settings, field, value)
    named = sc.Connection(
        "named",
        "google_workspace",
        "Other tenant",
        {"impersonate": "admin@other.example"},
        {"sa_json": "named-json"},
        {},
    )

    with sc.connection_scope(named):
        assert sc.settings.google_workspace_sa_json == "named-json"
        assert sc.settings.google_workspace_impersonate == "admin@other.example"
        assert sc.settings.google_workspace_sa_keyfile == ""
        assert sc.settings.google_workspace_sa_json_b64 == ""
        # Settings that name no credential stay deployment-wide.
        assert (
            sc.settings.google_workspace_config_path
            == "/app/config/google_workspace.yml"
        )

    environment = next(
        c
        for c in sc.connections(None, "google_workspace")
        if c.managed_by == "environment"
    )
    with sc.connection_scope(environment):
        assert (
            sc.settings.google_workspace_sa_keyfile
            == "/run/secrets/environment-account.json"
        )


def _connections_file(tmp_path, monkeypatch, text):
    path = tmp_path / "connections.yml"
    path.write_text(text)
    monkeypatch.setenv("KEEN_SOURCE_CONNECTIONS_FILE", str(path))


@pytest.mark.parametrize(
    "entry,message",
    [
        (
            "- id: prod\n  source: loki\n  name: Prod\n  configuration: {region: eu}\n",
            "Unsupported field in source connections file",
        ),
        (
            "- id: prod\n  source: loki\n  name: Prod\n  credentials: {api_key: {env: KEY}}\n",
            "Unsupported field in source connections file",
        ),
        (
            "- id: prod logs\n  source: loki\n  name: Prod\n",
            "Connection file IDs must be stable alphanumeric identifiers",
        ),
        (
            "- id: prod\n  source: loki\n  name: Prod\n- id: prod\n  source: loki\n  name: Again\n",
            "Duplicate source connection ID",
        ),
    ],
    ids=["setting", "credential", "identifier", "duplicate"],
)
def test_a_connections_file_with_an_invalid_entry_is_refused(
    tmp_path, monkeypatch, entry, message
):
    _connections_file(tmp_path, monkeypatch, "connections:\n" + entry)

    with pytest.raises(ValueError, match=message):
        sc.connections(None, "loki")


def test_entries_for_other_source_types_are_read_when_those_types_are(
    tmp_path, monkeypatch
):
    _connections_file(
        tmp_path,
        monkeypatch,
        "connections:\n"
        "- id: logs\n  source: loki\n  name: Logs\n"
        "- id: code\n  source: github\n  name: Code\n  configuration: {region: eu}\n",
    )

    loki = sc.connections(None, "loki")

    assert [c.id for c in loki if c.managed_by == "file"] == ["file:logs"]
    with pytest.raises(ValueError, match="Unsupported field"):
        sc.connections(None)


@pytest.mark.parametrize(
    "reference,message",
    [
        ("plain-text-secret", "require an env or file reference"),
        ("{env: A, file: /tmp/b}", "require an env or file reference"),
        ("{vault: secret/keen}", "Unknown credential reference"),
        ("{env: TEST_MISSING_LOKI_PASSWORD}", "environment variable is missing"),
    ],
    ids=["inline", "two-references", "unknown-kind", "missing-variable"],
)
def test_file_credentials_must_be_references_that_resolve(
    tmp_path, monkeypatch, reference, message
):
    monkeypatch.delenv("TEST_MISSING_LOKI_PASSWORD", raising=False)
    _connections_file(
        tmp_path,
        monkeypatch,
        "connections:\n- id: prod\n  source: loki\n  name: Prod\n"
        f"  credentials:\n    password: {reference}\n",
    )
    (item,) = [c for c in sc.connections(None, "loki") if c.managed_by == "file"]

    with pytest.raises(ValueError, match=message):
        sc.resolve(item)


def test_a_file_credential_reference_reads_the_secret_file(tmp_path, monkeypatch):
    secret = tmp_path / "loki-password"
    secret.write_text("  file-secret\n")
    _connections_file(
        tmp_path,
        monkeypatch,
        "connections:\n- id: prod\n  source: loki\n  name: Prod\n"
        f"  credentials:\n    password: {{file: {secret}}}\n",
    )
    (item,) = [c for c in sc.connections(None, "loki") if c.managed_by == "file"]

    # The file is read when the connection is used, not when it is listed.
    assert item.credentials == {}
    assert sc.resolve(item).credentials == {"password": "file-secret"}


def connection_for(key):
    return connection(key, "riskledger", credentials={"api_key": "synthetic"})


def test_keen_records_written_during_a_run_leave_the_connection_behind():
    connection = connection_for("db:riskledger")
    with sc.connection_scope(connection):
        sc.connection_cache()["token"] = "connection-secret"
        assert sc.namespace("isms-link:risk:1") != "isms-link:risk:1"
        with sc.outside_connection():
            # Identifiers, storage keys and identity are Keen's own, and the
            # run's cache, with whatever the connection keeps there, is hidden.
            assert sc.current_connection() is None
            assert sc.namespace("isms-link:risk:1") == "isms-link:risk:1"
            assert sc.artifact_key("diary/entry.json") == "diary/entry.json"
            assert sc.identity() == {}
            assert sc.connection_cache() == {}
        assert sc.current_connection() is connection
        assert sc.connection_cache() == {"token": "connection-secret"}
