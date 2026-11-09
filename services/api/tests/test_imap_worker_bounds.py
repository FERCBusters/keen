"""The IMAP worker's own bounds: destinations, deadlines, attachments and entry.

The worker resolves its destination itself and refuses an empty or oversized
address set, makes no connection once its deadline has passed, never takes an
attached part as a message body, and, run as a program, limits its own
resources before reading a request. Fake peers stand in for the mail server.
"""

import json
import resource
import runpy
import signal
import socket
import sys
import time

import pytest
from app.ingest.imap_support import transport, worker
from app.ingest.imap_support.mime import body_part, structure

from tests.test_imap import Peer, _Stdin, _Stdout

# A plain-text part offered as a download: no NAME parameter, only a filename.
DOWNLOAD = (
    b'("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 12 1 NIL '
    b'("ATTACHMENT" ("FILENAME" "notes.txt")))'
)
CONFIG = {
    "host": "mail.example",
    "port": 993,
    "username": "reader",
    "password": "synthetic-secret",
    "folder": "INBOX",
    "fetch_body": True,
    "body_max_chars": 100,
}


def resolving(monkeypatch, count):
    rows = [(None, None, None, None, (f"203.0.113.{n}", 993)) for n in range(count)]
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *a, **k: rows)


@pytest.mark.parametrize("count", [0, 17], ids=["none", "too-many"])
def test_a_destination_without_a_usable_address_set_is_refused(monkeypatch, count):
    resolving(monkeypatch, count)
    with pytest.raises(ValueError, match="Invalid IMAP address set"):
        transport.addresses("mail.example", 993, "")


def test_sixteen_allowed_addresses_are_accepted_in_resolver_order(monkeypatch):
    resolving(monkeypatch, 16)
    assert transport.addresses("mail.example", 993, "203.0.113.0/24")[:2] == [
        "203.0.113.0",
        "203.0.113.1",
    ]


def test_no_connection_is_made_once_the_deadline_has_passed(monkeypatch):
    monkeypatch.setattr(transport, "addresses", lambda *a: ["203.0.113.5"])

    def connect(*args, **kwargs):
        pytest.fail("a connection must not start after the deadline")

    monkeypatch.setattr(socket, "create_connection", connect)
    with pytest.raises(TimeoutError, match="IMAP deadline"):
        transport.Client("mail.example", 993, "", time.monotonic() - 1)


def test_a_part_offered_as_a_download_is_never_a_message_body():
    assert body_part(structure(DOWNLOAD)) is None
    plain = b'("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 5 1 NIL NIL)'
    mixed = structure(b"(" + DOWNLOAD + plain + b' "MIXED")')
    assert body_part(mixed)["section"] == "2"


class AddressPeer(Peer):
    def uid(self, command, *args):
        if command == "FETCH" and "HEADER.FIELDS" in args[1]:
            data = (
                b"Subject: Test report\r\n"
                b"From: Alice <alice@example.test>\r\n"
                b"To: Bob <bob@example.test>, carol@example.test\r\n"
                b"Cc: dave@example.test\r\n\r\n"
            )
            section = args[1].split("BODY.PEEK[", 1)[1].split("]", 1)[0]
            return "OK", [
                (
                    f"1 (UID {args[0]} BODY[{section}]<0> {{{len(data)}}}".encode(),
                    data,
                ),
                b")",
            ]
        return super().uid(command, *args)


def test_address_headers_are_masked_whether_or_not_masking_is_enabled(monkeypatch):
    monkeypatch.setattr(worker, "Client", AddressPeer)

    result = worker.collect(CONFIG, {"uidvalidity": 7, "last_uid": 1}, "")

    (item,) = result["items"]
    assert item["headers"]["from"] == "[MASKED_EMAIL]"
    assert item["headers"]["to"] == "[MASKED_EMAIL]"
    assert "cc" not in item["headers"]
    assert item["headers"]["subject"] == "Test report"


class DownloadOnlyPeer(Peer):
    def uid(self, command, *args):
        if command == "FETCH" and "BODYSTRUCTURE" in args[1]:
            self.commands.append((command, *args))
            return "OK", [b"1 (UID 2 RFC822.SIZE 100 BODYSTRUCTURE " + DOWNLOAD + b")"]
        return super().uid(command, *args)


def test_a_message_whose_only_text_is_a_download_is_kept_without_a_body(
    monkeypatch,
):
    monkeypatch.setattr(worker, "Client", DownloadOnlyPeer)

    result = worker.collect(CONFIG, {"uidvalidity": 7, "last_uid": 1}, "")

    (item,) = result["items"]
    assert item["headers"]["subject"] == "Test report"
    assert (item["body"], item["body_omitted_reason"]) == (
        None,
        "no_supported_inline_body",
    )
    # Only the headers are fetched; the attached part is never read.
    fetched = [c[-1] for c in Peer.last.commands if c[0] == "FETCH"]
    assert fetched == [
        "(UID RFC822.SIZE BODYSTRUCTURE)",
        "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID)]<0.16384>)",
    ]
    assert result["last_uid"] == 2


def test_the_worker_run_as_a_program_limits_itself_and_refuses_bad_input(
    monkeypatch,
):
    limits, alarms, out = [], [], _Stdout()
    monkeypatch.setattr(resource, "setrlimit", lambda kind, value: limits.append(kind))
    monkeypatch.setattr(signal, "alarm", alarms.append)
    monkeypatch.setattr(sys, "stdin", _Stdin(b'{"configuration": "password=hunter2'))
    monkeypatch.setattr(sys, "stdout", out)
    # Run the module afresh, as `python -m` does.
    monkeypatch.delitem(sys.modules, worker.__name__)

    with pytest.raises(SystemExit) as stop:
        runpy.run_module(worker.__name__, run_name="__main__")

    assert stop.value.code == 1
    assert set(limits) == {
        resource.RLIMIT_CORE,
        resource.RLIMIT_AS,
        resource.RLIMIT_CPU,
        resource.RLIMIT_NOFILE,
    }
    assert alarms == [50]
    output = "".join(out.text)
    assert json.loads(output) == {
        "error": "IMAP collection failed a connection, protocol or resource check"
    }
    assert "hunter2" not in output
