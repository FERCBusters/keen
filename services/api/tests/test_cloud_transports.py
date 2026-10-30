"""Shared cloud posture transports: the JSON client bound to one origin and the
HTTPS-only Google client. No network is used."""

from __future__ import annotations

import base64
import json

import httplib2
import httpx
import pytest
from app.ingest import cloud
from app.ingest.cloud import api, base, demo, google
from app.ingest.cloud.base import Unavailable
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth.exceptions import RefreshError

ORIGIN = "https://api.example.org"


def json_api(handler) -> api.JsonApi:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return api.JsonApi(ORIGIN, {"Authorization": "Bearer synthetic"}, client=client)


@pytest.mark.parametrize(
    "status, reason",
    [
        (401, "not_configured"),
        (403, "permission_denied"),
        (400, "not_configured"),
        (404, "not_configured"),
        (500, "unavailable"),
    ],
)
def test_statuses_become_reasons(status, reason):
    assert api.reason(httpx.Response(status)) == reason


def test_transport_failures_and_bad_bodies_are_unavailable():
    def refuse(request):
        raise httpx.ConnectError("token=synthetic", request=request)

    with pytest.raises(Unavailable) as caught:
        json_api(refuse).get("/x")
    assert caught.value.reason == "unavailable" and "synthetic" not in str(caught.value)
    for answer in (
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json=["a list"]),
    ):
        with pytest.raises(Unavailable):
            json_api(lambda request, answer=answer: answer).get("/x")
    with pytest.raises(Unavailable):
        api.items({"value": "not a list"}, "value")


def test_an_unreadable_retry_after_waits_one_second(monkeypatch):
    waits = []
    monkeypatch.setattr(api.time, "sleep", waits.append)
    answers = [
        httpx.Response(429, headers={"Retry-After": "soon"}),
        httpx.Response(200, json={"ok": True}),
    ]
    session = json_api(lambda request: answers.pop(0))
    assert session.get("/x") == {"ok": True}
    assert waits == [1.0]


def test_posts_send_json_and_close_clears_credentials():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    with json_api(handler) as session:
        session.post("/search", {"query": "x"})
        assert json.loads(seen[0].content) == {"query": "x"}
    assert session._headers == {}


def test_unknown_providers_are_refused():
    with pytest.raises(KeyError):
        cloud.provider("telepathy")


def test_mfa_on_for_everyone_is_met():
    people = [base.Identity("a", mfa=True), base.Identity("b", mfa=True)]
    check = base.mfa_check("identity.mfa", "MFA", people)
    assert (check.status, check.detail) == ("met", "MFA on for 2 of 2 users")


def test_providers_without_demo_activity_answer_nothing():
    assert demo.activity(
        "google_workspace", demo.utc_now_naive(), demo.utc_now_naive()
    ) == ([], True)


# --- Google ------------------------------------------------------------------------


def service_account_info(token_uri="https://oauth2.googleapis.com/token"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return {
        "type": "service_account",
        "client_email": "keen@example.iam.gserviceaccount.com",
        "private_key": pem,
        "private_key_id": "synthetic",
        "token_uri": token_uri,
    }


def test_google_requests_must_be_https(monkeypatch):
    sent = []
    monkeypatch.setattr(
        httplib2.Http, "request", lambda self, uri, *a, **k: sent.append(uri) or "ok"
    )
    http = google.HTTPSOnlyHttp()
    assert http.follow_redirects is False
    assert http.request("https://admin.googleapis.com/x") == "ok"
    with pytest.raises(ValueError):
        http.request("http://admin.googleapis.com/x")
    assert sent == ["https://admin.googleapis.com/x"]


def test_google_keys_are_bounded_and_need_an_https_token_endpoint(tmp_path):
    info = {"token_uri": "https://oauth2.googleapis.com/token"}
    text = json.dumps(info)
    assert google.key_info("", "", text) == info
    assert google.key_info("", base64.b64encode(text.encode()).decode(), "") == info
    keyfile = tmp_path / "key.json"
    keyfile.write_text(text)
    assert google.key_info(str(keyfile), "", "") == info
    big = tmp_path / "big.json"
    big.write_text(" " * (google.MAX_KEY_BYTES + 1))
    for args in (
        (str(big), "", ""),
        ("", "A" * (google.MAX_KEY_BYTES * 2 + 4), ""),
        ("", "", " " * (google.MAX_KEY_BYTES + 1)),
        ("", "", ""),
        ("", "not base64!", ""),
        (str(tmp_path / "missing.json"), "", ""),
        ("", "", json.dumps({"token_uri": "http://oauth2.googleapis.com/token"})),
        ("", "", "[]"),
    ):
        with pytest.raises(Unavailable) as caught:
            google.key_info(*args)
        assert caught.value.reason == "not_configured"


def test_google_clients_hold_only_their_scopes_and_subject():
    info = service_account_info()
    service = google.client(
        "admin", "directory_v1", info, ["scope-a"], "admin@example.org"
    )
    credentials = service._http.credentials
    assert credentials.scopes == ["scope-a"]
    assert credentials._subject == "admin@example.org"
    assert isinstance(service._http.http, google.HTTPSOnlyHttp)
    service.close()
    unattended = google.client("cloudasset", "v1", info, ["scope-b"])
    assert unattended._http.credentials._subject is None
    unattended.close()
    with pytest.raises(Unavailable):
        google.client("admin", "directory_v1", {"token_uri": "http://x"}, [])
    with pytest.raises(Unavailable):
        google.client("admin", "directory_v1", {"token_uri": info["token_uri"]}, [])


def test_google_failures_are_reasons():
    assert google.reason(RefreshError("unauthorized_client")) == "permission_denied"

    class Failing:
        def __init__(self, exc):
            self.exc = exc

        def execute(self, num_retries=0):
            raise self.exc

    for exc in (OSError("reset"), ValueError("bad"), httplib2.HttpLib2Error("x")):
        with pytest.raises(Unavailable) as caught:
            google.execute(Failing(exc))
        assert caught.value.reason == "unavailable"
