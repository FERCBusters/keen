"""Cloudflare audit log collection runs from the scheduled entry point.

A connection supplies an account ID and an API token. Each run reads the
account's audit log in time windows, oldest first, and stores every page
before the next. An in-memory transport stands in for Cloudflare's API;
its entries have the shape of a real response, with addresses and
identifiers replaced.
"""

import hashlib
import json
from datetime import datetime, timedelta
from http.cookiejar import CookieJar
from types import SimpleNamespace
from unittest.mock import patch

import httpx2 as httpx
import pytest
from app.core import task_deadline
from app.core.config import settings
from app.db.models import Artifact, Event, IngestionCursor
from app.ingest import cloudflare
from app.ingest.connections import Connection, connection_scope, namespace
from app.mapping.collector import (
    collector_id,
    collector_pointer_filter,
    event_collector,
)
from celery.exceptions import SoftTimeLimitExceeded

from tests.task_limits import checked_statements, stops_wherever_the_deadline_falls
from tests.transactions import open_while_answering

ACCOUNT = "ACraC9ywjzvAaic73ng9FCthpxFgsCts"
OTHER_ACCOUNT = "g2iEdmackkhmpt2345oh5Auqdxzwp9ve"
TOKEN = "synthetic-cloudflare-api-token"
MARKER = "upstream-body-marker"
NOW = datetime(2026, 11, 19, 12, 0, 0)
RESULT = {"connection_id": "db:cloudflare", "connection_name": "Cloudflare"}
LISTING = f"/client/v4/accounts/{ACCOUNT}/logs/audit"


def entry(ident, when, description="List DNS Records", kind="view", **fields):
    """One audit log entry as Cloudflare lists it."""
    item = {
        "account": {"id": ACCOUNT, "name": "ACMe Studios"},
        "action": {
            "description": description,
            "result": "success",
            "time": when,
            "type": kind,
        },
        "actor": {
            "context": "dash",
            "email": "bob@example.com",
            "id": "AdbeAEiovE5rv3kdqhy3bFgsy7c4CDp4",
            "ip_address": "192.65.3.72",
            "type": "user",
        },
        "id": ident,
        "raw": {
            "cf_ray_id": "na7Dg4zxDdep5bns",
            "method": "GET",
            "status_code": 200,
            "uri": "/zones/5bmqpurDcxb9dbiggeF4ix9qjD7cjwas/dns_records",
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:159.0)",
        },
        "resource": {"product": "dns_records", "scope": "zones"},
        "zone": {"id": "5bmqpurDcxb9dbiggeF4ix9qjD7cjwas", "name": "example.org"},
    }
    item.update(fields)
    return item


LOGIN = {
    "account": {"id": ACCOUNT, "name": "ACMe Studios"},
    "action": {
        "description": "LOGIN",
        "result": "success",
        "time": "2026-11-19T07:04:54Z",
        "type": "update",
    },
    "actor": {
        "context": "api",
        "email": "alice@example.com",
        "id": "7Fa3nm3mk33nAqECCoosv4t3urvjw3DD",
        "ip_address": "201.0.99.3",
        "type": "user",
    },
    "id": "fa9d7c02-027a-4b04-b42d-cf380907a61c",
    "resource": {
        "id": "7Fa3nm3mk33nAqECCoosv4t3urvjw3DD",
        "product": "",
        "scope": "user",
        "type": "user",
    },
}
DNS_CREATED = entry(
    "14e26663-9f62-4fc5-8665-03b2d8fe7843",
    "2026-11-18T22:54:12.234Z",
    description="Create DNS Record",
    kind="create",
    resource={
        "product": "dns_records",
        "request": {
            "comment": "",
            "content": '"site-verification=synthetic"',
            "name": "example.org",
            "proxied": False,
            "tags": [],
            "type": "TXT",
        },
        "response": {
            "content": '"site-verification=synthetic"',
            "id": "mvbc2wyFi4x5xvpDvaroawkq3xkiqx7m",
            "name": "example.org",
            "type": "TXT",
        },
        "scope": "zones",
    },
)
EARLY = entry("01a1e000-0000-7000-8000-000000000001", "2026-11-18T08:00:00.5Z")


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


class Cloudflare:
    """Cloudflare's audit log for one account, paged ``size`` entries at a time."""

    def __init__(self):
        self.requests = []
        self.entries = [EARLY, DNS_CREATED, LOGIN]
        self.size = None
        # Cloudflare may hand back a paging value with the last page too.
        self.cursor_on_last = False
        # A test's own answer to a request, or None for the usual one.
        self.answer = lambda request: None

    def __call__(self, request):
        self.requests.append(request)
        task_deadline.check()  # as the ingestion transport does before sending
        answer = self.answer(request)
        if answer is not None:
            return answer
        assert request.url.host == "api.cloudflare.com", request.url
        account = request.url.path.split("/")[4]
        params = request.url.params
        since, before = instant(params["since"]), instant(params["before"])
        # Both bounds exclude an entry at that exact time.
        rows = sorted(
            (
                e
                for e in self.entries
                if isinstance(e, dict)
                and e["account"]["id"] == account
                and since < instant(e["action"]["time"]) < before
            ),
            key=lambda e: e["action"]["time"],
            reverse=params["direction"] == "desc",
        )
        # Entries that are no object are listed in every window.
        rows += [e for e in self.entries if not isinstance(e, dict)]
        size = self.size or int(params["limit"])
        offset = int(params.get("cursor", "c0")[1:])
        following = offset + size
        more = following < len(rows) or self.cursor_on_last and offset < len(rows)
        return httpx.Response(
            200,
            json={
                "errors": [],
                "result": rows[offset:following],
                "result_info": {
                    "count": len(rows[offset:following]),
                    "cursor": f"c{following}" if more else "",
                },
                "success": True,
            },
        )

    def listings(self):
        return [dict(r.url.params) for r in self.requests]


@pytest.fixture
def account(contract_db, monkeypatch, tmp_path):
    for name, value in {
        "demo_mode": False,
        "cloudflare_enabled": True,
        "event_data_masking": "false",
        "artifact_storage_backend": "local",
        "artifact_local_dir": str(tmp_path),
    }.items():
        monkeypatch.setattr(settings, name, value)
    api = Cloudflare()
    clients = []

    def client(**kwargs):
        clients.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(api), **kwargs)

    monkeypatch.setattr(cloudflare, "ingestion_client", client)
    clock = SimpleNamespace(now=NOW)
    monkeypatch.setattr(cloudflare, "utc_now_naive", lambda: clock.now)
    inputs = {
        "streams": [{"stream": "audit", "label": "Production account"}],
        "initial_lookback_days": 2,
    }
    state = SimpleNamespace(account=ACCOUNT, token=TOKEN)

    def connection(key="db:cloudflare", account_id=None):
        return Connection(
            key,
            "cloudflare",
            "Cloudflare",
            {"account_id": account_id or state.account},
            {"api_token": state.token},
            inputs,
        )

    def run(*chosen):
        with patch(
            "app.ingest.connections.connections",
            return_value=list(chosen) or [connection()],
        ):
            return cloudflare.ingest_cloudflare_all(contract_db)

    def cursor(key="db:cloudflare", account_id=ACCOUNT):
        digest = hashlib.sha256(account_id.encode()).hexdigest()
        with connection_scope(connection(key, account_id)):
            name = namespace("cloudflare:" + digest)
        return contract_db.query(IngestionCursor).filter_by(name=name).one_or_none()

    def events():
        return (
            contract_db.query(Event)
            .filter_by(source="cloudflare")
            .order_by(Event.timestamp, Event.action)
            .all()
        )

    return SimpleNamespace(
        db=contract_db,
        api=api,
        clients=clients,
        clock=clock,
        inputs=inputs,
        state=state,
        connection=connection,
        run=run,
        cursor=cursor,
        events=events,
    )


def test_a_run_reads_the_audit_log_in_windows_oldest_first(account):
    (result,) = account.run()

    assert result == {
        "stream": "audit",
        "pages": 2,
        "created_events": 3,
        "invalid_records": 0,
        "more": False,
        "cursor": "2026-11-19T12:00:00Z",
        **RESULT,
    }
    # The token is the client's one credential, sent to Cloudflare's API alone,
    # which answers without redirects and keeps no cookie.
    (options,) = account.clients
    assert options["headers"] == {
        "Authorization": "Bearer " + TOKEN,
        "Accept": "application/json",
    }
    assert options["follow_redirects"] is False and options["verify"] is True
    assert isinstance(options["cookies"], CookieJar)
    for request in account.api.requests:
        assert (request.method, request.url.scheme, request.url.host) == (
            "GET",
            "https",
            "api.cloudflare.com",
        )
        assert request.url.path == LISTING and request.content == b""
        assert TOKEN not in str(request.url)
    # Two day-long windows from the lookback to now, each reaching back an hour
    # and one second past its end.
    assert account.api.listings() == [
        {
            "since": "2026-11-17T11:00:00Z",
            "before": "2026-11-18T12:00:01Z",
            "direction": "asc",
            "limit": "500",
        },
        {
            "since": "2026-11-18T11:00:00Z",
            "before": "2026-11-19T12:00:01Z",
            "direction": "asc",
            "limit": "500",
        },
    ]

    early, created, login = account.events()
    assert (
        created.action,
        created.outcome,
        created.severity,
        created.actor,
        created.system,
        created.timestamp,
        created.summary,
    ) == (
        "dns_records.create",
        "success",
        3,
        "bob@example.com",
        "Production account",
        datetime(2026, 11, 18, 22, 54, 12, 234000),
        "Cloudflare: Create DNS Record in example.org by bob@example.com",
    )
    # A change keeps what was asked for and what Cloudflare answered.
    assert created.normalized_payload == {"stream": "audit", "event": DNS_CREATED}
    # A user's own action is named by its scope.
    assert (login.action, login.summary) == (
        "user.update",
        "Cloudflare: LOGIN by alice@example.com",
    )
    assert early.action == "dns_records.view"
    assert created.raw_pointer == {
        "cloudflare": {"section": "streams", "stream": "audit"}
    }
    identity = collector_id("cloudflare", "streams", "audit")
    assert (
        event_collector({"source": "cloudflare", "raw_pointer": created.raw_pointer})
        == identity
    )
    assert collector_pointer_filter(identity) == {"cloudflare": {"stream": "audit"}}
    artifact = account.db.query(Artifact).filter_by(event_id=created.id).one()
    assert artifact.kind == "cloudflare_audit"
    cursor = account.cursor()
    assert (cursor.last_ts, cursor.meta) == (NOW, {})


def test_a_later_run_reads_from_the_cursor_and_stores_nothing_twice(account):
    account.run()
    account.api.requests.clear()
    account.clock.now = NOW + timedelta(minutes=15)
    account.api.entries.append(
        entry("01a1f2b4-0000-7000-8000-00000000000f", "2026-11-19T12:05:00Z")
    )

    (result,) = account.run()

    # The window reaches back an hour, so entries already stored are read again
    # and recognised.
    assert account.api.listings() == [
        {
            "since": "2026-11-19T11:00:00Z",
            "before": "2026-11-19T12:15:01Z",
            "direction": "asc",
            "limit": "500",
        }
    ]
    assert (result["created_events"], len(account.events())) == (1, 4)


def test_an_entry_is_told_apart_by_its_identifier(account):
    account.run()
    # Cloudflare's identifier names the entry, whatever else it reads as.
    account.api.entries[1] = {
        **DNS_CREATED,
        "raw": {**DNS_CREATED["raw"], "user_agent": "changed"},
    }
    account.clock.now = NOW + timedelta(minutes=1)
    account.inputs["overlap_minutes"] = 1440

    (result,) = account.run()

    assert (result["created_events"], len(account.events())) == (0, 3)


def test_a_run_with_nothing_new_to_read_asks_for_nothing(account):
    account.run()
    account.api.requests.clear()

    (result,) = account.run()

    assert account.api.requests == []
    assert result["pages"] == 0 and result["more"] is False


def test_a_window_is_paged_and_its_cursor_moves_only_when_it_is_complete(account):
    account.inputs.update(initial_lookback_days=1, max_pages=2, page_size=1)
    account.api.entries = [
        entry(f"01a1f2b4-0000-7000-8000-00000000000{n}", f"2026-11-19T0{n}:00:00Z")
        for n in range(1, 5)
    ]

    (first,) = account.run()

    assert [params.get("cursor") for params in account.api.listings()] == [None, "c1"]
    assert (first["pages"], first["more"]) == (2, True)
    cursor = account.cursor()
    assert cursor.last_ts == NOW - timedelta(days=1)
    assert cursor.meta == {"until": NOW.isoformat(), "token": "c2"}

    account.inputs["max_pages"] = 10
    account.api.requests.clear()
    (second,) = account.run()

    assert [params.get("cursor") for params in account.api.listings()] == ["c2", "c3"]
    assert (second["created_events"], second["more"]) == (2, False)
    assert len(account.events()) == 4
    assert (account.cursor().last_ts, account.cursor().meta) == (NOW, {})


def test_a_page_without_entries_ends_its_window_whatever_paging_value_it_carries(
    account,
):
    account.inputs.update(initial_lookback_days=1)
    account.api.cursor_on_last = True

    (result,) = account.run()

    # The last page carried a paging value; the page it led to was empty.
    assert [params.get("cursor") for params in account.api.listings()] == [None, "c500"]
    assert (result["pages"], result["created_events"], result["more"]) == (2, 2, False)
    assert account.cursor().meta == {}


def test_a_paging_value_cloudflare_no_longer_accepts_restarts_its_window(account):
    account.inputs.update(initial_lookback_days=1, max_pages=1, page_size=1)
    account.run()
    account.api.requests.clear()
    account.inputs["max_pages"] = 10

    def answer(request):
        if request.url.params.get("cursor") == "c1":
            account.api.answer = lambda request: None
            return httpx.Response(400, text=MARKER)

    account.api.answer = answer

    (result,) = account.run()

    assert [params.get("cursor") for params in account.api.listings()][:2] == [
        "c1",
        None,
    ]
    # The window's first page is read again and recognised; the rest is new.
    assert result["created_events"] == 1 and len(account.events()) == 2
    assert account.cursor().meta == {}


def test_no_transaction_is_open_while_cloudflare_answers(account, monkeypatch):
    open_during = open_while_answering(monkeypatch, Cloudflare, account.db)
    account.inputs.update(initial_lookback_days=1, max_pages=1, page_size=1)
    # The first run creates the cursor and leaves a window part-read.
    account.run()
    account.inputs["max_pages"] = 10

    def answer(request):
        if request.url.params.get("cursor") == "c1":
            account.api.answer = lambda request: None
            return httpx.Response(400, text=MARKER)

    # The next resumes it, and starts it again when its paging value is refused.
    account.api.answer = answer
    account.run()
    # A later run reads a new window.
    account.clock.now = NOW + timedelta(minutes=15)
    account.run()

    assert len(open_during) == 5 and not any(open_during)


@pytest.mark.parametrize(
    "status,message",
    [
        (
            403,
            "Cloudflare API HTTP 403; check the credentials, their permissions "
            "and the endpoint",
        ),
        (429, "Cloudflare rate limit reached; the next run continues from the cursor"),
    ],
)
def test_a_refused_request_is_reported_and_keeps_the_cursor(account, status, message):
    account.api.answer = lambda request: httpx.Response(
        status, json={"success": False, "errors": [{"message": MARKER}]}
    )

    (result,) = account.run()

    assert result == {"stream": "audit", "error": message, **RESULT}
    assert MARKER not in str(result)
    assert len(account.api.requests) == 1
    assert account.cursor().last_ts == NOW - timedelta(days=2)


def test_a_page_that_fails_keeps_the_windows_and_pages_before_it(account):
    def answer(request):
        if request.url.params["before"] == "2026-11-19T12:00:01Z":
            return httpx.Response(503, text=MARKER)

    account.api.answer = answer

    (result,) = account.run()

    assert result["error"].startswith("Cloudflare API HTTP 503;")
    # The first window was committed with its cursor before the failure.
    assert [e.normalized_payload["event"] for e in account.events()] == [EARLY]
    assert account.cursor().last_ts == datetime(2026, 11, 18, 12)


@pytest.mark.parametrize(
    "page,message",
    [
        (
            {"success": True, "result": [{}], "result_info": {"cursor": "c1"}},
            "Cloudflare repeated a page; cursor retained",
        ),
        (
            {"success": True, "result": [{}] * 1001, "result_info": {}},
            "Cloudflare returned more events than a page holds",
        ),
        (
            {"success": True, "result": None, "result_info": {}},
            "Cloudflare returned an invalid page of events",
        ),
        (
            {"success": True, "result": [{}], "result_info": {"cursor": ["c9"]}},
            "Cloudflare returned an invalid paging value",
        ),
        (
            {"success": False, "result": [], "errors": [{"message": MARKER}]},
            "Cloudflare refused the request; check the API token's permissions",
        ),
        (
            {"result": [], "result_info": {}},
            "Cloudflare refused the request; check the API token's permissions",
        ),
    ],
    ids=["repeat", "oversized", "not-a-list", "bad-cursor", "unsuccessful", "unsaid"],
)
def test_a_page_that_breaks_the_api_contract_keeps_the_cursor(account, page, message):
    def answer(request):
        # The first page is sound; the one after it is not.
        if request.url.params.get("cursor") == "c1":
            return httpx.Response(200, json=page)
        return httpx.Response(
            200,
            json={"success": True, "result": [EARLY], "result_info": {"cursor": "c1"}},
        )

    account.api.answer = answer

    (result,) = account.run()

    assert result == {"stream": "audit", "error": message, **RESULT}
    assert MARKER not in str(result)
    cursor = account.cursor()
    assert cursor.last_ts == NOW - timedelta(days=2)
    assert cursor.meta == {"until": "2026-11-18T12:00:00", "token": "c1"}
    assert len(account.events()) == 1


def test_what_an_entry_says_about_itself_is_read_with_care(account):
    deep = {"a": 1}
    for _ in range(80):
        deep = {"n": deep}
    failed = entry("01a1f2b4-0000-7000-8000-0000000000a1", "2026-11-19T01:00:00Z")
    failed["action"] = {
        "description": "Delete Zone",
        "result": "failure",
        "time": "2026-11-19T01:00:00Z",
        "type": "delete",
    }
    system = entry(
        "01a1f2b4-0000-7000-8000-0000000000a2",
        "2026-11-19T02:00:00Z",
        actor={"type": "system"},
        resource={"product": "ssl", "scope": "zones"},
    )
    del system["zone"]
    odd = entry(
        "01a1f2b4-0000-7000-8000-0000000000a3",
        "2026-11-19T03:00:00Z",
        description="  " + "x" * 300,
        kind="re/view",
        actor={"id": "0123", "email": "   "},
        resource={"product": "../../etc", "scope": "zones"},
        extra=deep,
    )
    odd["action"]["result"] = "unknown"
    # An entry without an identifier is told apart by its content.
    anonymous = entry(None, "2026-11-19T04:00:00Z", description=None)
    account.api.entries = [failed, system, odd, anonymous, "not an entry", 7]
    account.inputs["initial_lookback_days"] = 1

    (result,) = account.run()

    assert (result["created_events"], result["invalid_records"]) == (4, 2)
    deleted, by_system, strange, unnamed = account.events()
    assert (deleted.action, deleted.outcome, deleted.severity) == (
        "dns_records.delete",
        "failure",
        6,
    )
    assert (by_system.actor, by_system.action, by_system.summary) == (
        None,
        "ssl.view",
        "Cloudflare: List DNS Records",
    )
    # Upstream text is used only when well formed, and only so much of it.
    assert (strange.actor, strange.action, strange.outcome) == (
        "0123",
        "unknown.unknown",
        "info",
    )
    assert strange.summary == f"Cloudflare: {'x' * 200} in example.org by 0123"
    assert strange.normalized_payload["event"]["extra"] != deep
    assert unnamed.summary == "Cloudflare: action in example.org by bob@example.com"
    # An entry without an identifier is told apart by its content: the same
    # content is recognised, and other content is another entry.
    account.api.entries = [anonymous, {**anonymous, "zone": {"name": "other.example"}}]
    account.clock.now = NOW + timedelta(minutes=1)
    account.inputs["overlap_minutes"] = 1440
    (again,) = account.run()
    assert again["created_events"] == 1


def test_an_entry_without_a_readable_time_is_dated_when_collected(account):
    undated = entry("01a1f2b4-0000-7000-8000-0000000000b1", "2026-11-19T05:00:00Z")
    account.api.entries = [undated]
    account.inputs["initial_lookback_days"] = 1

    def answer(request):
        page = {**undated, "action": {**undated["action"], "time": "yesterday"}}
        return httpx.Response(
            200, json={"success": True, "result": [page], "result_info": {}}
        )

    account.api.answer = answer
    account.run()

    (stored,) = account.events()
    assert stored.timestamp == NOW


def test_an_api_tokens_name_never_names_the_actor(account):
    by_token = entry(
        "01a1f2b4-0000-7000-8000-0000000000c1",
        "2026-11-19T06:00:00Z",
        actor={
            "context": "api_token",
            "id": "1234abcd",
            "token_id": "5678efab",
            "token_name": "Terraform CI",
            "type": "user",
        },
    )
    account.api.entries = [by_token]
    account.inputs["initial_lookback_days"] = 1

    account.run()

    (stored,) = account.events()
    assert stored.actor == "1234abcd"
    assert "Terraform CI" not in json.dumps(stored.normalized_payload)
    assert "Terraform CI" not in stored.summary


DNS_PATH = "/zones/5bmqpurDcxb9dbiggeF4ix9qjD7cjwas/dns_records"
SCRIPTS = {"product": "workers", "scope": "accounts", "type": "scripts"}


@pytest.mark.parametrize(
    "resource,uri",
    [
        ({"product": "tokens", "scope": "accounts"}, DNS_PATH),
        ({"product": "", "scope": "user", "type": "tokens"}, DNS_PATH),
        ({"product": "cfd_tunnel", "scope": "accounts", "type": "token"}, DNS_PATH),
        ({**SCRIPTS, "type": "scripts.secrets"}, DNS_PATH),
        ({"product": "access", "scope": "accounts", "type": "service_tokens"}, None),
        # A resource named otherwise is known by the path its secret was set at.
        (SCRIPTS, "/accounts/" + ACCOUNT + "/workers/scripts/site/secrets"),
        ({"product": "users", "scope": "user"}, "/client/v4/user/api_key?x=1"),
    ],
    ids=[
        "account-token",
        "user-token",
        "tunnel-token",
        "worker-secret",
        "service",
        "secret-path",
        "key-path",
    ],
)
def test_the_request_and_response_of_a_secret_are_withheld(account, resource, uri):
    secret = "issued-secret-value-ABCDEFGHIJKLMNOPQRSTU"
    issued = entry(
        "01a1f2b4-0000-7000-8000-0000000000d1",
        "2026-11-19T06:30:00Z",
        description="Create Token",
        kind="create",
        resource={
            **resource,
            "id": "mvbc2wyFi4x5xvpDvaroawkq3xkiqx7m",
            "request": {"name": "deploy", "text": secret},
            "response": {"id": "t1", "value": secret},
        },
        raw={**DNS_CREATED["raw"], "uri": uri},
    )
    account.api.entries = [issued]
    account.inputs["initial_lookback_days"] = 1

    account.run()

    (stored,) = account.events()
    kept = stored.normalized_payload["event"]["resource"]
    assert (kept["request"], kept["response"]) == ("REDACTED", "REDACTED")
    # The rest of the resource tells what was done.
    assert kept["id"] == "mvbc2wyFi4x5xvpDvaroawkq3xkiqx7m"
    assert stored.summary.startswith("Cloudflare: Create Token")
    artifact = account.db.query(Artifact).filter_by(event_id=stored.id).one()
    assert artifact.sha256 != hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in json.dumps(stored.normalized_payload)


def test_a_query_that_mentions_a_secret_withholds_nothing(account):
    # Only the path names what was changed; a query is the caller's own words.
    created = {
        **DNS_CREATED,
        "raw": {**DNS_CREATED["raw"], "uri": DNS_PATH + "?name=token"},
    }
    account.api.entries = [created]
    account.inputs["initial_lookback_days"] = 1

    account.run()

    (stored,) = account.events()
    kept = stored.normalized_payload["event"]["resource"]
    assert kept["request"]["type"] == "TXT" and kept["response"]["type"] == "TXT"


def test_two_accounts_keep_their_own_cursors_and_events(account):
    account.api.entries.append(
        entry(
            "01a1f2b4-0000-7000-8000-0000000000e1",
            "2026-11-19T08:00:00Z",
            account={"id": OTHER_ACCOUNT, "name": "Other"},
        )
    )

    results = account.run(
        account.connection("db:one"),
        account.connection("db:other", OTHER_ACCOUNT),
    )

    created = {r["connection_id"]: r["created_events"] for r in results}
    assert created == {"db:one": 3, "db:other": 1}
    paths = {request.url.path for request in account.api.requests}
    assert paths == {LISTING, f"/client/v4/accounts/{OTHER_ACCOUNT}/logs/audit"}
    assert account.cursor("db:one").last_ts == NOW
    assert account.cursor("db:other", OTHER_ACCOUNT).last_ts == NOW
    assert account.cursor("db:one", OTHER_ACCOUNT) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("account", ""),
        ("account", ACCOUNT.upper()),
        ("account", ACCOUNT[:-1]),
        ("account", ACCOUNT[:-2] + "/x"),
        ("account", "../" + ACCOUNT[3:]),
        ("token", ""),
        ("token", "two words"),
        ("token", "line\nbreak"),
        ("token", "x" * 513),
    ],
)
def test_an_account_or_token_that_cannot_be_used_sends_nothing(account, field, value):
    setattr(account.state, field, value)

    (result,) = account.run()

    assert result["error"] == (
        "Set the Cloudflare account ID and an API token that can read the "
        "account's audit logs"
    )
    assert account.clients == [] and account.api.requests == []


def test_settings_that_cannot_run_send_nothing(account, monkeypatch):
    account.inputs["streams"] = [{"stream": "dns"}]
    assert (
        account.run()[0]["error"] == "Invalid Cloudflare configuration; check streams"
    )
    account.inputs["streams"] = []
    assert account.run()[0] == {
        "skipped": True,
        "reason": "Add the Cloudflare audit stream",
        **RESULT,
    }
    monkeypatch.setattr(settings, "demo_mode", True)
    assert account.run()[0]["reason"] == "Cloudflare disabled (or demo mode)"
    assert account.clients == [] and account.api.requests == []


def test_an_unexpected_failure_is_described_generically(account):
    def answer(request):
        raise ValueError("resolved " + MARKER)

    account.api.answer = answer

    (result,) = account.run()

    assert result == {
        "stream": "audit",
        "error": "Collection failed; check the input, credentials, upstream "
        "service and storage configuration",
        **RESULT,
    }


def test_the_tasks_time_limit_ends_the_whole_run(account):
    def answer(request):
        raise SoftTimeLimitExceeded()

    account.api.answer = answer

    with pytest.raises(SoftTimeLimitExceeded):
        account.run(account.connection("db:one"), account.connection("db:two"))

    assert len(account.api.requests) == 1


def test_wherever_the_time_limit_falls_no_entry_is_lost_or_stored_twice(account):
    # The deadline is placed at each statement and request of a run in turn.
    # Wherever it falls the run stops there, with nothing reported as failed,
    # and the next run stores what is left: every entry once.
    account.inputs.update(page_size=1)

    def prepare():
        account.db.rollback()
        for model in (Artifact, Event, IngestionCursor):
            account.db.query(model).delete()
        account.db.commit()
        account.api.requests.clear()

    def after_limit():
        account.db.rollback()
        (result,) = account.run()
        assert "error" not in result
        stored = account.events()
        assert len({event.external_id for event in stored}) == len(stored) == 3

    with checked_statements(account.db.get_bind().engine):
        checks = stops_wherever_the_deadline_falls(account.run, prepare, after_limit)
    assert checks > 10


@pytest.mark.parametrize(
    "config,message",
    [
        ("audit", "Cloudflare settings must be an object"),
        ({"streams": "audit"}, "Cloudflare collects one stream: audit"),
        (
            {"streams": [{"stream": "audit"}, {"stream": "audit"}]},
            "Cloudflare collects one stream: audit",
        ),
        ({"streams": [{"stream": "logs"}]}, "Cloudflare collects one stream: audit"),
        (
            {"streams": [{"stream": "audit", "label": "x" * 129}]},
            "Cloudflare label must be at most 128 characters",
        ),
        (
            {"initial_lookback_days": 368},
            "Cloudflare initial_lookback_days must be an integer between 0 and 367",
        ),
        (
            {"max_pages": 0},
            "Cloudflare max_pages must be an integer between 1 and 1000",
        ),
        (
            {"page_size": 1001},
            "Cloudflare page_size must be an integer between 1 and 1000",
        ),
        (
            {"page_size": "500"},
            "Cloudflare page_size must be an integer between 1 and 1000",
        ),
    ],
)
def test_settings_outside_their_bounds_are_refused(config, message):
    with pytest.raises(cloudflare.CloudflareError, match="^" + message + "$"):
        cloudflare.validate_config(config)


def test_settings_within_their_bounds_are_accepted():
    config = {
        "streams": [{"stream": "audit", "label": "Production"}],
        "initial_lookback_days": 367,
        "window_hours": 168,
        "overlap_minutes": 0,
        "max_pages": 1000,
        "page_size": 1000,
    }
    assert cloudflare.validate_config(config) is config
    assert cloudflare.validate_config({}) == {}
