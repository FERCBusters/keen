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
    text, truncated = decode_body(
        b'<img src="https://evil">', {"encoding": "7bit", "charset": "utf-8"}, 8
    )
    assert text == "<img src" and truncated


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
        if args[1] == "(UID RFC822.SIZE)":
            return "OK", [b"1 (UID 2 RFC822.SIZE 100)"]
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
        section = args[1].split("BODY.PEEK[", 1)[1].split("]", 1)[0]
        return "OK", [
            (f"1 (UID {uid} BODY[{section}]<0> {{{len(data)}}}".encode(), data),
            b")",
        ]

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
    from datetime import timedelta

    now = imap.utc_now_naive()
    monkeypatch.setattr(imap, "utc_now_naive", lambda: now + timedelta(minutes=5))
    imap.ingest_imap_all(contract_db, connection_id="one")
    assert contract_db.query(Event).filter_by(source="imap").count() == 2
    assert responses[-1][1] == {"uidvalidity": 7, "last_uid": 2}
    monkeypatch.setattr(
        imap, "fetch", lambda *a: {"reset_required": True, "uidvalidity": 8}
    )
    monkeypatch.setattr(imap, "utc_now_naive", lambda: now + timedelta(minutes=10))
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
        text, _ = decode_body(
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

    from datetime import timedelta

    now = imap.utc_now_naive()
    monkeypatch.setattr(imap, "utc_now_naive", lambda: now + timedelta(minutes=5))
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


CURSOR = {"uidvalidity": 7, "last_uid": 1}
MAILBOX = {
    "host": "mail.example",
    "port": 993,
    "username": "u",
    "password": "p",
    "folder": "INBOX",
    "fetch_body": False,
    "body_max_chars": 5,
}


def hostile_peer(**replies):
    """A peer that answers one command with a scripted, hostile reply."""

    class Hostile(Peer):
        def __init__(self, *args):
            super().__init__(*args)
            self.received = replies.get("received", 0)

        def response(self, key):
            if key == "UIDNEXT" and "uidnext" in replies:
                return key, [replies["uidnext"]]
            return super().response(key)

        def uid(self, command, *args):
            if command == "SEARCH" and "search" in replies:
                self.commands.append((command, *args))
                return "OK", replies["search"]
            if (
                command == "FETCH"
                and args[1] == "(UID RFC822.SIZE)"
                and "meta" in replies
            ):
                self.commands.append((command, *args))
                return "OK", replies["meta"]
            if (
                command == "FETCH"
                and "HEADER.FIELDS" in args[1]
                and "header" in replies
            ):
                self.commands.append((command, *args))
                data = replies["header"]
                section = args[1].split("BODY.PEEK[", 1)[1].split("]", 1)[0]
                return "OK", [
                    (
                        f"1 (UID {args[0]} BODY[{section}]<0> {{{len(data)}}}".encode(),
                        data,
                    ),
                    b")",
                ]
            return super().uid(command, *args)

    return Hostile


@pytest.mark.parametrize(
    "replies,detail",
    [
        ({"uidnext": b"1"}, "IMAP UIDNEXT moved backwards"),
        ({"search": [b"2", b"2"]}, "Oversized IMAP search response"),
        ({"search": [b"2 " * 601]}, "Oversized IMAP search response"),
        ({"search": [b"2 two"]}, "Invalid IMAP search response"),
        ({"search": [b"5"]}, "IMAP returned UID outside requested window"),
        (
            {"meta": [b"1 (UID 2 RFC822.SIZE 100)", b"1 (UID 2 RFC822.SIZE 100)"]},
            "Unsupported IMAP metadata response",
        ),
        ({"meta": [b"UID 2 RFC822.SIZE 100"]}, "Invalid IMAP metadata response"),
        ({"meta": [b"1 (UID 2)"]}, "Invalid IMAP metadata fields"),
        ({"meta": [b"1 (UID 9 RFC822.SIZE 100)"]}, "IMAP metadata UID mismatch"),
        ({"meta": [b"1 (UID 2 RFC822.SIZE NIL)"]}, "Invalid IMAP message size"),
        ({"meta": [b'1 (UID 2 RFC822.SIZE "1e3")']}, "Invalid IMAP message size"),
        ({"header": b"X: y\r\n" * 201}, "IMAP headers exceed limit"),
        ({"header": b"x" * 16384}, "IMAP headers exceed limit"),
    ],
)
def test_hostile_mailbox_replies_end_the_collection(monkeypatch, replies, detail):
    monkeypatch.setattr(worker, "Client", hostile_peer(**replies))

    with pytest.raises(ValueError) as error:
        worker.collect(MAILBOX, CURSOR, "")

    assert str(error.value) == detail
    assert Peer.last.closed


def test_a_spent_session_budget_leaves_messages_for_the_next_run(monkeypatch):
    monkeypatch.setattr(worker, "Client", hostile_peer(received=4 * 1024 * 1024))

    result = worker.collect(MAILBOX, CURSOR, "")

    # Nothing is fetched and the cursor stays before the unread message.
    assert result == {"uidvalidity": 7, "last_uid": 1, "items": []}
    assert [c[0] for c in Peer.last.commands] == ["EXAMINE", "SEARCH"]


@pytest.mark.parametrize(
    "raw,detail",
    [
        (b"", "Incomplete IMAP structure"),
        (b"   ", "Incomplete IMAP structure"),
        (b'("TEXT" "PLAIN"', "Incomplete IMAP structure"),
        (b'("TEXT" "PLA', "Unterminated IMAP string"),
        (b'"ends with an escape\\', "Unterminated IMAP string"),
        (b"({5}\r\nhello)", "Unsupported IMAP structure literal"),
        (b"(NIL) trailing", "Trailing IMAP structure data"),
    ],
)
def test_malformed_body_structures_are_refused(raw, detail):
    with pytest.raises(ValueError) as error:
        structure(raw)
    assert str(error.value) == detail


def test_structure_strings_keep_escaped_characters():
    assert structure(b'("a \\"quoted\\" \\\\ value" NIL)') == [
        'a "quoted" \\ value',
        None,
    ]


@pytest.mark.parametrize(
    "tree,detail",
    [
        (["TEXT"], "Invalid MIME structure"),
        ([["TEXT"], "MIXED"], "Invalid MIME structure"),
        (
            [["TEXT", "PLAIN", None, None, None, "7BIT", "x", 1], "MIXED"],
            "Invalid MIME part size",
        ),
        (
            [["TEXT", "PLAIN", None, None, None, "7BIT", "-1", 1], "MIXED"],
            "Invalid MIME part size",
        ),
        (
            [["TEXT", "PLAIN", None, None, None, "7BIT", "1", 1], ["X"]],
            "Missing MIME multipart subtype",
        ),
        (
            [["TEXT", "PLAIN", ["CHARSET"], None, None, "7BIT", "1", 1], "MIXED"],
            "Invalid MIME parameters",
        ),
        (
            [
                [
                    "TEXT",
                    "PLAIN",
                    None,
                    None,
                    None,
                    "7BIT",
                    "1",
                    1,
                    None,
                    ["ATTACHMENT"],
                ],
                "MIXED",
            ],
            "Invalid MIME disposition",
        ),
    ],
)
def test_malformed_mime_trees_are_refused(tree, detail):
    with pytest.raises(ValueError) as error:
        body_part(tree)
    assert str(error.value) == detail


def test_named_or_attached_parts_are_never_bodies():
    plain = ["TEXT", "PLAIN", None, None, None, "7BIT", "4", 1]
    # A multipart that is itself named or attached is skipped as a whole.
    assert body_part([plain, "MIXED", ["NAME", "bundle.eml"]]) is None
    assert body_part([plain, "MIXED", None, ["ATTACHMENT", None]]) is None
    assert body_part([plain, "MIXED", None, ["INLINE", ["FILENAME", "a.txt"]]]) is None
    assert body_part([plain + [None, ["INLINE", None]], "MIXED"])["section"] == "1"
    deep = plain
    for _ in range(13):
        deep = [deep, "MIXED"]
    with pytest.raises(ValueError, match="Too many MIME parts"):
        body_part(deep)


def test_quoted_printable_bodies_are_decoded_and_redacted():
    text, truncated = decode_body(
        b"token=3Dhunter2 caf=C3=A9",
        {"encoding": "quoted-printable", "charset": "utf8"},
        100,
    )
    assert (text, truncated) == ("token=REDACTED café", False)


def test_configuration_rejects_non_text_hosts_and_non_numeric_limits():
    # A hostname that is no string and port or body limits that are no whole
    # numbers are refused before any network use, with a safe message.
    with pytest.raises(ValueError, match="hostname must be text"):
        configuration({"host": ["mail.example"], "username": "user"})
    for change in ({"port": "99a"}, {"body_max_chars": "4000.5"}, {"port": -1}):
        with pytest.raises(ValueError, match="must be integers"):
            configuration({"host": "mail.example", "username": "user", **change})


class _Stdin:
    def __init__(self, raw):
        self.buffer = _Reader(raw)


class _Reader:
    def __init__(self, raw):
        self.raw = raw

    def read(self, limit):
        return self.raw[:limit]


class _Stdout:
    def __init__(self):
        self.text = []
        self.buffer = self

    def write(self, data):
        self.text.append(data if isinstance(data, str) else data.decode())


def _run_worker_main(monkeypatch, raw, collect):
    limits, alarms, out = [], [], _Stdout()
    monkeypatch.setattr(
        worker.resource, "setrlimit", lambda kind, value: limits.append((kind, value))
    )
    monkeypatch.setattr(worker.signal, "alarm", alarms.append)
    monkeypatch.setattr(worker.sys, "stdin", _Stdin(raw))
    monkeypatch.setattr(worker.sys, "stdout", out)
    monkeypatch.setattr(worker, "collect", collect)
    code = 0
    try:
        worker.main()
    except SystemExit as stop:
        code = stop.code
    return code, "".join(out.text), limits, alarms


def test_worker_process_limits_itself_then_relays_a_bounded_result(monkeypatch):
    # The worker drops core dumps, caps memory, CPU and descriptors, bounds
    # every phase with an alarm and hands the collected mailbox back as JSON.
    calls = []

    def collect(config, cursor, cidrs):
        calls.append((config, cursor, cidrs))
        return {"uidvalidity": 1, "last_uid": 3, "items": []}

    request = json.dumps(
        {
            "configuration": {"host": "mail.example"},
            "cursor": {"uidvalidity": 1, "last_uid": 2},
            "allowed_cidrs": ["10.0.0.0/8"],
        }
    ).encode()
    code, output, limits, alarms = _run_worker_main(monkeypatch, request, collect)
    assert code == 0
    assert json.loads(output) == {"uidvalidity": 1, "last_uid": 3, "items": []}
    assert calls == [
        ({"host": "mail.example"}, {"uidvalidity": 1, "last_uid": 2}, ["10.0.0.0/8"])
    ]
    assert dict(limits) == {
        worker.resource.RLIMIT_CORE: (0, 0),
        worker.resource.RLIMIT_AS: (256 * 1024 * 1024, 256 * 1024 * 1024),
        worker.resource.RLIMIT_CPU: (8, 8),
        worker.resource.RLIMIT_NOFILE: (32, 32),
    }
    assert alarms == [50, worker.COLLECTION_TIMEOUT, 5]


def test_worker_refuses_oversized_input_without_collecting(monkeypatch):
    def collect(*args):
        pytest.fail("oversized input must never reach the network")

    code, output, _, alarms = _run_worker_main(monkeypatch, b"{" * 32769, collect)
    assert code == 1
    assert json.loads(output) == {
        "error": "IMAP collection failed a connection, protocol or resource check"
    }
    assert alarms == [50]


def test_worker_failures_and_oversized_output_report_only_a_generic_error(
    monkeypatch,
):
    # Neither exception detail nor an over-large result leaves the worker.
    request = json.dumps(
        {"configuration": {}, "cursor": None, "allowed_cidrs": []}
    ).encode()

    def failing(*args):
        raise ValueError("server said: password secret-DO-NOT-LEAK")

    code, output, _, _ = _run_worker_main(monkeypatch, request, failing)
    assert code == 1 and "DO-NOT-LEAK" not in output
    assert json.loads(output)["error"].startswith("IMAP collection failed")

    def huge(*args):
        return {"items": ["x" * (worker.MAX_OUTPUT_BYTES + 1)]}

    code, output, _, _ = _run_worker_main(monkeypatch, request, huge)
    assert code == 1 and len(output) < 200
    assert json.loads(output)["error"].startswith("IMAP collection failed")


def test_fetch_rejects_oversized_configuration_before_spawning(monkeypatch):
    monkeypatch.setattr(
        imap.subprocess, "Popen", lambda *a, **k: pytest.fail("no child process")
    )
    with pytest.raises(ValueError, match="configuration exceeds limit"):
        imap.fetch({"host": "mail.example", "username": "u" * 40000}, None)


def test_fetch_parses_a_successful_worker_and_refuses_a_failed_one(monkeypatch):
    # The parent trusts only a zero exit status with a bounded body; any other
    # exit yields the generic error and never exposes the child's output.
    real = subprocess.Popen
    scripts = iter(
        [
            "import sys;sys.stdin.buffer.read();"
            'sys.stdout.write(\'{"uidvalidity":1,"last_uid":9,"items":[]}\')',
            "import sys;sys.stdin.buffer.read();"
            'sys.stdout.write(\'{"error":"detail-DO-NOT-LEAK"}\');sys.exit(1)',
        ]
    )

    def popen(*args, **kw):
        return real([sys.executable, "-c", next(scripts)], **kw)

    monkeypatch.setattr(imap.subprocess, "Popen", popen)
    assert imap.fetch({"host": "mail.example"}, None) == {
        "uidvalidity": 1,
        "last_uid": 9,
        "items": [],
    }
    with pytest.raises(ValueError) as failure:
        imap.fetch({"host": "mail.example"}, None)
    assert "DO-NOT-LEAK" not in str(failure.value)
    assert "IMAP collection failed" in str(failure.value)


def test_environment_and_demo_connections_never_poll(contract_db, monkeypatch):
    # Environment-managed IMAP and demo deployments skip collection with a
    # clear reason before credentials are touched.
    monkeypatch.setattr(settings, "imap_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(imap, "fetch", lambda *a: pytest.fail("no network"))
    peers = [
        sc.Connection(
            "env:imap", "imap", "Environment", {}, {}, None, True, "environment"
        ),
        sc.Connection(
            "named",
            "imap",
            "Named",
            {"host": "mail.example", "username": "reader"},
            {"password": "p"},
            {},
        ),
    ]
    monkeypatch.setattr(sc, "connections", lambda *args: peers)
    results = imap.ingest_imap_all(contract_db)
    assert [r["reason"] for r in results] == [
        "Create a named IMAP connection",
        "IMAP is disabled in demo environments",
    ]
    assert all(r["skipped"] for r in results)
    assert contract_db.query(IngestionCursor).count() == 0


@pytest.mark.parametrize("password", ["", "p" * 4097, "pass\x00word", "pässword"])
def test_unusable_passwords_are_refused_before_polling(
    contract_db, monkeypatch, password
):
    monkeypatch.setattr(settings, "imap_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(imap, "fetch", lambda *a: pytest.fail("no network"))
    peer = sc.Connection(
        "named",
        "imap",
        "Named",
        {"host": "mail.example", "username": "reader"},
        {"password": password},
        {},
    )
    monkeypatch.setattr(sc, "connections", lambda *args: [peer])
    (result,) = imap.ingest_imap_all(contract_db)
    assert result["ok"] is False
    assert result["error"].startswith("IMAP requires a printable ASCII password")
    assert password not in json.dumps(result) or password == ""
    assert contract_db.query(IngestionCursor).count() == 0


def test_a_first_run_that_fails_still_records_its_backoff(contract_db, monkeypatch):
    monkeypatch.setattr(settings, "imap_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", False)
    peer = sc.Connection(
        "fresh",
        "imap",
        "Fresh",
        {"host": "mail.example", "username": "reader"},
        {"password": "p"},
        {},
    )
    monkeypatch.setattr(sc, "connections", lambda *a: [peer])

    def failed(*a):
        raise ValueError("synthetic-secret")

    monkeypatch.setattr(imap, "fetch", failed)
    (result,) = imap.ingest_imap_all(contract_db)
    assert result["ok"] is False and "synthetic-secret" not in json.dumps(result)
    cursor = contract_db.query(IngestionCursor).one()
    assert cursor.meta["failures"] == 1
    assert cursor.meta["retry_after"] > cursor.meta["last_poll_at"]
    assert "last_uid" not in cursor.meta


def test_a_cursor_removed_during_the_poll_is_recreated(monkeypatch):
    # Removing the connection's cursor while the worker runs must not lose the
    # outcome: both a failure and a success re-create the row before writing.
    monkeypatch.setattr(settings, "imap_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", False)
    peer = sc.Connection(
        "racy",
        "imap",
        "Racy",
        {"host": "mail.example", "username": "reader"},
        {"password": "p"},
        {},
    )
    monkeypatch.setattr(sc, "connections", lambda *a: [peer])

    class Vanishing:
        """Every lookup finds no cursor, as after a concurrent removal."""

        def __init__(self):
            self.added = []

        def query(self, *a):
            return self

        def filter_by(self, **kw):
            return self

        def one_or_none(self):
            return None

        def add(self, row):
            self.added.append(row)

        def commit(self):
            pass

        def rollback(self):
            pass

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        def scalar(self, *a):
            return None

    db = Vanishing()

    def failed(*a):
        raise ValueError("boom")

    monkeypatch.setattr(imap, "fetch", failed)
    (result,) = imap.ingest_imap_all(db)
    assert result["ok"] is False
    assert len(db.added) == 2 and db.added[1].meta["failures"] == 1

    db = Vanishing()
    monkeypatch.setattr(
        imap, "fetch", lambda *a: {"reset_required": True, "uidvalidity": 8}
    )
    (result,) = imap.ingest_imap_all(db)
    assert result["ok"] is False and "UIDVALIDITY changed" in result["error"]
    assert len(db.added) == 2 and db.added[1].meta["reset_required"] is True
