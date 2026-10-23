"""Custom API integrations through the assembled application.

Administrators create connections and collectors, publish revisions and queue
runs. Requests pass the production middleware, sign-in, CSRF and the
administrator and demo-mode guards; a list records each broker hand-off.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.db.models import IntegrationCollector, IsmsEffectivenessMeasure
from app.services import integrations as integration_operations
from cryptography.fernet import Fernet

# Imported fixture: the assembled application with an in-memory session store.
from tests.test_assembled_authority_regressions import (
    keen,  # noqa: F401
    unsafe,
)

DEFINITION = {"path": "/v1/builds", "records_path": "/items"}
MEASUREMENT_FIELDS = {
    "id": {"path": "/id"},
    "timestamp": {"path": "/at"},
    "summary": {"path": "/name"},
    "value": {"path": "/value", "transform": "number"},
    "period_start": {"path": "/from"},
    "period_end": {"path": "/to"},
}


@pytest.fixture
def api(keen, monkeypatch):  # noqa: F811 - fixture imported above
    dispatched = []
    monkeypatch.setattr(integration_operations, "dispatch", dispatched.append)
    admin = keen.client(keen.user(role="admin"))

    def call(method, path, body=None, status=200):
        response = admin.request(
            method,
            "/v1/admin/integrations" + path,
            json=body,
            headers=unsafe(admin),
        )
        assert response.status_code == status, response.text
        return response.json()

    connection = call(
        "POST",
        "/connections",
        {"name": "Build server", "base_url": "https://builds.example"},
    )
    return SimpleNamespace(
        keen=keen,
        admin=admin,
        call=call,
        connection=connection,
        dispatched=dispatched,
    )


def collector(api, definition=DEFINITION, status=200, **changes):
    body = {
        "name": "Nightly builds",
        "connection_id": api.connection["id"],
        "definition": definition,
        **changes,
    }
    return api.call("POST", "/collectors", body, status=status)


def test_a_collector_is_saved_published_and_run(api):
    created = collector(api)
    key = created["id"]
    assert (created["version"], created["live_revision"], created["enabled"]) == (
        1,
        None,
        False,
    )
    assert created["draft"]["path"] == "/v1/builds"

    saved = api.call(
        "PUT",
        f"/collectors/{key}",
        {
            "name": "Nightly builds",
            "connection_id": api.connection["id"],
            "definition": {**DEFINITION, "path": "/v2/builds"},
            "version": 1,
        },
    )
    assert saved["version"] == 2
    # An edit based on the earlier version is refused.
    stale = api.call(
        "PUT",
        f"/collectors/{key}",
        {
            "name": "Overwritten",
            "connection_id": api.connection["id"],
            "definition": DEFINITION,
            "version": 1,
        },
        status=409,
    )
    assert stale["detail"] == "This item changed. Reload before saving."
    assert api.call("GET", f"/collectors/{key}/revisions") == {"items": []}
    # Running before anything is published is refused.
    unpublished = api.call("POST", f"/collectors/{key}/run", {"version": 2}, 409)
    assert unpublished["detail"] == "Publish a revision before collecting evidence"

    published = api.call("POST", f"/collectors/{key}/publish", {"version": 2})
    assert (published["live_revision"], published["enabled"]) == (2, True)
    (revision,) = api.call("GET", f"/collectors/{key}/revisions")["items"]
    assert (revision["revision"], revision["connection_id"]) == (
        2,
        api.connection["id"],
    )
    assert revision["definition"]["path"] == "/v2/builds"

    run = api.call("POST", f"/collectors/{key}/run", {"version": 2})
    assert (run["collector_id"], run["revision"], run["status"]) == (key, 2, "queued")
    assert api.dispatched == [run["id"]]
    assert api.call("GET", f"/runs/{run['id']}") == run
    busy = api.call("POST", f"/collectors/{key}/run", {"version": 2}, 409)
    assert busy["detail"] == "This integration already has a queued or running job"
    assert api.dispatched == [run["id"]]

    overview = api.call("GET", "")
    assert [c["id"] for c in overview["connections"]] == [api.connection["id"]]
    assert overview["connections"][0]["has_secret"] is False
    assert "encrypted_secret" not in overview["connections"][0]
    assert [(c["id"], c["live_revision"]) for c in overview["collectors"]] == [(key, 2)]
    assert [r["id"] for r in overview["runs"]] == [run["id"]]
    assert overview["templates"] and overview["demo_mode"] is False

    for path in (f"/runs/{key}", "/collectors/unknown/revisions"):
        assert api.call("GET", path, status=404)["detail"] == "Not found"
    unknown = api.call("POST", f"/collectors/{key}/explode", {"version": 2}, 404)
    assert unknown["detail"] == "Unknown action"


@pytest.mark.parametrize(
    "definition",
    [
        {**DEFINITION, "headers": {"X-Api-Token": "synthetic"}},
        {**DEFINITION, "query": {"api_key": "synthetic"}},
        {**DEFINITION, "method": "POST", "body": {"auth": {"client_secret": "x"}}},
        {**DEFINITION, "body": {"items": [{"password": "synthetic"}]}},
    ],
    ids=["header", "query", "nested-body", "listed-body"],
)
def test_definitions_never_hold_credentials(api, definition):
    refused = collector(api, definition, status=422)

    assert refused["detail"] == "Move credentials from the definition to a Connection"
    assert api.keen.db.query(IntegrationCollector).count() == 0


def test_collector_definitions_are_bounded_and_need_a_real_connection(api):
    oversized = collector(api, {**DEFINITION, "query": {"q": "x" * 70000}}, 422)
    assert oversized["detail"] == "Definition exceeds 64 KiB"
    missing = collector(api, connection_id="no-such-connection", status=404)
    assert missing["detail"] == "Not found"
    assert api.keen.db.query(IntegrationCollector).count() == 0


def test_measurement_collectors_need_an_existing_measure_and_its_unit(api):
    measure = IsmsEffectivenessMeasure(
        framework_slug="fw", summary="Patch latency", target_unit="days"
    )
    api.keen.db.add(measure)
    api.keen.db.commit()

    def measuring(measure_id, unit):
        return {
            **DEFINITION,
            "output": "measurement",
            "measure_id": measure_id,
            "unit": unit,
            "fields": MEASUREMENT_FIELDS,
        }

    for measure_id, unit in (
        (str(measure.id), "hours"),
        ("00000000-0000-0000-0000-000000000000", "days"),
        ("not-a-uuid", "days"),
    ):
        refused = collector(api, measuring(measure_id, unit), status=422)
        assert refused["detail"] == (
            "Select an existing measure and use its exact target unit"
        )
    created = collector(api, measuring(str(measure.id), "days"))
    assert created["draft"]["output"] == "measurement"
    (listed,) = api.call("GET", "")["measures"]
    assert listed == {
        "id": str(measure.id),
        "name": "Patch latency",
        "framework": "fw",
        "unit": "days",
    }


def test_only_administrators_reach_integrations(api, monkeypatch):
    lab = api.keen
    reader = lab.client(lab.user("events.read", "isms.manage", "audits.admin"))
    anonymous = lab.client()
    requests = [
        ("GET", ""),
        ("GET", "/runs/any"),
        ("GET", "/collectors/any/revisions"),
        ("POST", "/collectors"),
        ("POST", "/collectors/any/run"),
    ]
    for method, path in requests:
        url = "/v1/admin/integrations" + path
        assert (
            reader.request(method, url, json={}, headers=unsafe(reader)).status_code
            == 403
        )
        assert anonymous.request(method, url, json={}).status_code == 401
    # Administrator writes still need the CSRF token.
    body = {"name": "x", "connection_id": api.connection["id"], "definition": {}}
    refused = api.admin.post("/v1/admin/integrations/collectors", json=body)
    assert refused.status_code == 403 and "CSRF" in refused.text

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(
        settings, "demo_expires_at", datetime(2999, 1, 1, tzinfo=timezone.utc)
    )
    assert api.call("GET", "")["demo_mode"] is True
    demo = api.call("POST", "/collectors", {**body, "definition": DEFINITION}, 403)
    assert "demo environment" in demo["detail"]
    assert lab.db.query(IntegrationCollector).count() == 0


CONNECTION = {"name": "Checks", "base_url": "https://checks.example"}


@pytest.mark.parametrize(
    "changes,detail",
    [
        ({"base_url": "http://checks.example"}, "Base URL must use HTTPS"),
        ({"base_url": "https://user:pw@checks.example"}, "Base URL must use HTTPS"),
        ({"base_url": "https://checks.example/?token=1"}, "Base URL must use HTTPS"),
        ({"base_url": "https://checks.example/#top"}, "Base URL must use HTTPS"),
        ({"auth_kind": "kerberos", "secret": "s3"}, "Unsupported authentication type"),
        (
            {"auth_kind": "header", "auth_name": "Cookie", "secret": "s3"},
            "Provide a valid authentication parameter/header name",
        ),
        (
            {"auth_kind": "query", "auth_name": "api key", "secret": "s3"},
            "Provide a valid authentication parameter/header name",
        ),
        ({"auth_options": {"audience": "x"}}, "Unknown authentication option"),
        (
            {"auth_options": {"token_path": "https://elsewhere.example/token"}},
            "OAuth token path must be relative to the connection",
        ),
        (
            {"auth_options": {"token_path": "/token?grant=1"}},
            "OAuth token path must be relative to the connection",
        ),
        ({"auth_kind": "bearer"}, "A credential is required"),
        (
            {"auth_kind": "bearer", "secret": "line\nbreak"},
            "Credential must not contain newlines",
        ),
    ],
)
def test_connection_settings_are_validated(api, monkeypatch, changes, detail):
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())

    refused = api.call("POST", "/connections", {**CONNECTION, **changes}, 422)

    assert refused["detail"].startswith(detail)
    names = [c["name"] for c in api.call("GET", "")["connections"]]
    assert "Checks" not in names


def test_saved_credentials_never_follow_an_edit_elsewhere(api, monkeypatch):
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    saved = api.call(
        "POST",
        "/connections",
        {**CONNECTION, "auth_kind": "header", "auth_name": "X-Token", "secret": "s3"},
    )
    key = saved["id"]
    assert (saved["has_secret"], saved["version"]) == (True, 1)
    current = {**CONNECTION, "auth_kind": "header", "auth_name": "X-Token"}

    def edit(status=200, **changes):
        version = api.call("GET", "")["connections"]
        version = next(c["version"] for c in version if c["id"] == key)
        body = {**current, **changes, "version": version}
        return api.call("PUT", f"/connections/{key}", body, status)

    redirect = "Supply a new credential when changing the connection origin"
    for changes in (
        {"base_url": "https://collector.attacker.example"},
        {"base_url": "https://checks.example:8443"},
        {"auth_name": "X-Other"},
    ):
        assert edit(422, **changes)["detail"].startswith(redirect)
    assert edit(422, auth_kind="bearer")["detail"] == (
        "Supply a new credential when changing authentication type"
    )
    # Renaming keeps the saved credential; a new origin needs a new one.
    assert edit(name="Renamed checks")["has_secret"] is True
    current["name"] = "Renamed checks"
    moved = edit(base_url="https://new-checks.example", secret="new-s3")
    assert (moved["base_url"], moved["has_secret"]) == (
        "https://new-checks.example",
        True,
    )
    current["base_url"] = "https://new-checks.example"
    cleared = edit(auth_kind="none", auth_name="")
    assert cleared["has_secret"] is False
    stale = api.call("PUT", f"/connections/{key}", {**current, "version": 1}, 409)
    assert stale["detail"] == "This item changed. Reload before saving."


def test_live_revisions_change_only_between_runs(api):
    key = collector(api)["id"]
    published = api.call("POST", f"/collectors/{key}/publish", {"version": 1})
    run = api.call("POST", f"/collectors/{key}/run", {"version": 1})
    for action in ("publish", "rollback", "reset-cursor"):
        busy = api.call(
            "POST", f"/collectors/{key}/{action}", {"version": 1, "revision": 1}, 409
        )
        assert busy["detail"] == (
            "Wait for the active run before changing the live revision or cursor"
        )
    assert published["live_revision"] == 1 and run["status"] == "queued"


def test_rollback_pause_resume_and_cursor_reset(api):
    key = collector(api)["id"]
    unpublished = api.call("POST", f"/collectors/{key}/resume", {"version": 1}, 422)
    assert unpublished["detail"] == "Publish first"
    api.call("POST", f"/collectors/{key}/publish", {"version": 1})
    api.call(
        "PUT",
        f"/collectors/{key}",
        {
            "name": "Nightly builds",
            "connection_id": api.connection["id"],
            "definition": {**DEFINITION, "path": "/v2/builds"},
            "version": 1,
        },
    )
    missing = api.call(
        "POST", f"/collectors/{key}/rollback", {"version": 2, "revision": 9}, 404
    )
    assert missing["detail"] == "Revision not found"

    restored = api.call(
        "POST", f"/collectors/{key}/rollback", {"version": 2, "revision": 1}
    )

    assert (restored["version"], restored["live_revision"]) == (3, 1)
    assert restored["draft"]["path"] == "/v1/builds"
    paused = api.call("POST", f"/collectors/{key}/pause", {"version": 3})
    assert paused["enabled"] is False
    row = api.keen.db.get(IntegrationCollector, key)
    row.failures, row.cursor = 3, "2026-01-01T00:00:00+00:00"
    api.keen.db.commit()
    exhausted = api.call("POST", f"/collectors/{key}/run", {"version": 3}, 409)
    assert exhausted["detail"].startswith("Review the failure and explicitly resume")
    resumed = api.call("POST", f"/collectors/{key}/resume", {"version": 3})
    assert (resumed["enabled"], resumed["failures"]) == (True, 0)
    reset = api.call("POST", f"/collectors/{key}/reset-cursor", {"version": 3})
    assert reset["cursor"] is None


def test_a_run_that_cannot_reach_the_broker_is_marked_failed(api, monkeypatch):
    key = collector(api)["id"]
    api.call("POST", f"/collectors/{key}/publish", {"version": 1})

    def unavailable(run_id):
        raise ConnectionError("broker down")

    monkeypatch.setattr(integration_operations, "dispatch", unavailable)

    run = api.call("POST", f"/collectors/{key}/run", {"version": 1})

    assert (run["status"], run["message"]) == (
        "failed",
        "Could not queue run; check broker health.",
    )
    assert api.keen.db.get(IntegrationCollector, key).failures == 1


def test_pasted_samples_are_mapped_without_any_request(api):
    definition = {
        **DEFINITION,
        "fields": {
            "id": {"path": "/id"},
            "timestamp": {"path": "/at"},
            "summary": {"path": "/name"},
        },
    }
    sample = {
        "items": [
            {"id": 1, "at": "2026-01-01T12:00:00Z", "name": "Build 1", "password": "p"}
        ]
    }

    mapped = api.call(
        "POST", "/preview-sample", {"definition": definition, "response": sample}
    )

    (row,) = mapped["result"]["samples"]
    assert row["evidence"]["summary"] == "Build 1"
    assert row["evidence"]["source"] == "integration:draft"
    assert row["evidence"]["normalized_payload"]["password"] == "REDACTED"
    assert api.dispatched == []
    for body, status, detail in (
        (
            {"definition": definition, "response": {"items": {}}},
            422,
            "Records path must select a JSON array",
        ),
        (
            {"definition": definition, "response": {"items": [{"id": 1}]}},
            422,
            "Sample could not be mapped",
        ),
        (
            {"definition": definition, "response": {"items": ["x" * 1024 * 1024]}},
            422,
            "Sample response exceeds 1 MiB",
        ),
        (
            {"definition": definition, "response": sample, "collector_id": "missing"},
            404,
            "Not found",
        ),
    ):
        assert api.call("POST", "/preview-sample", body, status)["detail"].startswith(
            detail
        )
