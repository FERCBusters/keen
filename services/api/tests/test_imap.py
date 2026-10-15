"""IMAP boundaries using synthetic mail, fake peers and isolated database state."""

import json
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.core.config import settings
from app.db.models import Event, IngestionCursor
from app.ingest import connections as sc
from app.ingest import imap
from app.ingest.imap_support import transport, worker
from app.ingest.imap_support.config import configuration
from app.ingest.imap_support.mime import body_part, decode_body, structure

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_assembled_authority_regressions import unsafe

PLAIN = b'("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 12 1 NIL NIL)'
HTML = b'("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL "7BIT" 12 1 NIL NIL)'
ATTACHMENT = b'("TEXT" "PLAIN" ("NAME" "secret.txt") NIL NIL "7BIT" 12 1 NIL ("ATTACHMENT" ("FILENAME" "secret.txt")))'


def test_mime_prefers_plain_and_excludes_attachments():
    tree = structure(b"(" + HTML + ATTACHMENT + PLAIN + b' "MIXED")')
    assert body_part(tree)["section"] == "3"
    assert body_part(structure(ATTACHMENT)) is None
    assert body_part(structure(HTML))["content_type"] == "text/html"
    assert (
        body_part(["MESSAGE", "RFC822", None, None, None, "7BIT", "100", [], [], 1])
        is None
    )
    with pytest.raises(ValueError):
        structure(b"(" * 30 + b"NIL" + b")" * 30)
    with pytest.raises(ValueError):
        structure(b"(" * 40000)
    text, truncated, fallback = decode_body(
        b'<img src="https://evil">', {"encoding": "7bit", "charset": "utf-8"}, 8
    )
    assert text == "<img src" and truncated and not fallback


@pytest.mark.parametrize(
    "change",
    [
        {"fetch_body": "maybe"},
        {"body_max_chars": "65537"},
        {"port": "0"},
        {"host": "https://mail.example"},
        {"username": "user\r\nLOGOUT"},
        {"folder": "INBOX\n"},
        {"verify_tls": "false"},
    ],
)
def test_configuration_rejects_invalid_values(change):
    with pytest.raises(ValueError):
        configuration({"host": "mail.example", "username": "user", **change})


def test_transport_blocks_private_destinations_without_explicit_allowance(monkeypatch):
    def addr(ip):
        monkeypatch.setattr(
            transport.socket,
            "getaddrinfo",
            lambda *a, **kw: [(None, None, None, None, (ip, 993))],
        )

    for ip in ["127.0.0.1", "169.254.169.254", "::1", "10.0.0.1"]:
        addr(ip)
        with pytest.raises(ValueError):
            transport.addresses("mail.example", 993, "")
    assert transport.addresses("mail.example", 993, "10.0.0.0/8") == ["10.0.0.1"]
    addr("127.0.0.1")
    with pytest.raises(ValueError):
        transport.addresses("mail.example", 993, "127.0.0.0/8")


def test_response_limits_precede_reads_and_deadline_is_absolute():
    client = object.__new__(transport.Client)
    client.literal_limit = 100
    client.file = Mock()
    client.sock = Mock()
    client.deadline = time.monotonic() + 10
    client.received = 0
    client.lines = 0
    with pytest.raises(ValueError):
        client.read(101)
    client.file.read.assert_not_called()
    client.file.readline.return_value = b"x" * 32769
    with pytest.raises(ValueError):
        client.readline()
    client.file.readline.assert_called_once_with(32769)
    client.deadline = time.monotonic() - 1
    with pytest.raises(TimeoutError):
        client.read(1)
    client.file.read.assert_not_called()


class Peer:
    last = None

    def __init__(self, *args):
        Peer.last = self
        self.commands = []
        self.received = 0
        self.closed = False

    def login(self, *args):
        return "OK", []

    def select(self, folder, readonly):
        assert readonly
        self.commands.append(("EXAMINE", folder))
        return "OK", []

    def response(self, key):
        return key, [b"7" if key == "UIDVALIDITY" else b"3"]

    def uid(self, command, *args):
        self.commands.append((command, *args))
        if command == "SEARCH":
            return "OK", [b"2"]
        uid = args[0]
        if "BODYSTRUCTURE" in args[1]:
            return "OK", [
                b"1 (UID 2 RFC822.SIZE 100 BODYSTRUCTURE ("
                + HTML
                + ATTACHMENT
                + PLAIN
                + b' "MIXED"))'
            ]
        data = (
            b"Subject: Test report\r\nFrom: bot@example.test\r\n\r\n"
            if "HEADER.FIELDS" in args[1]
            else b"plain secret"
        )
        return "OK", [(f"1 (UID {uid} BODY[] {{{len(data)}}}".encode(), data), b")"]

    def shutdown(self):
        self.closed = True


@pytest.mark.parametrize("body", [False, True])
def test_readonly_peek_body_choice_and_initial_baseline(monkeypatch, body):
    monkeypatch.setattr(worker, "Client", Peer)
    cfg = {
        "host": "mail.example",
        "port": 993,
        "username": "u",
        "password": "p",
        "folder": "INBOX",
        "fetch_body": body,
        "body_max_chars": 5,
    }
    first = worker.collect(cfg, None, "")
    assert first["last_uid"] == 2 and not first["items"]
    assert all(c[0] != "SEARCH" for c in Peer.last.commands)
    result = worker.collect(cfg, {"uidvalidity": 7, "last_uid": 1}, "")
    assert Peer.last.closed
    requests = [c[-1] for c in Peer.last.commands if c[0] == "FETCH"]
    assert all("BODY[]" not in c and "BODY[" not in c for c in requests)
    if body:
        assert any("BODY.PEEK[3]" in c for c in requests)
        assert result["items"][0]["body"] == "plain"
        assert result["items"][0]["body_truncated"]
    else:
        assert len(requests) == 2 and result["items"][0]["body"] is None
    reset = worker.collect(cfg, {"uidvalidity": 6, "last_uid": 1}, "")
    assert reset["reset_required"]
    assert all(c[0] != "SEARCH" for c in Peer.last.commands)


def test_child_timeout_kills_and_reaps_without_inheriting_secrets(monkeypatch):
    real = subprocess.Popen
    processes = []

    def popen(*args, **kw):
        assert set(kw["env"]) <= {
            "PATH",
            "PYTHONIOENCODING",
            "KEEN_SENSITIVE_KEYS",
            "KEEN_SENSITIVE_KEYS_MODE",
        }
        assert "KEEN_DATABASE_URL" not in kw["env"]
        p = real([sys.executable, "-c", "import time;time.sleep(10)"], **kw)
        processes.append(p)
        return p

    monkeypatch.setattr(imap.subprocess, "Popen", popen)
    monkeypatch.setattr(imap, "WORKER_TIMEOUT", 0.1)
    with pytest.raises(subprocess.TimeoutExpired):
        imap.fetch({}, None)
    assert processes[0].poll() is not None


def test_collection_isolation_redaction_and_uid_reset(contract_db, monkeypatch):
    monkeypatch.setattr(settings, "imap_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", False)
    peers = [
        sc.Connection(
            k, "imap", k, {"host": "mail.example", "username": k}, {"password": "p"}, {}
        )
        for k in ("one", "two")
    ]
    monkeypatch.setattr(sc, "connections", lambda *args: peers)
    blobs = []

    def put(**kw):
        blobs.append(kw)
        return SimpleNamespace(
            uri="local://test", sha256="0" * 64, size_bytes=len(kw["data"])
        )

    monkeypatch.setattr("app.ingest.common.put_bytes", put)
    monkeypatch.setattr("app.ingest.common.apply_rules", lambda *a: 0)
    responses = []

    def fetch(config, cursor):
        responses.append((config["username"], cursor))
        return {
            "uidvalidity": 7,
            "last_uid": 2,
            "items": [
                {
                    "uid": 2,
                    "headers": {
                        "subject": "Report token=DO-NOT-LEAK",
                        "from": "bot@example.test",
                    },
                    "body": "token=DO-NOT-LEAK",
                    "body_content_type": "text/plain",
                }
            ],
        }

    monkeypatch.setattr(imap, "fetch", fetch)
    for peer in peers:
        result = imap.ingest_imap_all(contract_db, connection_id=peer.id)
        assert result[0].get("ok"), result
    rows = contract_db.query(Event).filter_by(source="imap").all()
    assert len(rows) == 2 and {r.connection_id for r in rows} == {"one", "two"}
    assert all(
        "DO-NOT-LEAK" not in r.summary + json.dumps(r.normalized_payload) for r in rows
    )
    assert "DO-NOT-LEAK" not in str(blobs)
    imap.ingest_imap_all(contract_db, connection_id="one")
    assert contract_db.query(Event).filter_by(source="imap").count() == 2
    assert responses[-1][1] == {"uidvalidity": 7, "last_uid": 2}
    monkeypatch.setattr(
        imap, "fetch", lambda *a: {"reset_required": True, "uidvalidity": 8}
    )
    assert not imap.ingest_imap_all(contract_db, connection_id="one")[0]["ok"]
    monkeypatch.setattr(imap, "fetch", lambda *a: pytest.fail("reset must stay paused"))
    assert not imap.ingest_imap_all(contract_db, connection_id="one")[0]["ok"]
    assert contract_db.query(IngestionCursor).count() == 2


@pytest.mark.parametrize(
    "case", ["matching", "wrong-name", "untrusted", "expired", "oversized"]
)
def test_real_tls_checks_before_credentials_and_rejects_large_literal(
    monkeypatch, tmp_path, case
):
    import socket
    import ssl
    import threading
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    host = "attacker.example" if case == "wrong-name" else "mail.example"
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=-1 if case == "expired" else 1))
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
    listener.settimeout(3)
    commands, errors = [], []

    def serve():
        try:
            with listener.accept()[0] as conn:
                conn.settimeout(3)
                try:
                    with context.wrap_socket(conn, server_side=True) as secured:
                        secured.sendall(b"* OK synthetic IMAP peer\r\n")
                        with secured.makefile("rb") as stream:
                            while line := stream.readline(4096):
                                commands.append(line)
                                tag = line.split()[0]
                                if b" CAPABILITY" in line:
                                    secured.sendall(
                                        b"* CAPABILITY IMAP4rev1\r\n"
                                        + tag
                                        + b" OK capability\r\n"
                                    )
                                elif b" LOGIN" in line:
                                    secured.sendall(tag + b" OK authenticated\r\n")
                                elif b" FETCH" in line:
                                    # Claim a huge literal; never actually send/allocate that body.
                                    secured.sendall(
                                        b"* 1 FETCH (UID 2 BODY[1] {2147483647}\r\n"
                                    )
                                    assert stream.read(1) == b""
                                    break
                except (ssl.SSLError, ConnectionResetError, BrokenPipeError):
                    pass
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    original = ssl.create_default_context
    monkeypatch.setattr(transport, "addresses", lambda *a: ["127.0.0.1"])
    monkeypatch.setattr(
        transport.ssl,
        "create_default_context",
        lambda: original(cafile=str(certfile) if case != "untrusted" else None),
    )
    try:
        if case in {"matching", "oversized"}:
            client = transport.Client(
                "mail.example", listener.getsockname()[1], "", time.monotonic() + 3
            )
            try:
                assert client.login("reader", "synthetic-secret")[0] == "OK"
                if case == "oversized":
                    client.state = "SELECTED"
                    with pytest.raises(ValueError, match="literal exceeds"):
                        worker.literal(client, 2, "1", 100)
            finally:
                client.shutdown()
        else:
            with pytest.raises(ssl.SSLCertVerificationError):
                transport.Client(
                    "mail.example", listener.getsockname()[1], "", time.monotonic() + 3
                )
    finally:
        thread.join(4)
        listener.close()
    assert not thread.is_alive() and not errors
    assert any(b"synthetic-secret" in c for c in commands) == (
        case in {"matching", "oversized"}
    )


def test_custom_redaction_precedes_body_character_limit(monkeypatch):
    from app.security import redaction

    monkeypatch.setenv("KEEN_SENSITIVE_KEYS", "private_code")
    redaction._sensitive_cfg.cache_clear()
    try:
        text, _, _ = decode_body(
            b'private_code="secret value with spaces" ordinary',
            {"encoding": "7bit", "charset": "utf-8"},
            30,
        )
        assert "secret" not in text and "REDACTED" in text
    finally:
        redaction._sensitive_cfg.cache_clear()


def test_failure_preserves_checkpoint_and_enforces_backoff(contract_db, monkeypatch):
    monkeypatch.setattr(settings, "imap_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", False)
    peer = sc.Connection(
        "failure",
        "imap",
        "Failure",
        {"host": "mail.example", "username": "reader"},
        {"password": "p"},
        {},
    )
    monkeypatch.setattr(sc, "connections", lambda *a: [peer])
    monkeypatch.setattr(
        imap, "fetch", lambda *a: {"uidvalidity": 7, "last_uid": 20, "items": []}
    )
    assert imap.ingest_imap_all(contract_db)[0]["ok"]

    def failed(*a):
        raise ValueError("upstream synthetic-secret")

    monkeypatch.setattr(imap, "fetch", failed)
    result = imap.ingest_imap_all(contract_db)
    assert not result[0]["ok"] and "synthetic-secret" not in str(result)
    cursor = contract_db.query(IngestionCursor).one()
    assert cursor.meta["last_uid"] == 20 and cursor.meta["failures"] == 1
    monkeypatch.setattr(
        imap, "fetch", lambda *a: pytest.fail("backoff must prevent networking")
    )
    assert imap.ingest_imap_all(contract_db)[0]["skipped"]


def test_connection_admin_csrf_credential_isolation_and_queue(keen, monkeypatch):
    from app.worker import tasks
    from cryptography.fernet import Fernet

    monkeypatch.setenv("KEEN_INTEGRATION_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "imap_enabled", True)
    path = "/v1/admin/source-connections"
    payload = {
        "source": "imap",
        "name": "Mail",
        "configuration": {"host": "mail.example", "username": "reader"},
        "credentials": {"password": "synthetic-secret"},
    }
    assert keen.client().post(path, json=payload).status_code == 401
    ordinary = keen.client(keen.user())
    assert (
        ordinary.post(path, json=payload, headers=unsafe(ordinary)).status_code == 403
    )
    admin = keen.client(keen.user(role="admin"))
    assert admin.post(path, json=payload).status_code == 403
    response = admin.post(path, json=payload, headers=unsafe(admin))
    assert response.status_code == 200, response.text
    assert "synthetic-secret" not in response.text
    row = response.json()
    bad = {
        **payload,
        "version": row["version"],
        "configuration": {"host": "other.example", "username": "reader"},
        "credentials": {},
    }
    assert (
        admin.put(path + "/" + row["id"], json=bad, headers=unsafe(admin)).status_code
        == 422
    )
    calls = []
    monkeypatch.setattr(
        tasks.ingest_imap_connection_task,
        "delay",
        lambda key: calls.append(key) or SimpleNamespace(id="queued"),
    )
    run = admin.post(path + "/" + row["id"] + "/run", headers=unsafe(admin))
    assert run.status_code == 200, run.text
    assert calls == [row["id"]]
    assert (
        tasks.celery_app.conf.task_routes["keen.ingest_imap_connection"]["queue"]
        == "imap"
    )
    assert tasks.ingest_imap_connection_task.time_limit == 75
    invalid = {
        **payload,
        "configuration": {**payload["configuration"], "fetch_body": "maybe"},
    }
    assert admin.post(path, json=invalid, headers=unsafe(admin)).status_code == 422
