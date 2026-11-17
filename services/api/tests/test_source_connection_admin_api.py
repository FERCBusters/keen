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


def _bitwarden(credentials=None, **configuration):
    return {
        "source": "bitwarden",
        "name": "Company vault",
        "configuration": {
            "base_url": "https://vault.example/api",
            "identity_url": "https://vault.example/identity",
            "client_id": "organization.3f2504e0-4f89-11d3-9a0c-0305e82c3301",
            **configuration,
        },
        "credentials": credentials or {"client_secret": "synthetic-secret"},
    }


def _mosyle(credentials=None, **configuration):
    return {
        "source": "mosyle",
        "name": "Fleet",
        "configuration": {"email": "auditor@example.org", **configuration},
        "credentials": credentials
        or {
            "password": "synthetic pass-phrase",
            "access_token": "synthetic-api-token",
        },
    }


ENDPOINT = "Use an endpoint URL without embedded credentials, query or fragment"
ONE_PASSWORD_SERVERS = (
    "Choose a 1Password Events API server: https://events.1password.com, "
    "https://events.ent.1password.com, https://events.1password.ca, "
    "https://events.1password.eu"
)
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
        "unbalanced-bracket",
        _connection(configuration={"base_url": "https://[logs.example"}),
        ENDPOINT,
    ),
    (
        "identity-embedded-user",
        _bitwarden(identity_url="https://reader@identity.example"),
        ENDPOINT,
    ),
    (
        "identity-query",
        _bitwarden(identity_url="https://identity.example/?next=1"),
        ENDPOINT,
    ),
    (
        "identity-unbalanced-bracket",
        _bitwarden(identity_url="https://[identity.example"),
        ENDPOINT,
    ),
    (
        "bitwarden-http-api",
        _bitwarden(base_url="http://vault.example/api"),
        "Bitwarden requires HTTPS API and identity URLs",
    ),
    (
        "bitwarden-http-identity",
        _bitwarden(identity_url="http://vault.example/identity"),
        "Bitwarden requires HTTPS API and identity URLs",
    ),
    (
        "bitwarden-no-identity",
        _bitwarden(identity_url=""),
        "Bitwarden requires HTTPS API and identity URLs",
    ),
    (
        "bitwarden-foreign-credential",
        _bitwarden(credentials={"token": "t"}),
        "Unsupported connection field",
    ),
    (
        "mosyle-two-mailboxes",
        _mosyle(email="one@example.org, two@example.org"),
        "Enter the email address of one Mosyle administrator",
    ),
    (
        "mosyle-display-name",
        _mosyle(email="Auditor <auditor@example.org>"),
        "Enter the email address of one Mosyle administrator",
    ),
    (
        # The servers are Mosyle's own: a connection names no endpoint.
        "mosyle-endpoint",
        _mosyle(base_url="https://businessapi.mosyle.com.attacker.example/v1"),
        "Unsupported connection field",
    ),
    (
        "mosyle-foreign-credential",
        _mosyle(credentials={"token": "t"}),
        "Unsupported connection field",
    ),
    (
        "1password-other-host",
        _connection(
            source="op_events",
            configuration={"base_url": "https://events.1password.com.attacker.example"},
        ),
        ONE_PASSWORD_SERVERS,
    ),
    (
        "1password-http",
        _connection(
            source="op_events",
            configuration={"base_url": "http://events.1password.com"},
        ),
        ONE_PASSWORD_SERVERS,
    ),
    (
        "1password-no-server",
        _connection(source="op_events", configuration={}),
        ONE_PASSWORD_SERVERS,
    ),
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


# --- Mosyle --------------------------------------------------------------------


def test_a_mosyle_connection_saves_its_sign_in_and_stream(admin):
    body = {
        **_mosyle(),
        "inputs": {"streams": [{"stream": "adminlogs", "label": "Fleet"}]},
    }

    created = admin.send("POST", ROOT, body)

    assert created.status_code == 200, created.text
    row = created.json()
    assert row["configuration"] == {"email": "auditor@example.org"}
    # The password and the access token are write-only.
    assert row["has_credentials"] is True and "synthetic" not in created.text
    assert row["inputs"] == body["inputs"]
    # A connection may be saved before its administrator is chosen.
    blank = admin.send("POST", ROOT, {**_mosyle(email=""), "name": "Later"})
    assert blank.status_code == 200, blank.text

    refused = admin.send(
        "POST", ROOT, {**_mosyle(), "inputs": {"streams": [{"stream": "devices"}]}}
    )
    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        "Mosyle collects one stream: adminlogs",
    )
    assert [c["name"] for c in admin.saved()] == ["Fleet", "Later"]


# --- Password manager event logs ---------------------------------------------


def test_password_manager_connections_save_with_their_streams(admin):
    for body in (
        {
            **_bitwarden(),
            "inputs": {"streams": [{"stream": "events", "label": "Vault"}]},
        },
        _connection(
            source="op_events",
            configuration={"base_url": "https://events.1password.eu/"},
            credentials={"token": "synthetic-token"},
            inputs={"streams": [{"stream": "signinattempts"}]},
        ),
    ):
        created = admin.send("POST", ROOT, body)
        assert created.status_code == 200, created.text
        row = created.json()
        assert row["has_credentials"] is True
        assert "synthetic" not in created.text
        assert row["inputs"] == body["inputs"]
    assert [c["source"] for c in admin.saved()] == ["bitwarden", "op_events"]


@pytest.mark.parametrize(
    "body,detail",
    [
        (
            {**_bitwarden(), "inputs": {"streams": [{"stream": "members"}]}},
            "Bitwarden collects one stream: events",
        ),
        (
            _connection(
                source="op_events",
                configuration={"base_url": "https://events.1password.com"},
                inputs={"streams": [{"stream": ["auditevents"]}]},
            ),
            "Choose a 1Password stream: auditevents, itemusages or signinattempts",
        ),
    ],
    ids=["bitwarden", "1password"],
)
def test_streams_a_password_manager_does_not_offer_are_refused(admin, body, detail):
    refused = admin.send("POST", ROOT, body)

    assert (refused.status_code, refused.json()["detail"]) == (400, detail)
    assert admin.saved() == []


@pytest.mark.parametrize(
    "change",
    [
        {"identity_url": "https://identity.attacker.example"},
        {"base_url": "https://api.attacker.example"},
    ],
    ids=["identity", "api"],
)
def test_a_saved_bitwarden_secret_never_follows_a_changed_endpoint(admin, change):
    row = admin.send("POST", ROOT, _bitwarden()).json()
    path = f"{ROOT}/{row['id']}"
    moved = _bitwarden(**change)

    # The saved secret is write-only: it is not sent to an endpoint entered
    # after it, whichever of the two endpoints changes.
    kept = admin.send("PUT", path, {**moved, "credentials": {}, "version": 1})
    assert (kept.status_code, kept.json()["detail"]) == (
        422,
        "Re-enter or clear the saved credentials when changing the endpoint, "
        "authentication mode or credential header",
    )
    (saved,) = admin.saved()
    assert saved["configuration"] == _bitwarden()["configuration"]

    # Entering the secret again, or changing only the client ID, is accepted.
    renamed = _bitwarden(client_id="organization.00000000-0000-4000-8000-000000000000")
    assert (
        admin.send(
            "PUT", path, {**renamed, "credentials": {}, "version": 1}
        ).status_code
        == 200
    )
    reentered = admin.send("PUT", path, {**moved, "version": 2})
    assert reentered.status_code == 200, reentered.text
    assert admin.saved()[0]["configuration"] == moved["configuration"]


def test_no_source_type_is_a_key_that_redaction_takes_for_a_secret():
    # A source's name is a key of its event provenance and of its run results.
    # One that reads as a secret's name would be replaced there, and with it
    # the collection item a mapping rule selects.
    from app.security.redaction import redact_obj

    from tests.source_types import FIELDS, PAUSABLE_SOURCES

    for source in sorted(set(FIELDS) | set(PAUSABLE_SOURCES)):
        pointer = {source: {"section": "streams", "stream": "events"}}
        assert redact_obj(pointer) == pointer, source
    # The property the name "onepassword" would have broken.
    assert redact_obj({"onepassword": {"stream": "events"}}) == {
        "onepassword": "REDACTED"
    }
