"""Regression tests for the release security review; all upstreams are synthetic."""

import asyncio
import io
import socket
import subprocess
import uuid
from datetime import datetime
from unittest.mock import Mock

import httpx
import pytest
from app.api.routes.isms.schemas import (
    DocumentPayload,
    EffectivenessMetricEntryPayload,
    MeetingLinkPayload,
)
from app.core.config import settings
from app.ingest import forgejo, gitea, github, http
from app.security.request_limits import RequestBodyLimitMiddleware
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request


async def body_request(chunks, headers=(), path="/v1/agents/enroll"):
    seen = []
    sent = []
    messages = iter(
        [
            {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
            for i, c in enumerate(chunks)
        ]
    )

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    async def endpoint(scope, receive, send):
        while True:
            message = await receive()
            seen.append(message["body"])
            if not message["more_body"]:
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    await RequestBodyLimitMiddleware(endpoint)(
        {"type": "http", "path": path, "headers": headers}, receive, send
    )
    return sent[0]["status"], b"".join(seen)


@pytest.mark.parametrize(
    "headers", [[], [(b"content-length", b"1")], [(b"transfer-encoding", b"chunked")]]
)
def test_limits_count_bytes_before_endpoint_can_write(headers):
    status, seen = asyncio.run(body_request([b"a" * 8192, b"b" * 8193], headers))
    assert status == 413 and not seen


@pytest.mark.parametrize("length", [b"-1", b"abc", b"999999999999999999999999999"])
def test_invalid_or_excessive_length_rejected_without_reading(length):
    status, seen = asyncio.run(body_request([], [(b"content-length", length)]))
    assert status in (400, 413) and not seen


def test_streamed_body_and_empty_body_replayed_exactly():
    data = b"x" * (1024 * 1024 + 17)  # includes disk spool and multiple replay chunks
    assert asyncio.run(
        body_request([data[:17], data[17:]], path="/v1/isms/documents")
    ) == (200, data)
    assert asyncio.run(body_request([b""])) == (200, b"")


def dns(monkeypatch, addresses):
    monkeypatch.setattr(
        http.socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (
                socket.AF_INET6 if ":" in ip else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (ip, 443),
            )
            for ip in addresses
        ],
    )


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "169.254.169.254",
        "::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "224.0.0.1",
        "0.0.0.0",
    ],
)
def test_transport_rejects_prohibited_addresses(monkeypatch, address):
    monkeypatch.setattr(settings, "ingestion_allowed_cidrs", "")
    dns(monkeypatch, [address])
    transport = http.IngestionTransport()
    with pytest.raises(ValueError):
        transport.handle_request(httpx.Request("GET", "https://feed.example/rss"))
    transport.close()


@pytest.mark.parametrize("restriction", ["not-a-cidr", "10.2.0.0/16", ""])
def test_cidr_policy_is_fail_closed_and_mapped_addresses_normalised(
    monkeypatch, restriction
):
    dns(monkeypatch, ["::ffff:10.1.2.3"])
    monkeypatch.setattr(settings, "ingestion_allowed_cidrs", restriction)
    if not restriction:
        assert http.destination("https://feed.example")
    else:
        with pytest.raises(ValueError):
            http.destination("https://feed.example")


def test_transport_pins_address_and_preserves_tls_hostname(monkeypatch):
    dns(monkeypatch, ["8.8.8.8"])
    monkeypatch.setattr(settings, "ingestion_allowed_cidrs", "")
    transport = http.IngestionTransport()
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b"feed"))

    transport.transports[("feed.example", 8443)] = httpx.MockTransport(handle)
    with httpx.Client(transport=transport) as client:
        response = client.get(
            "https://feed.example:8443/rss?x=1", headers={"Accept-Encoding": "gzip"}
        )
    assert response.content == b"feed"
    request = seen[0]
    assert str(request.url) == "https://8.8.8.8:8443/rss?x=1"
    assert request.headers["host"] == "feed.example:8443"
    assert request.extensions["sni_hostname"] == "feed.example"
    assert request.headers["accept-encoding"] == "identity"


def test_response_limit_and_compression_rejection(monkeypatch):
    monkeypatch.setattr(http, "MAX_RESPONSE_BYTES", 8)
    with pytest.raises(ValueError):
        b"".join(http.LimitedStream(httpx.ByteStream(b"123456789")))
    dns(monkeypatch, ["8.8.8.8"])
    monkeypatch.setattr(settings, "ingestion_allowed_cidrs", "")
    transport = http.IngestionTransport()
    transport.transports[("feed.example", None)] = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(b"bad")
        )
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ValueError):
            client.get("https://feed.example/rss")


@pytest.mark.parametrize("module,name", [(forgejo, "forgejo"), (gitea, "gitea")])
@pytest.mark.parametrize(
    "feed",
    [
        "https://attacker.example/a/b.rss",
        "http://git.example/a/b.rss",
        "https://git.example:444/a/b.rss",
    ],
)
def test_git_feed_cannot_send_connection_credentials_elsewhere(
    monkeypatch, module, name, feed
):
    monkeypatch.setattr(settings, name + "_base_url", "https://git.example")
    monkeypatch.setattr(settings, name + "_token", "synthetic-secret")
    network = Mock()
    monkeypatch.setattr(module, "ingestion_client", network)
    with pytest.raises(ValueError):
        module._fetch_rss(feed)
    network.assert_not_called()


def test_github_feed_cannot_exfiltrate_pat(monkeypatch):
    monkeypatch.setattr(settings, "github_base_url", "https://api.github.com")
    monkeypatch.setattr(settings, "github_username", "alice")
    monkeypatch.setattr(settings, "github_token", "synthetic-secret")
    with pytest.raises(ValueError):
        github.ingest_github_atom_feed(None, "https://attacker.example/feed")


@pytest.mark.parametrize(
    "model,field",
    [
        (DocumentPayload, "external_url"),
        (EffectivenessMetricEntryPayload, "source_url"),
        (MeetingLinkPayload, "url"),
    ],
)
@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "java\nscript:alert(1)",
        "data:text/html,test",
        "//attacker.example",
        "https://user:secret@example.org",
        "/\\attacker.example",
    ],
)
def test_untrusted_isms_links_reject_active_and_ambiguous_urls(model, field, url):
    with pytest.raises(ValidationError):
        model(**{field: url})


@pytest.mark.parametrize(
    "model,field",
    [
        (DocumentPayload, "external_url"),
        (EffectivenessMetricEntryPayload, "source_url"),
        (MeetingLinkPayload, "url"),
    ],
)
def test_isms_links_keep_valid_urls_and_allow_clearing(model, field):
    assert (
        getattr(model(**{field: "https://example.org/policy#section"}), field)
        == "https://example.org/policy#section"
    )
    assert getattr(model(**{field: ""}), field) is None


def test_pdf_renderer_has_time_and_pixel_limits(contract_db, monkeypatch, tmp_path):
    from app.api.routes import artifacts
    from app.db.models import Artifact, Event

    event = Event(
        id=uuid.uuid4(),
        timestamp=datetime(2026, 1, 1),
        source="rss",
        summary="PDF",
        external_id="pdf",
        raw_pointer={},
        normalized_payload={},
    )
    contract_db.add(event)
    contract_db.flush()
    art = Artifact(
        id=uuid.uuid4(),
        event_id=event.id,
        kind="pdf",
        storage_uri="local://local/file.pdf",
        sha256="a" * 64,
        size_bytes=8,
        content_type="application/pdf",
        captured_by="test",
    )
    contract_db.add(art)
    contract_db.commit()
    monkeypatch.setattr(artifacts, "_require_events_read", lambda *a: None)
    monkeypatch.setattr(artifacts, "_PREVIEW_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        artifacts, "get_object_stream", lambda *a: {"Body": io.BytesIO(b"%PDF-1.7")}
    )

    def render(args, **kwargs):
        assert args[1:3] == ["-m", "app.render.pdf_preview"]
        assert kwargs["timeout"] == 15
        raise subprocess.TimeoutExpired(args, 15)

    monkeypatch.setattr(artifacts.subprocess, "run", render)
    with pytest.raises(HTTPException) as exc:
        artifacts.preview_pdf_first_page(
            Request({"type": "http", "headers": []}), str(art.id), contract_db
        )
    assert exc.value.status_code == 422


def test_pdf_child_sets_os_limits_before_parser(monkeypatch):
    from app.render import pdf_preview

    limits = Mock()
    execute = Mock()
    monkeypatch.setattr(pdf_preview.resource, "setrlimit", limits)
    monkeypatch.setattr(pdf_preview.os, "execvp", execute)
    monkeypatch.setattr(pdf_preview.sys, "argv", ["pdf_preview", "input.pdf", "output"])
    pdf_preview.main()
    values = {call.args[0]: call.args[1] for call in limits.call_args_list}
    assert values[pdf_preview.resource.RLIMIT_AS] == (512 * 1024 * 1024,) * 2
    assert values[pdf_preview.resource.RLIMIT_CPU] == (10, 10)
    assert values[pdf_preview.resource.RLIMIT_FSIZE] == (16 * 1024 * 1024,) * 2
    arguments = execute.call_args.args[1]
    assert arguments[arguments.index("-scale-to") + 1] == "1600"
    assert arguments[-2:] == ["input.pdf", "output"]


def test_distinct_tls_origins_never_share_a_pinned_pool(monkeypatch):
    dns(monkeypatch, ["8.8.8.8"])
    monkeypatch.setattr(settings, "ingestion_allowed_cidrs", "")
    pools = []

    def factory(**kwargs):
        pool = httpx.MockTransport(
            lambda r: httpx.Response(200, stream=httpx.ByteStream(b"ok"))
        )
        pools.append(pool)
        return pool

    monkeypatch.setattr(http.httpx, "HTTPTransport", factory)
    with httpx.Client(transport=http.IngestionTransport()) as client:
        client.get("https://one.example/path")
        client.get("https://two.example/path")
        client.get("https://one.example/again")
    assert len(pools) == 2


@pytest.mark.parametrize(
    "header", ["User-Agent", "Content-Type", "X-Webhook-Timestamp"]
)
def test_named_webhook_secret_never_retained_as_header_metadata(monkeypatch, header):
    from app.ingest import webhooks
    from app.ingest.connections import Connection, connection_scope

    connection = Connection(
        "test",
        "webhooks",
        "Webhook",
        {"provider": "custom", "secret_header": header},
        {"secret": "synthetic-secret"},
        {"providers": {}},
        True,
    )
    monkeypatch.setattr(webhooks, "_check_replay_protection", lambda *a: False)
    store = Mock(return_value={"created": True})
    monkeypatch.setattr(webhooks, "store_event_with_artifact", store)
    with connection_scope(connection):
        headers = {header.lower(): "synthetic-secret"}
        assert webhooks.verify_secret("custom", headers)
        webhooks.ingest_webhook(None, "custom", "event", b"{}", headers)
    assert "synthetic-secret" not in str(store.call_args.kwargs["raw_pointer"])


def test_pinned_transport_verifies_original_tls_hostname(monkeypatch, tmp_path):
    """Local TLS peer verifies actual certificate handling, not just extensions."""
    import ssl
    import threading
    from datetime import timedelta, timezone
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "feed.example")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("feed.example")]), False
        )
        .sign(key, hashes.SHA256())
    )
    certfile = tmp_path / "cert.pem"
    keyfile = tmp_path / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"local fixture")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile, keyfile)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = httpx.HTTPTransport
    trusted = ssl.create_default_context(cafile=str(certfile))
    monkeypatch.setattr(
        http.httpx,
        "HTTPTransport",
        lambda **kw: original(verify=trusted, trust_env=False),
    )
    # The address policy is tested above; only this local fixture bypasses it.
    monkeypatch.setattr(http, "destination", lambda url: ["127.0.0.1"])
    try:
        with httpx.Client(
            transport=http.IngestionTransport(), trust_env=False
        ) as client:
            assert (
                client.get(f"https://feed.example:{server.server_port}/rss").content
                == b"local fixture"
            )
            with pytest.raises(httpx.ConnectError):
                client.get(f"https://wrong.example:{server.server_port}/rss")
        # A matching hostname alone is insufficient: an untrusted chain fails.
        monkeypatch.setattr(http.httpx, "HTTPTransport", original)
        with httpx.Client(
            transport=http.IngestionTransport(), trust_env=False
        ) as client:
            with pytest.raises(httpx.ConnectError):
                client.get(f"https://feed.example:{server.server_port}/rss")
        # The custom API transport uses a separate TLS implementation.
        from app.integrations import transport as custom

        monkeypatch.setattr(custom.ssl, "create_default_context", lambda: trusted)
        for host, valid in [("feed.example", True), ("wrong.example", False)]:
            conn = custom.PinnedHTTPS(host, server.server_port, "127.0.0.1", 2)
            try:
                if valid:
                    conn.request("GET", "/rss")
                    assert conn.getresponse().read() == b"local fixture"
                else:
                    with pytest.raises(ssl.SSLCertVerificationError):
                        conn.request("GET", "/rss")
            finally:
                conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
