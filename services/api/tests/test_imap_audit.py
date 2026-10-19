"""Second-pass IMAP audit reproductions with synthetic secrets and server data."""

import pytest
from app.ingest.imap_support.mime import body_part, decode_body, structure
from app.ingest.imap_support.worker import literal
from app.services.source_connections import _destination

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_assembled_authority_regressions import unsafe


@pytest.mark.parametrize("quote", ['"', "'"])
def test_incomplete_quoted_secret_is_redacted_to_end_of_fetched_body(quote):
    raw = ("token=" + quote + "first second-secret third-secret").encode()
    text, _ = decode_body(raw, {"encoding": "7bit", "charset": "utf-8"}, 4000)
    assert "second-secret" not in text and "third-secret" not in text
    assert "REDACTED" in text


@pytest.mark.parametrize("username", ["reader/", " reader", "reader "])
def test_imap_account_identity_preserves_significant_characters(username):
    original = {"host": "mail.example", "username": "reader"}
    assert _destination(original, "imap") != _destination(
        {**original, "username": username}, "imap"
    )


def test_related_plaintext_resource_is_not_selected_as_the_message_body():
    html = b'("TEXT" "HTML" NIL "<root>" NIL "7BIT" 12 1 NIL NIL)'
    resource = b'("TEXT" "PLAIN" NIL "<resource>" NIL "7BIT" 12 1 NIL NIL)'
    part = body_part(
        structure(b"(" + html + resource + b' "RELATED" ("START" "<root>"))')
    )
    assert part["section"] == "1" and part["content_type"] == "text/html"


def test_literal_uid_must_belong_to_that_literal_not_another_response():
    class Peer:
        def uid(self, *args):
            return "OK", [
                (b"1 (UID 999 BODY[2] {6}", b"secret"),
                b")",
                b"2 (UID 2 FLAGS ())",
            ]

    with pytest.raises(ValueError):
        literal(Peer(), 2, "1", 100)


@pytest.mark.parametrize(
    "prefix,suffix",
    [
        (b"1 (UID 2 BODY[1]<0> {3}", b")"),
        (b"1 (BODY[1]<0> {3}", b" UID 2)"),
    ],
)
def test_literal_accepts_uid_before_or_after_its_own_body(prefix, suffix):
    class Peer:
        def uid(self, *args):
            # An unrelated unsolicited FLAGS update must not supply identity.
            return "OK", [b"5 (UID 77 FLAGS ())", (prefix, b"abc"), suffix]

    assert literal(Peer(), 2, "1", 100) == b"abc"


@pytest.mark.parametrize(
    "prefix,suffix",
    [
        (b"1 (UID 2 BODY[2]<0> {3}", b")"),
        (b"1 (UID 2 BODY[1]<10> {3}", b")"),
        (b"1 (UID 2 BODY[1]<0> {4}", b")"),
        (b"1 (UID 2 BODY[1]<0> {3}", b" UID 2)"),
        (b"1 (UID 2 BODY[1]<0> {3}", b' FLAGS ("UID 2"))'),
    ],
)
def test_literal_rejects_wrong_section_offset_size_and_duplicate_uid(prefix, suffix):
    class Peer:
        def uid(self, *args):
            return "OK", [(prefix, b"abc"), suffix]

    with pytest.raises(ValueError):
        literal(Peer(), 2, "1", 100)


def test_metadata_uid_cannot_be_supplied_by_a_quoted_mime_parameter():
    from app.ingest.imap_support.worker import metadata

    data = b'1 (UID 999 RFC822.SIZE 3 BODYSTRUCTURE ("TEXT" "PLAIN" ("NAME" "UID 2") NIL NIL "7BIT" 3 1))'
    with pytest.raises(ValueError):
        metadata(data, 2)
    size, tree = metadata(
        b'1 (BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 3 1) RFC822.SIZE 3 UID 2)',
        2,
    )
    assert size == 3 and body_part(tree)["section"] == "1"


def test_related_root_and_alternative_selection_preserve_valid_mail():
    plain = b'("TEXT" "PLAIN" NIL NIL NIL "7BIT" 12 1 NIL NIL)'
    html = b'("TEXT" "HTML" NIL "<root>" NIL "7BIT" 12 1 NIL NIL)'
    resource = b'("TEXT" "PLAIN" NIL "<resource>" NIL "7BIT" 12 1 NIL NIL)'
    assert (
        body_part(
            structure(b"(" + resource + html + b' "RELATED" ("START" "<root>"))')
        )["section"]
        == "2"
    )
    assert (
        body_part(structure(b"(" + html + resource + b' "RELATED")'))["section"] == "1"
    )
    assert (
        body_part(
            structure(b"(" + html + resource + b' "RELATED" ("START" "<absent>"))')
        )
        is None
    )
    alternative = b"(" + html + plain + b' "ALTERNATIVE")'
    assert (
        body_part(structure(b"(" + alternative + resource + b' "RELATED")'))["section"]
        == "1.2"
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'("TEXT" "PLAIN" ("NAME") NIL NIL "7BIT" 12 1)',
        b'("TEXT" "PLAIN" NIL NIL NIL "7BIT" 12 1 NIL "ATTACHMENT")',
        b'("TEXT" "PLAIN" NIL NIL NIL "7BIT" 12 1) trailing',
    ],
)
def test_malformed_mime_attachment_metadata_fails_closed(raw):
    with pytest.raises(ValueError):
        body_part(structure(raw))


@pytest.mark.parametrize("tail", ["", "\\", "\\\n", '\\"'])
def test_partial_custom_secret_redaction_also_protects_json_artifact(monkeypatch, tail):
    import json

    from app.security import redaction

    monkeypatch.setenv("KEEN_SENSITIVE_KEYS", "private_code")
    redaction._sensitive_cfg.cache_clear()
    try:
        text = 'private_code="first second-secret\nthird-secret' + tail
        raw, _ = redaction.redact_bytes(
            json.dumps({"mail": {"body": text}}).encode(), "application/json"
        )
        assert (
            "second-secret" not in raw.decode() and "third-secret" not in raw.decode()
        )
        complete = 'private_code="first second-secret" normal text'
        assert redaction.redact_str(complete) == "private_code=REDACTED normal text"
    finally:
        redaction._sensitive_cfg.cache_clear()


def test_account_edits_require_credentials_through_real_admin_route(keen, monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    admin = keen.client(keen.user(role="admin"))
    path = "/v1/admin/source-connections"
    payload = {
        "source": "imap",
        "name": "Mail",
        "configuration": {"host": "mail.example", "username": "reader"},
        "credentials": {"password": "synthetic-secret"},
    }
    result = admin.post(path, json=payload, headers=unsafe(admin))
    assert result.status_code == 200, result.text
    row = result.json()
    for name in ["reader/", " reader", "reader "]:
        changed = {
            **payload,
            "version": row["version"],
            "credentials": {},
            "configuration": {**payload["configuration"], "username": name},
        }
        result = admin.put(path + "/" + row["id"], json=changed, headers=unsafe(admin))
        assert result.status_code == 422 and "synthetic-secret" not in result.text
    unchanged = {
        **payload,
        "version": row["version"],
        "credentials": {},
        "configuration": {**payload["configuration"], "port": "993"},
    }
    assert (
        admin.put(
            path + "/" + row["id"], json=unchanged, headers=unsafe(admin)
        ).status_code
        == 200
    )


def test_process_deadline_stops_a_tls_peer_dripping_without_newlines(tmp_path):
    import json
    import os
    import signal
    import socket
    import ssl
    import subprocess
    import sys
    import threading
    import time
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mail.example")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("mail.example")]), False
        )
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
    listener.settimeout(4)
    sent, errors = [], []
    stop = threading.Event()

    def serve():
        try:
            with listener.accept()[0] as connection:
                connection.settimeout(2)
                with context.wrap_socket(connection, server_side=True) as secured:
                    while not stop.is_set():
                        secured.sendall(
                            b"x"
                        )  # Progress, but never a complete IMAP greeting.
                        sent.append(1)
                        stop.wait(0.05)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            pass
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    # Test-only local destination and CA. Production resolution/verification
    # remains unchanged; its restrictions have separate actual-TLS regressions.
    code = """import ssl, sys
from app.ingest.imap_support import worker, transport
original = ssl.create_default_context
transport.ssl.create_default_context = lambda: original(cafile=sys.argv[1])
transport.addresses = lambda *a: ['127.0.0.1']
worker.COLLECTION_TIMEOUT = 1
worker.main()
"""
    request = {
        "configuration": {
            "host": "mail.example",
            "port": listener.getsockname()[1],
            "username": "reader",
            "password": "synthetic-secret",
            "folder": "INBOX",
            "fetch_body": False,
            "body_max_chars": 4000,
        },
        "cursor": None,
        "allowed_cidrs": "",
    }
    start = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, str(certfile)],
            input=json.dumps(request).encode(),
            capture_output=True,
            cwd=Path(__file__).resolve().parents[1],
            env={"PATH": os.defpath, "PYTHONIOENCODING": "utf-8"},
            timeout=3,
        )
    finally:
        stop.set()
        thread.join(4)
        listener.close()
    assert result.returncode == -signal.SIGALRM
    assert time.monotonic() - start < 3
    assert len(sent) >= 5 and not errors and not thread.is_alive()
    assert b"synthetic-secret" not in result.stdout + result.stderr


def test_dispatch_rotation_prevents_same_mailboxes_expiring_every_cycle(
    contract_db, monkeypatch
):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.core.config import settings
    from app.ingest import connections
    from app.worker import tasks

    monkeypatch.setattr(settings, "imap_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", False)
    peers = [
        SimpleNamespace(id=str(i), enabled=True, managed_by="database")
        for i in range(7)
    ]
    monkeypatch.setattr(connections, "connections", lambda *a: peers)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: nullcontext(contract_db))
    queued = []
    monkeypatch.setattr(tasks.ingest_imap_connection_task, "delay", queued.append)
    serviced = set()
    for cycle in range(7):
        monkeypatch.setattr(
            tasks, "time", SimpleNamespace(time=lambda: cycle * 300), raising=False
        )
        queued.clear()
        assert tasks.dispatch_imap_task()["queued"] == 7
        # Model capacity exhaustion: later jobs expire while earlier slow polls
        # occupy the single worker. Every mailbox must get an early position.
        serviced.update(queued[:4])
    assert serviced == {p.id for p in peers}
