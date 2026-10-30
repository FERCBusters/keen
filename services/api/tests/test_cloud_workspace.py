"""Google Workspace posture and the shared Google transport, against
in-memory answers. No network is used."""

from __future__ import annotations

import base64
import json

import pytest
from app.core.config import settings
from app.ingest.cloud import google, workspace
from app.ingest.cloud.base import Unavailable

from tests.google_fakes import CUSTOMER, FakeDirectory, Service, http_error, user

KEY = {"token_uri": "https://oauth2.googleapis.com/token", "client_email": "keen@x"}


@pytest.fixture
def directory(monkeypatch):
    fake = FakeDirectory()
    made = []

    def client(api, version, info, scopes, subject=""):
        made.append((api, version, tuple(scopes), subject))
        return Service(fake)

    monkeypatch.setattr(settings, "google_workspace_impersonate", "admin@example.org")
    monkeypatch.setattr(workspace.google, "key_info", lambda *a: dict(KEY))
    monkeypatch.setattr(workspace.google, "client", client)
    fake.made = made
    return fake


def test_directory_checks_and_identities(directory):
    (result,) = workspace.collect()
    assert (result.key, result.label, result.kind) == (
        CUSTOMER,
        "example.org",
        "customer",
    )
    found = {c.key: c for c in result.checks}
    assert found["identity.mfa"].detail == "MFA off for 2 of 5 users"
    assert found["identity.mfa"].subjects == ["bob@example.org", "shared@example.org"]
    assert found["identity.mfa_enforced"].detail == "Not enforced for 2 of 5 users"
    assert found["identity.mfa_enforced"].subjects == [
        "carol@example.org",
        "shared@example.org",
    ]
    assert found["identity.admin_mfa"].detail == "MFA off for 2 of 3 administrators"
    assert found["identity.admins"].detail == "3 super or delegated administrators"
    assert (
        found["identity.inactive"].detail == "1 of 5 users unused for 45 days or more"
    )
    assert found["identity.inactive"].subjects == ["carol@example.org"]
    names = [p.name for p in result.identities]
    assert "erin@example.org" not in names and "frank@example.org" not in names
    alice = next(p for p in result.identities if p.name == "alice@example.org")
    assert alice.detail == "Super administrator" and alice.admin
    assert directory.made == [
        ("admin", "directory_v1", (workspace.DIRECTORY,), "admin@example.org")
    ]
    # The listing pages through every user and closes its client.
    assert len([c for c in directory.calls if c[0] == "users.list"]) == 3
    assert directory.services[0].closed
    assert all(kwargs["customer"] == "my_customer" for _, kwargs in directory.calls)


def test_users_of_another_customer_are_refused(directory):
    directory.users = [user("a", "1"), user("b", "2", customerId="C0other")]
    with pytest.raises(Unavailable) as caught:
        workspace.collect()
    assert caught.value.reason == "not_configured"


def test_settings_and_refusals(monkeypatch, directory):
    monkeypatch.setattr(settings, "google_workspace_impersonate", "not an email")
    with pytest.raises(Unavailable):
        workspace.collect()
    monkeypatch.setattr(settings, "google_workspace_impersonate", "admin@example.org")
    directory.fail["users.list"] = http_error(403)
    with pytest.raises(Unavailable) as caught:
        workspace.collect()
    assert caught.value.reason == "permission_denied"


@pytest.mark.parametrize(
    "info",
    [
        {"token_uri": "http://oauth2.googleapis.com/token"},
        {"token_uri": "https://user:pass@oauth2.googleapis.com/token"},
        {"token_uri": "https://oauth2.googleapis.com/token#x"},
        {},
        [],
    ],
)
def test_keys_with_unsafe_token_endpoints_are_refused(info):
    with pytest.raises(Unavailable) as caught:
        google.key_info("", "", json.dumps(info))
    assert caught.value.reason == "not_configured"


def test_keys_are_read_from_a_file_base64_or_json(tmp_path):
    text = json.dumps(KEY)
    path = tmp_path / "key.json"
    path.write_text(text)
    assert google.key_info(str(path), "", "") == KEY
    assert google.key_info("", base64.b64encode(text.encode()).decode(), "") == KEY
    assert google.key_info("", "", text) == KEY
    for keyfile, b64, raw in (
        ("", "", ""),
        (str(tmp_path / "missing.json"), "", ""),
        ("", "not base64!", ""),
        ("", "", "{not json"),
        ("", "", " " * (google.MAX_KEY_BYTES + 1)),
    ):
        with pytest.raises(Unavailable):
            google.key_info(keyfile, b64, raw)


def test_the_transport_refuses_plain_http_and_redirects():
    transport = google.HTTPSOnlyHttp()
    assert (
        transport.follow_redirects is False and transport.follow_all_redirects is False
    )
    assert transport.disable_ssl_certificate_validation is False
    assert transport.proxy_info is None
    with pytest.raises(ValueError):
        transport.request("http://admin.googleapis.com/x")


@pytest.mark.parametrize(
    "status, reason",
    [
        (401, "not_configured"),
        (403, "permission_denied"),
        (404, "not_configured"),
        (500, "unavailable"),
    ],
)
def test_failures_become_reasons(status, reason):
    class Failing:
        def execute(self, num_retries=0):
            raise http_error(status)

    with pytest.raises(Unavailable) as caught:
        google.execute(Failing())
    assert caught.value.reason == reason
    if status == 404:
        assert google.execute(Failing(), missing={}) == {}


def test_help_documents_the_posture_setting_and_scope():
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[2]
        / "ui/public/help/administrator-google-workspace.html"
    )
    text = page.read_text()
    assert "<code>KEEN_GOOGLE_WORKSPACE_POSTURE_ENABLED</code>" in text
    assert workspace.DIRECTORY in text
    for key, label in workspace.CHECKS.items():
        assert label in text and f"posture.{key}" in text, key
