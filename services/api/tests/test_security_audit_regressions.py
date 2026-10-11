"""Reproductions for the October security audit, using synthetic credentials."""

import asyncio
import io
import socket
import ssl
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.core.config import settings
from app.security import auth, ldap, passwords
from app.services import artifacts, questions
from app.services.errors import DomainError
from app.storage import s3
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from starlette.datastructures import Headers


@pytest.mark.parametrize("scheme", ["ldaps", "ldap"])
@pytest.mark.parametrize("case", ["matching", "wrong-name", "untrusted"])
def test_ldap_real_tls_checks_chain_and_hostname_before_bind(
    monkeypatch, tmp_path, scheme, case
):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    host = "localhost" if case != "wrong-name" else "attacker.example"
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), False)
        .sign(key, hashes.SHA256())
    )
    certfile, keyfile = tmp_path / "cert.pem", tmp_path / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile, keyfile)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    errors = []

    def serve():
        try:
            with listener.accept()[0] as conn:
                conn.settimeout(5)
                if scheme == "ldap":
                    packet = conn.recv(4096)
                    # StartTLS ExtendedRequest contains the public OID, no bind.
                    assert b"1.3.6.1.4.1.1466.20037" in packet
                    from ldap3.protocol.rfc4511 import LDAPMessage
                    from pyasn1.codec.ber.decoder import decode

                    msg, _ = decode(packet, asn1Spec=LDAPMessage())
                    message_id = int(msg["messageID"])
                    assert message_id < 128
                    conn.sendall(
                        bytes(
                            [
                                0x30,
                                0x0C,
                                0x02,
                                0x01,
                                message_id,
                                0x78,
                                0x07,
                                0x0A,
                                0x01,
                                0x00,
                                0x04,
                                0x00,
                                0x04,
                                0x00,
                            ]
                        )
                    )
                try:
                    with context.wrap_socket(conn, server_side=True) as secured:
                        secured.recv(4096)
                except (
                    ssl.SSLError,
                    ConnectionResetError,
                    BrokenPipeError,
                    TimeoutError,
                ):
                    pass  # Client rejected the certificate.
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    for field, value in {
        "ldap_enabled": True,
        "ldap_url": f"{scheme}://localhost:{listener.getsockname()[1]}",
        "ldap_base_dn": "dc=example",
        "ldap_bind_dn": "cn=reader,dc=example",
        "ldap_bind_password": "synthetic-secret",
        "ldap_timeout_seconds": 2,
        "ldap_ca_cert_path": str(certfile) if case != "untrusted" else "",
    }.items():
        monkeypatch.setattr(settings, field, value)
    attempts = []

    def bind(connection):
        attempts.append(connection.user)
        connection.result = {"result": 49}
        return False

    monkeypatch.setattr(ldap.Connection, "bind", bind)
    try:
        with pytest.raises(HTTPException) as error:
            ldap.directory_identity("alice", "synthetic-password")
        assert error.value.status_code == 503
        assert attempts == (["cn=reader,dc=example"] if case == "matching" else [])
    finally:
        worker.join(6)
        listener.close()
    assert not worker.is_alive()
    assert not errors


@pytest.mark.parametrize("kind", ["missing", "inactive", "ldap", "local"])
def test_failed_login_performs_one_real_password_derivation(monkeypatch, kind):
    stored = passwords.hash_password("synthetic-password")
    user = (
        None
        if kind == "missing"
        else SimpleNamespace(
            is_active=kind != "inactive",
            auth_backend="ldap" if kind == "ldap" else "local",
            password_hash=stored,
        )
    )
    db = Mock()
    db.query.return_value.filter.return_value.one_or_none.return_value = user
    original = passwords._pbkdf2_sha256
    derive = Mock(wraps=original)
    monkeypatch.setattr(passwords, "_pbkdf2_sha256", derive)
    assert auth.authenticate_user(db, "alice", "wrong-password") is None
    derive.assert_called_once()
    assert derive.call_args.args[2] == passwords._DEFAULT_ITERS


def test_valid_local_login_still_succeeds():
    user = SimpleNamespace(
        is_active=True,
        auth_backend="local",
        password_hash=passwords.hash_password("synthetic-password"),
    )
    db = Mock()
    db.query.return_value.filter.return_value.one_or_none.return_value = user
    assert auth.authenticate_user(db, "alice", "synthetic-password") is user


@pytest.mark.parametrize("failure", ["magic", "read", "storage"])
def test_attachment_failures_propagate_without_logging_untrusted_names(
    monkeypatch, caplog, failure
):
    upload = SimpleNamespace(
        filename="attack\nforged-log.pdf",
        content_type="application/pdf",
        read=AsyncMock(return_value=b"%PDF-1.7\nfixture"),
    )
    if failure == "magic":
        upload.read.return_value = b"not a PDF"
    elif failure == "read":
        upload.read.side_effect = OSError("read failed")
    store = Mock(side_effect=OSError("storage failed"))
    monkeypatch.setattr(questions, "put_bytes", store)
    with pytest.raises(DomainError if failure == "magic" else OSError):
        asyncio.run(
            questions._store_attachments(
                Mock(),
                event_id=uuid.uuid4(),
                thread_id=uuid.uuid4(),
                post_id=uuid.uuid4(),
                files=[upload],
                captured_by="fixture",
            )
        )
    assert "forged-log" not in caplog.text
    if failure != "storage":
        store.assert_not_called()


@pytest.mark.parametrize("backend", ["local", "s3"])
@pytest.mark.parametrize(
    "key",
    [
        "../escape",
        "/absolute",
        "a//b",
        "a/../b",
        "a\\b",
        "",
        "a?versionId=bad",
        "a#fragment",
        "a\x00b",
    ],
)
def test_storage_keys_rejected_before_backend_use(monkeypatch, backend, key):
    monkeypatch.setattr(settings, "artifact_storage_backend", backend)
    client = Mock()
    monkeypatch.setattr(s3, "_client", client)
    with pytest.raises(ValueError):
        s3.put_bytes(key, b"fixture", "text/plain")
    client.assert_not_called()


def test_preview_cache_bounds_bytes_and_entries(monkeypatch):
    monkeypatch.setattr(artifacts, "_PREVIEW_CACHE", OrderedDict())
    monkeypatch.setattr(artifacts, "_PREVIEW_CACHE_BYTES", 6)
    monkeypatch.setattr(artifacts, "_PREVIEW_CACHE_ENTRIES", 2)
    for i in range(10):
        artifacts._cache_preview(str(i), b"abc")
    assert list(artifacts._PREVIEW_CACHE) == ["8", "9"]
    artifacts._cache_preview("huge", b"1234567")
    assert sum(map(len, artifacts._PREVIEW_CACHE.values())) <= 6


@pytest.mark.parametrize(
    "peer,values,expected",
    [
        ("127.0.0.1", ["alice"], "alice"),
        ("192.0.2.1", ["alice"], None),
        (None, ["alice"], None),
        ("127.0.0.1", ["attacker", "alice"], None),
    ],
)
def test_remote_identity_requires_trusted_direct_peer_and_single_header(
    monkeypatch, peer, values, expected
):
    monkeypatch.setattr(settings, "trust_remote_user", True)
    monkeypatch.setattr(settings, "oidc_enabled", False)
    monkeypatch.setattr(settings, "security_trusted_proxy_cidrs", "127.0.0.1/32")
    headers = Headers(raw=[(b"remote_user", v.encode()) for v in values])
    assert auth._remote_user_from_headers(headers, peer=peer) == expected


@pytest.mark.parametrize(
    "operation", ["clear-role", "delete", "empty", "replace-normal", "replace-inactive"]
)
def test_admin_group_cannot_remove_all_inherited_admins(contract_db, operation):
    from app.db.models import Group, User, user_groups
    from app.schemas import GroupMembersPayload, GroupUpdatePayload
    from app.security.roles import count_effective_admins
    from app.services import admin

    db = contract_db
    users = [
        User(
            username=f"inherited-{i}",
            password_hash="unused",
            role="inherit",
            is_active=True,
        )
        for i in range(2)
    ]
    normal = User(
        username="normal", password_hash="unused", role="normal", is_active=True
    )
    inactive = User(
        username="inactive", password_hash="unused", role="inherit", is_active=False
    )
    group = Group(name="only-admins", role="admin")
    db.add_all([*users, normal, inactive, group])
    db.flush()
    db.execute(
        user_groups.insert(), [{"user_id": u.id, "group_id": group.id} for u in users]
    )
    db.commit()
    assert count_effective_admins(db) == 2
    with pytest.raises(DomainError, match="Cannot remove"):
        if operation == "clear-role":
            admin.admin_update_group(
                group_id=str(group.id),
                payload=GroupUpdatePayload(role=""),
                user=users[0],
                db=db,
            )
        elif operation == "delete":
            admin.admin_delete_group(group_id=str(group.id), user=users[0], db=db)
        else:
            ids = (
                []
                if operation == "empty"
                else [(normal if operation == "replace-normal" else inactive).id]
            )
            admin.admin_set_group_members(
                group_id=str(group.id),
                payload=GroupMembersPayload(user_ids=ids),
                user=users[0],
                db=db,
            )
    assert count_effective_admins(db) == 2


@pytest.mark.parametrize(
    "content_type",
    ["x-application/pdf", "application/pdfx", "text/plain; hint=application/pdf"],
)
def test_preview_media_type_is_exact(monkeypatch, content_type):
    artifact = SimpleNamespace(content_type=content_type, event_id=uuid.uuid4())
    db = Mock()
    db.query.return_value.filter.return_value.one_or_none.side_effect = [artifact, None]
    fetch = Mock()
    monkeypatch.setattr(artifacts, "get_object_stream", fetch)
    with pytest.raises(DomainError) as error:
        artifacts.preview_pdf_first_page(
            artifact_id=str(uuid.uuid4()), db=db, user=None
        )
    assert error.value.status_code == 415
    fetch.assert_not_called()


def test_masking_rejects_oversize_actual_stream_and_closes(monkeypatch):
    artifact = SimpleNamespace(
        id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        content_type="text/plain",
        storage_uri="local://local/a.txt",
        sha256="0" * 64,
        meta={},
        kind="text",
        size_bytes=1,
    )
    db = Mock()
    db.query.return_value.filter.return_value.one_or_none.side_effect = [artifact, None]
    body = io.BytesIO(b"x" * (20 * 1024 * 1024 + 1))
    monkeypatch.setattr(settings, "event_data_masking", "samples")
    monkeypatch.setattr(artifacts, "get_object_stream", lambda *a: {"Body": body})
    with pytest.raises(DomainError) as error:
        artifacts.download_artifact(
            artifact_id=str(uuid.uuid4()), sample_export=True, db=db, user=None
        )
    assert error.value.status_code == 413
    assert body.closed


@pytest.mark.parametrize(
    "allowed,url,permitted",
    [
        ("example.org", "https://example.org", True),
        ("example.org", "https://example.org:8443", False),
        ("example.org:8443", "https://example.org:8443", True),
        ("example.org:8443", "https://example.org", False),
    ],
)
def test_custom_integration_allowlist_scopes_ports(
    monkeypatch, allowed, url, permitted
):
    from app.integrations import transport
    from app.integrations.errors import IntegrationError

    monkeypatch.setenv("KEEN_INTEGRATION_ALLOWED_HOSTS", allowed)
    monkeypatch.setattr(
        transport.socket,
        "getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("93.184.216.34", 443))],
    )
    if permitted:
        assert transport.destination(url)[1] == ["93.184.216.34"]
    else:
        with pytest.raises(IntegrationError):
            transport.destination(url)


@pytest.mark.parametrize("url", ["http://notify.example", "https://notify.example"])
def test_outbound_notification_rejects_cleartext_and_loopback(monkeypatch, url):
    from app.ingest import http
    from app.outbound.incidents import IncidentWebhookConfig, post_incident_webhook

    monkeypatch.setattr(
        http.socket,
        "getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 443))],
    )
    upstream = Mock()
    monkeypatch.setattr(http.httpx, "HTTPTransport", upstream)
    config = IncidentWebhookConfig(
        url=url,
        headers={},
        signing_secret="synthetic",
        signature_header="X-Signature",
        timeout_seconds=1,
    )
    with pytest.raises(ValueError):
        post_incident_webhook(config, {"fixture": True})
    upstream.assert_not_called()


def test_later_invalid_attachment_prevents_all_storage_writes(monkeypatch):
    store = Mock()
    monkeypatch.setattr(questions, "put_bytes", store)
    files = [
        SimpleNamespace(
            filename="good.pdf",
            content_type="application/pdf",
            read=AsyncMock(return_value=b"%PDF-1.7\nfixture"),
        ),
        SimpleNamespace(
            filename="bad.pdf",
            content_type="application/pdf",
            read=AsyncMock(return_value=b"not a PDF"),
        ),
    ]
    with pytest.raises(DomainError):
        asyncio.run(
            questions._store_attachments(
                Mock(),
                event_id=uuid.uuid4(),
                thread_id=uuid.uuid4(),
                post_id=uuid.uuid4(),
                files=files,
                captured_by="fixture",
            )
        )
    store.assert_not_called()
