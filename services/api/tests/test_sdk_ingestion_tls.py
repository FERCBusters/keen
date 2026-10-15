"""Operator SDK endpoint overrides must not permit plaintext authentication."""

import base64
import json
from unittest.mock import Mock

import pytest
from app.core.config import settings
from app.ingest import cloudwatch_logs, google_workspace


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.test",
        "https://user:pass@example.test",
        "https://example.test/#fragment",
    ],
)
def test_cloudwatch_rejects_unsafe_sdk_endpoint(monkeypatch, endpoint):
    client = Mock()
    client.meta.endpoint_url = endpoint
    monkeypatch.setattr(cloudwatch_logs.boto3, "client", Mock(return_value=client))
    with pytest.raises(ValueError, match="HTTPS"):
        cloudwatch_logs._cloudwatch_client("eu-west-1")
    client.close.assert_called_once()
    client.filter_log_events.assert_not_called()


def test_real_cloudwatch_sdk_http_override_rejected_before_request(monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://example.test")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-secret")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    import boto3

    monkeypatch.setattr(boto3, "DEFAULT_SESSION", None)
    with pytest.raises(ValueError, match="HTTPS"):
        cloudwatch_logs._cloudwatch_client("eu-west-1")


@pytest.mark.parametrize("location", ["file", "json", "base64"])
@pytest.mark.parametrize("scheme", ["http", "https"])
def test_google_credential_inputs_check_token_transport_before_key_loading(
    monkeypatch, tmp_path, location, scheme
):
    info = {"token_uri": f"{scheme}://oauth2.googleapis.com/token"}
    raw = json.dumps(info)
    path = tmp_path / "synthetic.json"
    path.write_text(raw)
    monkeypatch.setattr(
        settings, "google_workspace_sa_keyfile", str(path) if location == "file" else ""
    )
    monkeypatch.setattr(
        settings, "google_workspace_sa_json", raw if location == "json" else ""
    )
    monkeypatch.setattr(
        settings,
        "google_workspace_sa_json_b64",
        base64.b64encode(raw.encode()).decode() if location == "base64" else "",
    )
    factory = Mock()
    monkeypatch.setattr(
        google_workspace.service_account.Credentials,
        "from_service_account_info",
        factory,
    )
    if scheme == "http":
        with pytest.raises(ValueError, match="HTTPS"):
            google_workspace._load_credentials("")
        factory.assert_not_called()
    else:
        assert google_workspace._load_credentials("") == factory.return_value
        factory.assert_called_once_with(info, scopes=google_workspace.SCOPES)
