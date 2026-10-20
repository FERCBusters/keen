"""The custom-integration HTTPS client: destinations, credentials and limits.

Requests go only to allow-listed HTTPS origins at validated addresses and
carry the connection's credential in the form its connection names. A
recording connection object stands in for the pinned TLS socket.
"""

import base64
import copy
import socket
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import pytest
from app.integrations import transport
from app.integrations.errors import IntegrationError
from cryptography.fernet import Fernet


class FakeResponse:
    def __init__(self, status=200, body=b'{"ok": true}', headers=None):
        self.status = status
        self.chunks = [body] if body else []
        self.headers = {"Content-Type": "application/json", **(headers or {})}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def getheaders(self):
        return list(self.headers.items())

    def read1(self, size):
        return self.chunks.pop(0) if self.chunks else b""


@pytest.fixture
def upstream(monkeypatch):
    monkeypatch.setenv(
        # Blank entries in the list are ignored.
        "KEEN_INTEGRATION_ALLOWED_HOSTS",
        "api.example.org,, ci.example.org:8443",
    )
    monkeypatch.delenv("KEEN_INTEGRATION_PRIVATE_CIDRS", raising=False)
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
    )
    state = SimpleNamespace(response=FakeResponse(), sent=[], opened=[])

    class Connection:
        def __init__(self, hostname, port, address, timeout):
            state.opened.append((hostname, port, address, timeout))
            self.sock = None

        def request(self, method, target, payload, headers):
            state.sent.append(
                SimpleNamespace(
                    method=method, target=target, payload=payload, headers=headers
                )
            )

        def getresponse(self):
            # Each request reads its own copy of the configured response.
            return copy.deepcopy(state.response)

        def close(self):
            state.closed = True

    monkeypatch.setattr(transport, "PinnedHTTPS", Connection)
    return state


def sent_query(request):
    return parse_qsl(urlsplit(request.target).query)


@pytest.mark.parametrize(
    "auth,header,value",
    [
        ({"kind": "bearer", "secret": "s3"}, "Authorization", "Bearer s3"),
        (
            {"kind": "basic", "username": "reader", "secret": "s3"},
            "Authorization",
            "Basic " + base64.b64encode(b"reader:s3").decode(),
        ),
        ({"kind": "header", "name": "X-Token", "secret": "s3"}, "X-Token", "s3"),
        ({"kind": "header", "secret": "s3"}, "X-API-Key", "s3"),
    ],
    ids=["bearer", "basic", "named-header", "default-header"],
)
def test_credentials_travel_in_the_form_the_connection_names(
    upstream, auth, header, value
):
    payload, headers = transport.request(
        "https://api.example.org/v1/checks", auth=auth, headers={"X-Trace": "1"}
    )

    (request,) = upstream.sent
    assert request.headers[header] == value
    assert request.headers["X-Trace"] == "1"
    assert request.headers["Accept-Encoding"] == "identity"
    assert (payload, headers["Content-Type"]) == ({"ok": True}, "application/json")
    assert upstream.opened == [("api.example.org", 443, "93.184.216.34", 20)]
    assert upstream.closed is True


def test_query_credentials_join_the_query_string(upstream):
    transport.request(
        "https://api.example.org/v1/checks?status=all",
        query={"page": 2},
        auth={"kind": "query", "name": "token", "secret": "s3"},
    )

    (request,) = upstream.sent
    assert urlsplit(request.target).path == "/v1/checks"
    assert sent_query(request) == [("status", "all"), ("page", "2"), ("token", "s3")]
    assert "Authorization" not in request.headers


def test_json_and_form_bodies_declare_their_content_type(upstream):
    transport.request("https://api.example.org/search", method="POST", body={"q": "x"})
    transport.request(
        "https://api.example.org/token",
        method="POST",
        form={"grant_type": "client_credentials"},
    )

    json_request, form_request = upstream.sent
    assert (json_request.payload, json_request.headers["Content-Type"]) == (
        b'{"q": "x"}',
        "application/json",
    )
    assert (form_request.payload, form_request.headers["Content-Type"]) == (
        b"grant_type=client_credentials",
        "application/x-www-form-urlencoded",
    )


@pytest.mark.parametrize(
    "response,message",
    [
        (FakeResponse(status=500, body=b"secret upstream detail"), "HTTP 500"),
        (FakeResponse(headers={"Content-Encoding": "gzip"}), "Compressed responses"),
    ],
)
def test_unusable_responses_are_refused_without_their_body(upstream, response, message):
    upstream.response = response

    with pytest.raises(IntegrationError, match=message) as error:
        transport.request("https://api.example.org/v1/checks")

    assert "secret upstream detail" not in str(error.value)
    assert upstream.closed is True


def test_a_response_that_outlasts_its_budget_is_abandoned(upstream, monkeypatch):
    clock = iter([0, 25])
    monkeypatch.setattr(transport.time, "monotonic", lambda: next(clock))

    with pytest.raises(TimeoutError, match="time budget"):
        transport.request("https://api.example.org/v1/checks", timeout=20)
    assert upstream.closed is True


def test_ports_are_part_of_the_allowed_origin(upstream):
    transport.request("https://ci.example.org:8443/api/json")
    assert upstream.opened[0][:2] == ("ci.example.org", 8443)

    with pytest.raises(IntegrationError, match="not in KEEN_INTEGRATION_ALLOWED_HOSTS"):
        transport.request("https://ci.example.org/api/json")
    assert len(upstream.sent) == 1


@pytest.mark.parametrize(
    "allowed",
    ["api.example.org/path", "user@api.example.org", "api.example.org?x=1", "[::1"],
)
def test_a_malformed_allowlist_refuses_every_destination(monkeypatch, allowed):
    monkeypatch.setenv("KEEN_INTEGRATION_ALLOWED_HOSTS", "api.example.org," + allowed)

    with pytest.raises(IntegrationError, match="host allowlist"):
        transport.destination("https://api.example.org/v1")


def test_a_destination_without_addresses_is_refused(upstream, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])

    with pytest.raises(IntegrationError, match="no addresses"):
        transport.destination("https://api.example.org/v1")


def test_credentials_need_the_integration_secret_key(monkeypatch):
    monkeypatch.delenv("KEEN_INTEGRATION_SECRET_KEY", raising=False)
    with pytest.raises(IntegrationError, match="KEEN_INTEGRATION_SECRET_KEY"):
        transport.encrypt({"secret": "s3"})

    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    # A secret long enough that its appearance in the ciphertext's base64 text
    # cannot be a coincidence.
    secret = "s3cret-" + "x" * 24
    sealed = transport.encrypt({"secret": secret})
    assert secret not in sealed and transport.decrypt(sealed) == {"secret": secret}
    assert transport.decrypt("") == {}


@pytest.mark.parametrize(
    "token",
    [
        {"token_type": "Bearer"},
        {"access_token": "t", "token_type": "mac"},
        ["access_token"],
    ],
    ids=["no-token", "not-bearer", "not-object"],
)
def test_oauth_needs_a_bearer_access_token(monkeypatch, token):
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    connection = SimpleNamespace(
        encrypted_secret=transport.encrypt({"secret": "client-secret"}),
        auth_kind="oauth_client_credentials",
        auth_name="",
        username="client",
        base_url="https://api.example.org/",
        auth_options={},
    )
    calls = []
    monkeypatch.setattr(
        transport, "request", lambda url, **kw: calls.append((url, kw)) or (token, {})
    )

    with pytest.raises(IntegrationError, match="bearer access token"):
        transport.connection_auth(connection)
    ((url, options),) = calls
    # Without configured options, the token request uses the default path and no scope.
    assert url == "https://api.example.org/oauth/token"
    assert options["form"] == {"grant_type": "client_credentials"}


def test_saved_credentials_join_the_connection_details(monkeypatch):
    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    connection = SimpleNamespace(
        encrypted_secret=transport.encrypt({"secret": "s3"}),
        auth_kind="header",
        auth_name="X-Token",
        username="",
        base_url="https://api.example.org",
        auth_options={},
    )

    auth, secret = transport.connection_auth(connection)

    assert auth == {"kind": "header", "name": "X-Token", "username": "", "secret": "s3"}
    assert secret == "s3"
