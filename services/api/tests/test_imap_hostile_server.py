"""Hostile IMAP peers over real local TLS; synthetic identities and contents."""

import socket
import ssl
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from app.ingest.imap_support import transport, worker
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@pytest.fixture(scope="module")
def tls_files(tmp_path_factory):
    root = tmp_path_factory.mktemp("imap-hostile-tls")
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
    certfile, keyfile = root / "cert.pem", root / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certfile, keyfile


@pytest.fixture
def peer(monkeypatch, tls_files):
    certfile, keyfile = tls_files
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile, keyfile)
    original = ssl.create_default_context
    # Test-only destination and CA; real certificate/hostname verification remains active.
    monkeypatch.setattr(transport, "addresses", lambda *args: ["127.0.0.1"])
    monkeypatch.setattr(
        transport.ssl, "create_default_context", lambda: original(cafile=str(certfile))
    )

    @contextmanager
    def run(respond, greeting=b"* OK synthetic peer\r\n"):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)
        commands, errors = [], []

        def serve():
            try:
                with listener.accept()[0] as connection:
                    connection.settimeout(3)
                    with context.wrap_socket(connection, server_side=True) as secured:
                        secured.sendall(greeting)
                        with secured.makefile("rb") as stream:
                            while line := stream.readline(8192):
                                assert len(line) < 8192
                                commands.append(line)
                                tag = line.split()[0]
                                if b" CAPABILITY" in line:
                                    secured.sendall(
                                        b"* CAPABILITY IMAP4rev1\r\n"
                                        + tag
                                        + b" OK done\r\n"
                                    )
                                elif b" LOGIN" in line:
                                    secured.sendall(tag + b" OK done\r\n")
                                else:
                                    reply = respond(line, tag)
                                    if reply is None:
                                        break
                                    if callable(reply):
                                        reply(secured)
                                        break
                                    secured.sendall(reply)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                pass
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], commands
        finally:
            thread.join(4)
            listener.close()
            assert not thread.is_alive()
            assert not errors

    return run


def test_unsolicited_greeting_literal_is_rejected_before_authentication(peer):
    with peer(lambda *a: None, b"* OK {6}\r\nsecret\r\n") as (port, commands):
        with pytest.raises(ValueError, match="literal"):
            client = transport.Client("mail.example", port, "", time.monotonic() + 3)
            client.shutdown()
    assert not commands


def test_second_literal_is_rejected_before_its_payload_is_read(peer, monkeypatch):
    read_sizes = []
    original = transport.Client.read

    def read(client, size):
        result = original(client, size)
        read_sizes.append(size)
        return result

    monkeypatch.setattr(transport.Client, "read", read)
    response = b"* 1 FETCH (UID 2 BODY[1]<0> {3}\r\nabc BODY[2]<0> {3}\r\nxyz)\r\n"
    with peer(lambda line, tag: response + tag + b" OK done\r\n") as (port, commands):
        client = transport.Client("mail.example", port, "", time.monotonic() + 3)
        try:
            client.login('"reader"', "synthetic-secret")
            client.state = "SELECTED"
            with pytest.raises(ValueError):
                worker.literal(client, 2, "1", 100)
        finally:
            client.shutdown()
    assert read_sizes == [3]


def test_conflicting_uidvalidity_is_rejected():
    class Peer:
        def response(self, key):
            return key, [b"7", b"8"]

    with pytest.raises(ValueError):
        worker.number(Peer(), "UIDVALIDITY")


@pytest.mark.parametrize("payload", [b"", b"abc"])
def test_valid_single_literal_and_uid_after_literal_remain_supported(peer, payload):
    def respond(line, tag):
        return (
            b"* 1 FETCH (BODY[1]<0> {"
            + str(len(payload)).encode()
            + b"}\r\n"
            + payload
            + b" UID 2)\r\n"
            + tag
            + b" OK done\r\n"
        )

    with peer(respond) as (port, commands):
        client = transport.Client("mail.example", port, "", time.monotonic() + 3)
        try:
            client.login('"reader"', "synthetic-secret")
            client.state = "SELECTED"
            assert worker.literal(client, 2, "1", 100) == payload
            assert client.literal_limit == 0
        finally:
            client.shutdown()


@pytest.mark.parametrize("status", ["NO", "OK"])
def test_missing_literal_does_not_leave_an_allowance(peer, status):
    with peer(lambda line, tag: tag + b" " + status.encode() + b" no literal\r\n") as (
        port,
        commands,
    ):
        client = transport.Client("mail.example", port, "", time.monotonic() + 3)
        try:
            client.login('"reader"', "synthetic-secret")
            client.state = "SELECTED"
            with pytest.raises(ValueError):
                worker.literal(client, 2, "1", 100)
            assert client.literal_limit == 0
        finally:
            client.shutdown()


@pytest.mark.parametrize(
    "attack",
    [
        "valid",
        "long-from",
        "wrong-uid",
        "wrong-section",
        "wrong-offset",
        "metadata-literal",
        "conflicting-epoch",
    ],
)
def test_full_collection_with_hostile_tls_responses(peer, attack):
    def respond(line, tag):
        done = tag + b" OK done\r\n"
        if b" EXAMINE" in line:
            epoch = (
                b"* OK [UIDVALIDITY 8] conflicting\r\n"
                if attack == "conflicting-epoch"
                else b""
            )
            return (
                b"* 2 EXISTS\r\n* OK [UIDVALIDITY 7] valid\r\n"
                + epoch
                + b"* OK [UIDNEXT 3] next\r\n"
                + done
            )
        if b" UID SEARCH" in line:
            return b"* SEARCH 2\r\n" + done
        if b"RFC822.SIZE" in line:
            if attack == "metadata-literal":
                return (
                    b"* 1 FETCH (UID 2 RFC822.SIZE 3 BODYSTRUCTURE {3}\r\nabc)\r\n"
                    + done
                )
            return (
                b'* 1 FETCH (UID 2 RFC822.SIZE 100 BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 3 1))\r\n'
                + done
            )
        section = line.split(b"BODY.PEEK[", 1)[1].split(b"]", 1)[0]
        data = (
            b"Subject: Report\r\nFrom: bot@example.test\r\n\r\n"
            if section.startswith(b"HEADER")
            else b"abc"
        )
        if attack == "long-from" and section.startswith(b"HEADER"):
            data = (
                b"Subject: Report\r\nFrom: "
                + b"a" * 250
                + b" <bot@example.test>\r\n\r\n"
            )
        uid = b"999" if attack == "wrong-uid" else b"2"
        if attack == "wrong-section":
            section = b"99"
        offset = b"9" if attack == "wrong-offset" else b"0"
        return (
            b"* 1 FETCH (UID "
            + uid
            + b" BODY["
            + section
            + b"]<"
            + offset
            + b"> {"
            + str(len(data)).encode()
            + b"}\r\n"
            + data
            + b")\r\n"
            + done
        )

    with peer(respond) as (port, commands):
        config = dict(
            host="mail.example",
            port=port,
            username="reader",
            password="synthetic-secret",
            folder="INBOX",
            fetch_body=True,
            body_max_chars=4000,
        )
        if attack in {"valid", "long-from"}:
            result = worker.collect(config, {"uidvalidity": 7, "last_uid": 1}, "")
            assert result["last_uid"] == 2
            assert result["items"][0]["body"] == "abc"
            assert result["items"][0]["headers"]["subject"] == "Report"
            if attack == "long-from":
                assert len(result["items"][0]["headers"]["from"]) > 128
        else:
            with pytest.raises(ValueError):
                worker.collect(config, {"uidvalidity": 7, "last_uid": 1}, "")
    assert all(
        not any(
            command in line
            for command in (
                b" SELECT ",
                b" STORE ",
                b" EXPUNGE",
                b" CLOSE",
                b" LOGOUT",
                b" BODY[",
            )
        )
        for line in commands
    )
    assert any(b" EXAMINE " in line for line in commands)


@pytest.mark.parametrize("attack", ["line-size", "line-count", "session-bytes"])
def test_real_transport_stops_response_floods(peer, monkeypatch, attack):
    if attack == "line-size":
        greeting = b"* OK " + b"x" * 32768 + b"\r\n"
        expected = "line exceeds"
    elif attack == "line-count":
        greeting = b"* OK padding\r\n" * 2049
        expected = "Too many"
    else:
        # Real 4 MiB budget, with individual lines below the line-size bound.
        greeting = (b"* OK " + b"x" * 30000 + b"\r\n") * 140
        expected = "budget exceeded"
    with peer(lambda *a: None, greeting) as (port, commands):
        with pytest.raises(ValueError, match=expected):
            transport.Client("mail.example", port, "", time.monotonic() + 3)
    assert not any(b" LOGIN " in line for line in commands)


def test_referral_and_server_secrets_do_not_escape_child_output(peer, tls_files):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    secret = b"UPSTREAM-RESPONSE-SECRET"
    with peer(
        lambda line, tag: (
            tag
            + b" NO [REFERRAL imaps://reader:"
            + secret
            + b"@other.example/INBOX] denied\r\n"
        )
    ) as (port, commands):
        code = """import ssl, sys
from app.ingest.imap_support import transport, worker
original = ssl.create_default_context
transport.ssl.create_default_context = lambda: original(cafile=sys.argv[1])
transport.addresses = lambda *a: ['127.0.0.1']
worker.main()
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(tls_files[0])],
            input=json.dumps(
                {
                    "configuration": dict(
                        host="mail.example",
                        port=port,
                        username="reader",
                        password="synthetic-secret",
                        folder="INBOX",
                        fetch_body=False,
                        body_max_chars=4000,
                    ),
                    "cursor": None,
                    "allowed_cidrs": "",
                }
            ).encode(),
            capture_output=True,
            cwd=Path(__file__).resolve().parents[1],
            env={"PATH": os.defpath, "PYTHONIOENCODING": "utf-8"},
            timeout=4,
        )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        "error": "IMAP collection failed a connection, protocol or resource check"
    }
    assert not result.stderr
    assert secret not in result.stdout and b"synthetic-secret" not in result.stdout
    # The referral answers EXAMINE and the client follows it nowhere. Newer
    # imaplib releases ask for capabilities again after LOGIN.
    verbs = [command.split()[1] for command in commands]
    assert [verb for verb in verbs if verb != b"CAPABILITY"] == [b"LOGIN", b"EXAMINE"]
    assert verbs[-1] == b"EXAMINE"


@pytest.mark.parametrize(
    "values", [[b"0"], [b"4294967296"], [b"7 trailing"], [b"7", b"bad"]]
)
def test_invalid_epoch_values_are_rejected(values):
    class Peer:
        def response(self, key):
            return key, values

    with pytest.raises(ValueError):
        worker.number(Peer(), "UIDVALIDITY")


def test_identical_repeated_epoch_is_accepted():
    class Peer:
        def response(self, key):
            return key, [b"7", b"7"]

    assert worker.number(Peer(), "UIDVALIDITY") == 7


def test_child_alarm_stops_slow_drip_inside_an_allowed_literal(peer, tls_files):
    import json
    import os
    import signal
    import subprocess
    import sys
    from pathlib import Path

    sent = []

    def drip(secured):
        secured.sendall(
            b"* 1 FETCH (UID 2 BODY[HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID)]<0> {1000}\r\n"
        )
        for _ in range(1000):
            secured.sendall(b"x")
            sent.append(1)
            time.sleep(0.03)

    def respond(line, tag):
        done = tag + b" OK done\r\n"
        if b" EXAMINE " in line:
            return (
                b"* 2 EXISTS\r\n* OK [UIDVALIDITY 7] epoch\r\n* OK [UIDNEXT 3] next\r\n"
                + done
            )
        if b" UID SEARCH " in line:
            return b"* SEARCH 2\r\n" + done
        if b"RFC822.SIZE" in line:
            return b"* 1 FETCH (UID 2 RFC822.SIZE 1000)\r\n" + done
        return drip

    with peer(respond) as (port, commands):
        code = """import ssl, sys
from app.ingest.imap_support import transport, worker
original = ssl.create_default_context
transport.ssl.create_default_context = lambda: original(cafile=sys.argv[1])
transport.addresses = lambda *a: ['127.0.0.1']
worker.COLLECTION_TIMEOUT = 1
worker.main()
"""
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-c", code, str(tls_files[0])],
            input=json.dumps(
                {
                    "configuration": dict(
                        host="mail.example",
                        port=port,
                        username="reader",
                        password="synthetic-secret",
                        folder="INBOX",
                        fetch_body=False,
                        body_max_chars=4000,
                    ),
                    "cursor": {"uidvalidity": 7, "last_uid": 1},
                    "allowed_cidrs": "",
                }
            ).encode(),
            capture_output=True,
            cwd=Path(__file__).resolve().parents[1],
            env={"PATH": os.defpath, "PYTHONIOENCODING": "utf-8"},
            timeout=4,
        )
    assert result.returncode == -signal.SIGALRM
    assert time.monotonic() - started < 3
    assert len(sent) > 5
    assert not result.stdout and not result.stderr
    assert any(b"BODY.PEEK[HEADER.FIELDS" in line for line in commands)


@pytest.mark.parametrize("masking", ["false", "true"])
def test_server_header_cannot_overflow_event_display_columns(
    contract_db, monkeypatch, masking
):
    import json
    from types import SimpleNamespace

    from app.core.config import settings
    from app.db.models import Event, IngestionCursor
    from app.ingest import common, connections, imap

    # Long but bounded header; short email addresses also expand under masking.
    sender = "a@b.co, " * 100
    host = ".".join(["a" * 60] * 3)
    monkeypatch.setattr(settings, "imap_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "event_data_masking", masking)
    connection = connections.Connection(
        "hostile-header",
        "imap",
        "Hostile header",
        {"host": host, "username": "reader"},
        {"password": "synthetic-secret"},
        {},
    )
    monkeypatch.setattr(connections, "connections", lambda *args: [connection])
    item = {"uid": 2, "headers": {"subject": "Report", "from": sender}, "body": None}
    monkeypatch.setattr(
        imap, "fetch", lambda *args: {"uidvalidity": 7, "last_uid": 2, "items": [item]}
    )
    blobs = []

    def put(**kwargs):
        blobs.append(kwargs["data"])
        return SimpleNamespace(
            uri="local://test", sha256="0" * 64, size_bytes=len(kwargs["data"])
        )

    monkeypatch.setattr(common, "put_bytes", put)
    monkeypatch.setattr(common, "apply_rules", lambda *args: 0)
    result = imap.ingest_imap_all(contract_db)
    assert result[0].get("ok"), result
    event = contract_db.query(Event).one()
    assert len(event.actor) <= Event.__table__.c.actor.type.length
    assert len(event.system) <= Event.__table__.c.system.type.length
    assert contract_db.query(IngestionCursor).one().meta["last_uid"] == 2
    original = event.normalized_payload["mail"]["headers"]["from"]
    assert len(original) > 128
    assert json.loads(blobs[0])["mail"]["headers"]["from"] == original
    if masking == "true":
        assert "a@b.co" not in original and "a@b.co" not in event.actor
    else:
        assert original == sender


@pytest.mark.parametrize("max_chars", [4000, 65536])
def test_unicode_batch_respects_serialised_output_budget_and_can_resume(
    monkeypatch, max_chars
):
    import json

    body = ("\U0001f680" * 65536).encode()

    class LargePeer:
        def __init__(self, *args):
            self.received = 0

        def login(self, *args):
            return "OK", []

        def select(self, *args, **kwargs):
            return "OK", []

        def response(self, key):
            return key, [b"7" if key == "UIDVALIDITY" else b"11"]

        def uid(self, command, *args):
            if command == "SEARCH":
                start = int(args[-1].split(":")[0])
                return "OK", [b" ".join(str(i).encode() for i in range(start, 11))]
            uid = args[0].encode()
            if "BODYSTRUCTURE" in args[-1]:
                return "OK", [
                    b"1 (UID "
                    + uid
                    + b' RFC822.SIZE 300000 BODYSTRUCTURE ("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "8BIT" 262144 1))'
                ]
            section = args[-1].split("BODY.PEEK[", 1)[1].split("]", 1)[0].encode()
            data = (
                b"Subject: Large report\r\n\r\n"
                if section.startswith(b"HEADER")
                else body
            )
            limit = int(args[-1].split("<0.", 1)[1].split(">", 1)[0])
            data = data[:limit]
            self.received += len(data)
            return "OK", [
                (
                    b"1 (UID "
                    + uid
                    + b" BODY["
                    + section
                    + b"]<0> {"
                    + str(len(data)).encode()
                    + b"}",
                    data,
                ),
                b")",
            ]

        def shutdown(self):
            pass

    monkeypatch.setattr(worker, "Client", LargePeer)
    config = dict(
        host="mail.example",
        port=993,
        username="reader",
        password="synthetic-secret",
        folder="INBOX",
        fetch_body=True,
        body_max_chars=max_chars,
    )
    first = worker.collect(config, {"uidvalidity": 7, "last_uid": 0}, "")
    assert len(json.dumps(first, ensure_ascii=True).encode()) <= 5 * 1024 * 1024
    assert 0 < first["last_uid"] <= 10
    assert (first["last_uid"] == 10) == (max_chars == 4000)
    second = worker.collect(
        config, {"uidvalidity": 7, "last_uid": first["last_uid"]}, ""
    )
    assert len(json.dumps(second, ensure_ascii=True).encode()) <= 5 * 1024 * 1024
    assert [item["uid"] for item in first["items"] + second["items"]] == list(
        range(1, 11)
    )
    assert second["last_uid"] == 10
