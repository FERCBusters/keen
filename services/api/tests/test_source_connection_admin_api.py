"""Named source connections through the assembled application.

Administrators define a connection for one source type: its endpoint and
public settings, write-only credentials and the inputs it collects. Saving
checks every field against that source type, keeps the source type fixed and
refuses endpoints that could leak credentials. Requests pass through the
production middleware, password sign-in, CSRF and the administrator check.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from app.core.config import settings
from cryptography.fernet import Fernet

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401

ROOT = "/v1/admin/source-connections"


@pytest.fixture
def admin(keen, monkeypatch):  # noqa: F811 - fixture imported above
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    client = keen.client(keen.user(role="admin"))

    def send(method, path, body=None, **params):
        return client.request(
            method,
            path,
            params=params,
            **({"json": body} if body is not None else {}),
            headers=unsafe(client),
        )

    def saved():
        listing = client.get(ROOT).json()["connections"]
        return [c for c in listing if c["managed_by"] == "database"]

    return SimpleNamespace(send=send, saved=saved, get=client.get)


def _connection(source="loki", **changes):
    return {
        "source": source,
        "name": "Production logs",
        "configuration": {"base_url": "https://logs.example"},
        **changes,
    }


ENDPOINT = "Use an endpoint URL without embedded credentials, query or fragment"
REFUSED = [
    ("unknown-type", _connection(source="ftp"), "Unknown source type"),
    (
        "foreign-setting",
        _connection(configuration={"base_url": "https://logs.example", "region": "eu"}),
        "Unsupported connection field",
    ),
    (
        "foreign-credential",
        _connection(credentials={"api_key": "k"}),
        "Unsupported connection field",
    ),
    (
        "foreign-clear",
        _connection(clear_credentials=["token"]),
        "Unsupported connection field",
    ),
    (
        "not-http",
        _connection(configuration={"base_url": "ftp://logs.example"}),
        ENDPOINT,
    ),
    ("no-host", _connection(configuration={"base_url": "https:///logs"}), ENDPOINT),
    (
        "embedded-user",
        _connection(configuration={"base_url": "https://reader@logs.example"}),
        ENDPOINT,
    ),
    (
        "embedded-password",
        _connection(configuration={"base_url": "https://:pw@logs.example"}),
        ENDPOINT,
    ),
    (
        "query",
        _connection(configuration={"base_url": "https://logs.example/?token=1"}),
        ENDPOINT,
    ),
    (
        "fragment",
        _connection(configuration={"base_url": "https://logs.example/#top"}),
        ENDPOINT,
    ),
    (
        "elasticsearch-http",
        _connection(
            source="elasticsearch", configuration={"base_url": "http://search.example"}
        ),
        "Elasticsearch requires an HTTPS endpoint",
    ),
    (
        "elasticsearch-no-endpoint",
        _connection(source="elasticsearch", configuration={}),
        "Elasticsearch requires an HTTPS endpoint",
    ),
    (
        "unknown-auth-mode",
        _connection(
            source="forgejo",
            configuration={"base_url": "https://git.example", "auth_mode": "kerberos"},
        ),
        "Choose a supported authentication mode",
    ),
    (
        "bad-header",
        _connection(
            configuration={"base_url": "https://logs.example", "header_name": "X Token"}
        ),
        "Enter a valid HTTP header name",
    ),
    (
        "bad-secret-header",
        _connection(
            source="webhooks",
            configuration={"provider": "github", "secret_header": "X-Sig:1"},
        ),
        "Enter a valid HTTP header name",
    ),
    (
        "webhook-provider-spaces",
        _connection(source="webhooks", configuration={"provider": "git hub"}),
        "Enter a webhook provider name using letters, numbers, hyphens or underscores",
    ),
    (
        "webhook-no-provider",
        _connection(source="webhooks", configuration={}),
        "Enter a webhook provider name using letters, numbers, hyphens or underscores",
    ),
    ("blank-name", _connection(name="   "), "A connection name is required"),
    (
        "imap-password",
        _connection(
            source="imap",
            configuration={"host": "mail.example", "username": "evidence"},
            credentials={"password": "pässword"},
        ),
        "IMAP password must use printable ASCII characters, up to 4096",
    ),
    (
        "imap-inputs",
        _connection(
            source="imap",
            configuration={"host": "mail.example", "username": "evidence"},
            inputs={"folders": [{"name": "Archive"}]},
        ),
        "IMAP uses one folder per connection; extra inputs are unsupported",
    ),
]


@pytest.mark.parametrize(
    "body,detail",
    [(body, detail) for _, body, detail in REFUSED],
    ids=[name for name, _, _ in REFUSED],
)
def test_settings_outside_the_source_type_are_refused(admin, body, detail):
    refused = admin.send("POST", ROOT, body)

    assert (refused.status_code, refused.json()["detail"]) == (422, detail)
    assert admin.saved() == []


def test_a_connection_keeps_its_source_type_and_version(admin):
    created = admin.send("POST", ROOT, _connection())
    assert created.status_code == 200, created.text
    row = created.json()
    path = f"{ROOT}/{row['id']}"

    retyped = admin.send(
        "PUT", path, {**_connection(source="github"), "version": row["version"]}
    )
    assert (retyped.status_code, retyped.json()["detail"]) == (
        422,
        "The source type of a connection is fixed",
    )
    stale = admin.send(
        "PUT", path, {**_connection(name="Renamed"), "version": row["version"] - 1}
    )
    assert (stale.status_code, stale.json()["detail"]) == (
        409,
        "Connection changed; reload before saving",
    )
    missing = admin.send(
        "PUT", f"{ROOT}/no-such-connection", {**_connection(), "version": 1}
    )
    assert (missing.status_code, missing.json()["detail"]) == (
        404,
        "Connection not found",
    )
    assert [c["name"] for c in admin.saved()] == ["Production logs"]


def test_collection_runs_only_for_enabled_polling_connections(admin, monkeypatch):
    from app.worker import tasks

    queued = []
    monkeypatch.setattr(
        tasks.ingest_source_connection_task,
        "delay",
        lambda source, key: (
            queued.append((source, key)) or type("Task", (), {"id": "task-1"})()
        ),
    )
    monkeypatch.setattr(settings, "loki_enabled", True)
    monkeypatch.setattr(settings, "webhooks_enabled", True)
    logs = admin.send("POST", ROOT, _connection()).json()
    paused = admin.send(
        "POST", ROOT, _connection(name="Paused logs", enabled=False)
    ).json()
    hooks = admin.send(
        "POST",
        ROOT,
        _connection(source="webhooks", configuration={"provider": "github"}),
    ).json()

    started = admin.send("POST", f"{ROOT}/{logs['id']}/run")
    assert (started.status_code, started.json()) == (200, {"task_id": "task-1"})
    assert queued == [("loki", logs["id"])]

    for key, status, detail in [
        (hooks["id"], 422, "Webhooks receive events at their callback URL"),
        (paused["id"], 409, "Connection or source type is disabled"),
        ("no-such-connection", 404, "Connection not found"),
    ]:
        refused = admin.send("POST", f"{ROOT}/{key}/run")
        assert (refused.status_code, refused.json()["detail"]) == (status, detail), key
    # A connection whose source type the deployment turns off does not run.
    monkeypatch.setattr(settings, "loki_enabled", False)
    off = admin.send("POST", f"{ROOT}/{logs['id']}/run")
    assert (off.status_code, off.json()["detail"]) == (
        409,
        "Connection or source type is disabled",
    )
    assert queued == [("loki", logs["id"])]


def test_credentials_need_the_integration_secret_key(admin, monkeypatch):
    monkeypatch.delenv("KEEN_INTEGRATION_SECRET_KEY")
    refused = admin.send("POST", ROOT, _connection(credentials={"password": "s3cret"}))
    assert (refused.status_code, refused.json()["detail"]) == (
        503,
        "Set KEEN_INTEGRATION_SECRET_KEY on API and worker before storing credentials",
    )
    assert admin.saved() == []

    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    row = admin.send(
        "POST", ROOT, _connection(credentials={"password": "s3cret"})
    ).json()
    assert row["has_credentials"] is True
    assert "s3cret" not in str(row)

    # Credentials sealed with another key cannot be changed until the key matches.
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    rekeyed = admin.send(
        "PUT",
        f"{ROOT}/{row['id']}",
        {**_connection(name="Renamed"), "version": row["version"]},
    )
    assert (rekeyed.status_code, rekeyed.json()["detail"]) == (
        503,
        "Check KEEN_INTEGRATION_SECRET_KEY on API and worker before changing credentials",
    )
    assert [c["name"] for c in admin.saved()] == ["Production logs"]


def test_a_demo_lists_connections_but_cannot_change_or_run_them(admin, monkeypatch):
    row = admin.send("POST", ROOT, _connection()).json()
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(
        settings, "demo_expires_at", datetime.now(timezone.utc) + timedelta(hours=1)
    )

    assert admin.get(ROOT).status_code == 200
    message = (
        "You are running in a demo environment. "
        "New API integrations cannot be configured or run here."
    )
    for method, path, body in [
        ("POST", ROOT, _connection(name="Another")),
        (
            "PUT",
            f"{ROOT}/{row['id']}",
            {**_connection(name="Renamed"), "version": row["version"]},
        ),
        ("POST", f"{ROOT}/{row['id']}/run", None),
    ]:
        refused = admin.send(method, path, body)
        assert (refused.status_code, refused.json()["detail"]) == (403, message), path
    deleted = admin.send("DELETE", f"{ROOT}/{row['id']}", version=row["version"])
    assert (deleted.status_code, deleted.json()["detail"]) == (403, message)
    assert [c["name"] for c in admin.saved()] == ["Production logs"]
