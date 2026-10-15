"""Runner authority and third-pass IMAP audit reproductions."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.core.config import settings
from app.ingest.imap_support import worker
from app.ingest.imap_support.mime import decode_body
from app.worker import tasks

from tests.test_assembled_authority_regressions import keen as keen
from tests.test_assembled_authority_regressions import unsafe
from tests.test_imap import Peer

CONFIG = dict(
    host="mail.example",
    port=993,
    username="u",
    password="p",
    folder="INBOX",
    fetch_body=True,
    body_max_chars=4000,
)


def test_unsupported_charset_cannot_bypass_sensitive_key_redaction():
    with pytest.raises(ValueError, match="charset"):
        decode_body(
            "token=synthetic-secret".encode("utf-16-le"),
            {"encoding": "8bit", "charset": "utf-16le"},
            4000,
        )


def test_headers_only_never_requests_mime_structure(monkeypatch):
    class HeadersPeer(Peer):
        def uid(self, command, *args):
            if command == "FETCH" and "BODYSTRUCTURE" in args[-1]:
                pytest.fail(
                    "Headers-only collection must not parse remote MIME structure"
                )
            if command == "FETCH" and args[-1] == "(UID RFC822.SIZE)":
                return "OK", [b"1 (UID 2 RFC822.SIZE 100)"]
            return super().uid(command, *args)

    monkeypatch.setattr(worker, "Client", HeadersPeer)
    result = worker.collect(
        {**CONFIG, "fetch_body": False}, {"uidvalidity": 7, "last_uid": 1}, ""
    )
    assert result["last_uid"] == 2
    assert result["items"][0]["headers"]["subject"] == "Test report"


def test_invalid_body_encoding_retains_headers_and_advances_cursor(monkeypatch):
    class InvalidBodyPeer(Peer):
        def uid(self, command, *args):
            if command == "FETCH" and "BODYSTRUCTURE" in args[-1]:
                return "OK", [
                    b'1 (UID 2 RFC822.SIZE 100 BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "BASE64" 12 1))'
                ]
            if command == "FETCH" and "BODY.PEEK[1]" in args[-1]:
                return "OK", [(b"1 (UID 2 BODY[1]<0> {4}", b"!!!!"), b")"]
            return super().uid(command, *args)

    monkeypatch.setattr(worker, "Client", InvalidBodyPeer)
    result = worker.collect(CONFIG, {"uidvalidity": 7, "last_uid": 1}, "")
    assert result["last_uid"] == 2
    item = result["items"][0]
    assert item["headers"]["subject"] == "Test report"
    assert item["body"] is None
    assert item["body_omitted_reason"] == "invalid_or_unsupported_body_encoding"


@pytest.mark.parametrize("role", [None, "normal"])
def test_runner_rejects_unprivileged_callers_without_queueing(keen, monkeypatch, role):
    dispatch = Mock()
    monkeypatch.setattr(tasks.dispatch_imap_task, "apply_async", dispatch)
    client = keen.client(keen.user(role=role) if role else None)
    response = client.post(
        "/v1/admin/ingest/imap/run", json={}, headers=unsafe(client) if role else {}
    )
    assert response.status_code in (401, 403)
    dispatch.assert_not_called()


def test_runner_requires_csrf_and_queues_only_dispatch(keen, monkeypatch):
    monkeypatch.setattr(settings, "imap_enabled", True)
    dispatch = Mock(return_value=SimpleNamespace(id="synthetic-job"))
    monkeypatch.setattr(tasks.dispatch_imap_task, "apply_async", dispatch)
    monkeypatch.setattr(
        worker, "Client", Mock(side_effect=AssertionError("No network on HTTP path"))
    )
    client = keen.client(keen.user(role="admin"))
    path = "/v1/admin/ingest/imap/run"
    assert client.post(path, json={}).status_code == 403
    dispatch.assert_not_called()
    response = client.post(path, json={}, headers=unsafe(client))
    assert response.status_code == 202, response.text
    assert response.json()["queued"] is True
    dispatch.assert_called_once_with(expires=240)


@pytest.mark.parametrize("state", ["disabled", "demo", "paused"])
def test_runner_rejects_unavailable_ingestion(keen, monkeypatch, state):
    from app.db.models import SourceIngestionState

    monkeypatch.setattr(settings, "imap_enabled", state != "disabled")
    if state == "paused":
        keen.db.add(SourceIngestionState(source="imap", paused=True))
        keen.db.commit()
    dispatch = Mock()
    monkeypatch.setattr(tasks.dispatch_imap_task, "apply_async", dispatch)
    client = keen.client(keen.user(role="admin"))
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(
        settings, "demo_expires_at", datetime.now(timezone.utc) + timedelta(hours=1)
    )
    monkeypatch.setattr(settings, "demo_mode", state == "demo")
    response = client.post("/v1/admin/ingest/imap/run", json={}, headers=unsafe(client))
    assert response.status_code in (403, 409), response.text
    dispatch.assert_not_called()


def test_wrong_body_uid_still_fails_poll(monkeypatch):
    class WrongUidPeer(Peer):
        def uid(self, command, *args):
            if command == "FETCH" and "BODY.PEEK[3]" in args[-1]:
                return "OK", [(b"1 (UID 99 BODY[3]<0> {4}", b"!!!!"), b")"]
            return super().uid(command, *args)

    monkeypatch.setattr(worker, "Client", WrongUidPeer)
    with pytest.raises(ValueError, match="identity"):
        worker.collect(CONFIG, {"uidvalidity": 7, "last_uid": 1}, "")
    assert Peer.last.closed


@pytest.mark.parametrize(
    "body,reason",
    [
        (
            b'("TEXT" "PLAIN" ("CHARSET" "UTF-16LE") NIL NIL "8BIT" 12 1)',
            "invalid_or_unsupported_body_encoding",
        ),
        (
            b'("TEXT" "PLAIN" NIL NIL NIL "X-UNKNOWN" 12 1)',
            "invalid_or_unsupported_body_encoding",
        ),
    ],
)
def test_unsupported_body_metadata_omits_content(monkeypatch, body, reason):
    class UnsupportedPeer(Peer):
        def uid(self, command, *args):
            if command == "FETCH" and "BODYSTRUCTURE" in args[-1]:
                return "OK", [b"1 (UID 2 RFC822.SIZE 100 BODYSTRUCTURE " + body + b")"]
            return super().uid(command, *args)

    monkeypatch.setattr(worker, "Client", UnsupportedPeer)
    result = worker.collect(CONFIG, {"uidvalidity": 7, "last_uid": 1}, "")
    assert result["last_uid"] == 2
    assert result["items"][0]["body"] is None
    assert result["items"][0]["body_omitted_reason"] == reason


def test_dispatch_rechecks_pause_before_queueing(contract_db, monkeypatch):
    from contextlib import nullcontext

    from app.db.models import SourceIngestionState

    monkeypatch.setattr(settings, "imap_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", False)
    contract_db.add(SourceIngestionState(source="imap", paused=True))
    contract_db.commit()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: nullcontext(contract_db))
    queued = Mock()
    monkeypatch.setattr(tasks.ingest_imap_connection_task, "delay", queued)
    assert tasks.dispatch_imap_task() == {"queued": 0, "paused": True}
    queued.assert_not_called()


@pytest.mark.parametrize(
    "value", ["0", "1", "4", "6", "1445", "-5", "5.0", "", "99999", True, 5]
)
def test_collection_interval_rejects_unsafe_or_ambiguous_values(value):
    from app.ingest.imap_support.config import configuration

    with pytest.raises(ValueError, match="interval"):
        configuration(
            {
                "host": "mail.example",
                "username": "reader",
                "poll_interval_minutes": value,
            }
        )


def test_collection_interval_is_connection_local_and_applies_to_manual_runs(
    contract_db, monkeypatch
):
    from datetime import timedelta

    from app.ingest import connections, imap
    from app.ingest.imap_support.config import configuration

    assert (
        configuration({"host": "mail.example", "username": "reader"})[
            "poll_interval_minutes"
        ]
        == 5
    )
    assert (
        configuration(
            {
                "host": "mail.example",
                "username": "reader",
                "poll_interval_minutes": "1440",
            }
        )["poll_interval_minutes"]
        == 1440
    )
    monkeypatch.setattr(settings, "imap_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", False)
    peers = [
        connections.Connection(
            key,
            "imap",
            key,
            {"host": "mail.example", "username": key, "poll_interval_minutes": minutes},
            {"password": "p"},
            {},
        )
        for key, minutes in [("fast", "5"), ("slow", "30")]
    ]
    monkeypatch.setattr(connections, "connections", lambda *a: peers)
    now = imap.utc_now_naive()
    clock = [now]
    monkeypatch.setattr(imap, "utc_now_naive", lambda: clock[0])
    fetch = Mock(return_value={"uidvalidity": 7, "last_uid": 20, "items": []})
    monkeypatch.setattr(imap, "fetch", fetch)
    assert all(row["ok"] for row in imap.ingest_imap_all(contract_db))
    assert fetch.call_count == 2
    clock[0] = now + timedelta(seconds=299)
    assert imap.ingest_imap_all(contract_db, connection_id="fast")[0]["skipped"]
    assert fetch.call_count == 2
    clock[0] = now + timedelta(minutes=5)
    assert imap.ingest_imap_all(contract_db, connection_id="fast")[0]["ok"]
    assert imap.ingest_imap_all(contract_db, connection_id="slow")[0]["skipped"]
    clock[0] = now + timedelta(minutes=30)
    assert imap.ingest_imap_all(contract_db, connection_id="slow")[0]["ok"]
    assert fetch.call_count == 4
    assert fetch.call_args.args[1] == {"uidvalidity": 7, "last_uid": 20}


def test_failed_attempt_still_enforces_interval_after_backoff_expires(
    contract_db, monkeypatch
):
    from datetime import timedelta

    from app.db.models import IngestionCursor
    from app.ingest import connections, imap

    monkeypatch.setattr(settings, "imap_enabled", True)
    monkeypatch.setattr(settings, "demo_mode", False)
    peer = connections.Connection(
        "slow-failure",
        "imap",
        "Slow failure",
        {"host": "mail.example", "username": "reader", "poll_interval_minutes": "30"},
        {"password": "p"},
        {},
    )
    monkeypatch.setattr(connections, "connections", lambda *a: [peer])
    now = imap.utc_now_naive()
    clock = [now]
    monkeypatch.setattr(imap, "utc_now_naive", lambda: clock[0])
    fetch = Mock(side_effect=ValueError("synthetic-secret"))
    monkeypatch.setattr(imap, "fetch", fetch)
    assert imap.ingest_imap_all(contract_db)[0]["ok"] is False
    clock[0] = now + timedelta(minutes=3)
    assert imap.ingest_imap_all(contract_db)[0]["reason"] == "IMAP collection interval"
    assert fetch.call_count == 1
    row = contract_db.query(IngestionCursor).one()
    assert "uidvalidity" not in row.meta
    assert "synthetic-secret" not in str(row.meta)
    clock[0] = now + timedelta(minutes=30)
    imap.ingest_imap_all(contract_db)
    assert fetch.call_count == 2


def test_admin_connection_rejects_interval_below_minimum(keen, monkeypatch):
    monkeypatch.setattr(settings, "imap_enabled", True)
    client = keen.client(keen.user(role="admin"))
    response = client.post(
        "/v1/admin/source-connections",
        headers=unsafe(client),
        json={
            "source": "imap",
            "name": "Too rapid",
            "configuration": {
                "host": "mail.example",
                "username": "reader",
                "poll_interval_minutes": "1",
            },
            "credentials": {"password": "synthetic-secret"},
            "inputs": {},
        },
    )
    assert response.status_code == 422, response.text
    assert "interval" in response.text
    assert "synthetic-secret" not in response.text
