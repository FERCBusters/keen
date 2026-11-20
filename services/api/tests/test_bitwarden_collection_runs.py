"""Bitwarden collection runs from the scheduled entry point.

A connection supplies the API and identity endpoints and an organisation API
key. Each run signs in, reads the event log in time windows, oldest first, and
stores every page before the next. An in-memory transport stands in for both
Bitwarden endpoints.
"""

import hashlib
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx2 as httpx
import pytest
from app.core.config import settings
from app.db.models import Event, IngestionCursor
from app.ingest import bitwarden
from app.ingest.connections import Connection, connection_scope, namespace
from app.mapping.collector import collector_id, event_collector
from celery.exceptions import SoftTimeLimitExceeded

from tests.transactions import open_while_answering

API = "https://93.184.216.34/api"
IDENTITY_URL = "https://93.184.216.35/identity"
CLIENT_ID = "organization.3f2504e0-4f89-11d3-9a0c-0305e82c3301"
SECRET = "synthetic-bitwarden-secret"
ACCESS = "synthetic.access.token"
MARKER = "upstream-body-marker"
NOW = datetime(2026, 10, 10, 12, 0, 0)
RESULT = {"connection_id": "db:bitwarden", "connection_name": "Bitwarden"}


def event(code, when, **fields):
    return {
        "object": "event",
        "type": code,
        "itemId": None,
        "actingUserId": "8a1c0f6e-0000-4000-8000-000000000001",
        "date": when,
        "device": 9,
        "ipAddress": "203.0.113.9",
        **fields,
    }


def instant(value):
    return datetime.fromisoformat(value.removesuffix("Z"))


class Bitwarden:
    """An identity endpoint and an event log paged ``size`` events at a time."""

    def __init__(self):
        self.requests = []
        self.events = [
            event(1000, "2026-10-09T08:00:00.1234567Z"),
            event(1005, "2026-10-10T09:00:00Z"),
        ]
        self.size = 50
        self.token = {
            "access_token": ACCESS,
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        # A test's own answer to a request, or None for the usual one.
        self.answer = lambda request: None

    def __call__(self, request):
        self.requests.append(request)
        answer = self.answer(request)
        if answer is not None:
            return answer
        if request.url.path == "/identity/connect/token":
            return httpx.Response(200, json=self.token)
        assert request.url.path == "/api/public/events", request.url.path
        params = request.url.params
        start, end = instant(params["start"]), instant(params["end"])
        # Both ends are inclusive, and the newest event comes first.
        rows = sorted(
            (e for e in self.events if start <= instant(e["date"]) <= end),
            key=lambda e: e["date"],
            reverse=True,
        )
        offset = int(params.get("continuationToken", "t0")[1:])
        following = offset + self.size
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": rows[offset:following],
                "continuationToken": f"t{following}" if following < len(rows) else None,
            },
        )

    def listings(self):
        return [
            dict(r.url.params) for r in self.requests if r.url.path.endswith("/events")
        ]

    def sign_ins(self):
        return [r for r in self.requests if r.url.path.endswith("/connect/token")]


@pytest.fixture
def vault(contract_db, monkeypatch, tmp_path):
    for name, value in {
        "demo_mode": False,
        "bitwarden_enabled": True,
        "event_data_masking": "false",
        "artifact_storage_backend": "local",
        "artifact_local_dir": str(tmp_path),
    }.items():
        monkeypatch.setattr(settings, name, value)
    api = Bitwarden()
    clients = []

    def client(**kwargs):
        clients.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(api), **kwargs)

    monkeypatch.setattr(bitwarden, "ingestion_client", client)
    clock = SimpleNamespace(now=NOW)
    monkeypatch.setattr(bitwarden, "utc_now_naive", lambda: clock.now)
    inputs = {
        "streams": [{"stream": "events", "label": "Company vault"}],
        "initial_lookback_days": 2,
    }
    state = SimpleNamespace(
        base_url=API, identity_url=IDENTITY_URL, client_id=CLIENT_ID, secret=SECRET
    )

    def connection(key="db:bitwarden"):
        return Connection(
            key,
            "bitwarden",
            "Bitwarden",
            {
                "base_url": state.base_url,
                "identity_url": state.identity_url,
                "client_id": state.client_id,
            },
            {"client_secret": state.secret},
            inputs,
        )

    def run(*keys):
        chosen = [connection(key) for key in keys or ("db:bitwarden",)]
        with patch("app.ingest.connections.connections", return_value=chosen):
            return bitwarden.ingest_bitwarden_all(contract_db)

    def cursor(key="db:bitwarden"):
        digest = hashlib.sha256(API.encode()).hexdigest()
        with connection_scope(connection(key)):
            name = namespace("bitwarden:" + digest)
        return contract_db.query(IngestionCursor).filter_by(name=name).one_or_none()

    def events():
        return (
            contract_db.query(Event)
            .filter_by(source="bitwarden")
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
        run=run,
        cursor=cursor,
        events=events,
    )


def test_a_run_signs_in_and_reads_the_log_in_windows_oldest_first(vault):
    (result,) = vault.run()

    assert result == {
        "stream": "events",
        "pages": 2,
        "created_events": 2,
        "invalid_records": 0,
        "more": False,
        "cursor": "2026-10-10T12:00:00Z",
        **RESULT,
    }
    # No credential is a default header of the client.
    (options,) = vault.clients
    assert options["headers"] == {"Accept": "application/json"}
    assert options["follow_redirects"] is False and options["verify"] is True
    # The secret goes to the identity endpoint alone, in the request body.
    (sign_in,) = vault.api.sign_ins()
    assert (sign_in.method, str(sign_in.url)) == (
        "POST",
        IDENTITY_URL + "/connect/token",
    )
    assert "authorization" not in sign_in.headers
    assert parse_qs(sign_in.content.decode()) == {
        "grant_type": ["client_credentials"],
        "scope": ["api.organization"],
        "client_id": [CLIENT_ID],
        "client_secret": [SECRET],
    }
    # The access token goes to the API endpoint alone, and the secret never.
    listings = [r for r in vault.api.requests if r.url.path.endswith("/events")]
    for request in listings:
        assert request.method == "GET" and request.url.host == "93.184.216.34"
        assert request.headers["authorization"] == "Bearer " + ACCESS
        assert SECRET not in str(request.url) and request.content == b""
    # Two day-long windows from the lookback to now, each reaching back an hour.
    assert vault.api.listings() == [
        {"start": "2026-10-08T11:00:00Z", "end": "2026-10-09T12:00:00Z"},
        {"start": "2026-10-09T11:00:00Z", "end": "2026-10-10T12:00:00Z"},
    ]

    logged_in, failed = vault.events()
    assert (
        logged_in.action,
        logged_in.outcome,
        logged_in.severity,
        logged_in.actor,
        logged_in.system,
        logged_in.timestamp,
    ) == (
        "user.logged_in",
        "info",
        3,
        "8a1c0f6e-0000-4000-8000-000000000001",
        "Company vault",
        datetime(2026, 10, 9, 8, 0, 0, 123456),
    )
    assert logged_in.summary == (
        "Bitwarden: Logged in by 8a1c0f6e-0000-4000-8000-000000000001"
    )
    assert (failed.action, failed.outcome, failed.severity) == (
        "user.failed_login",
        "failure",
        6,
    )
    assert logged_in.raw_pointer["bitwarden"] == {
        "section": "streams",
        "stream": "events",
        "type": 1000,
    }
    assert logged_in.normalized_payload == {
        "stream": "events",
        "event": event(1000, "2026-10-09T08:00:00.1234567Z"),
    }
    assert event_collector(
        {"source": "bitwarden", "raw_pointer": logged_in.raw_pointer}
    ) == collector_id("bitwarden", "streams", "events")
    cursor = vault.cursor()
    assert (cursor.last_ts, cursor.meta) == (NOW, {})


def test_a_later_run_reads_from_the_cursor_and_stores_nothing_twice(vault):
    vault.run()
    vault.api.requests.clear()
    vault.clock.now = NOW + timedelta(minutes=30)
    # Reported late: dated before the cursor, inside the hour each window
    # reaches back.
    vault.api.events.append(event(1107, "2026-10-10T11:40:00Z"))

    (result,) = vault.run()

    assert vault.api.listings() == [
        {"start": "2026-10-10T11:00:00Z", "end": "2026-10-10T12:30:00Z"}
    ]
    assert (result["pages"], result["created_events"]) == (1, 1)
    assert [e.action for e in vault.events()] == [
        "user.logged_in",
        "user.failed_login",
        "item.viewed",
    ]
    assert vault.cursor().last_ts == NOW + timedelta(minutes=30)


def test_a_run_with_nothing_new_to_read_asks_for_nothing(vault):
    vault.run()
    vault.api.requests.clear()

    (result,) = vault.run()

    # It signs in, finds the cursor at the present and reads no window.
    assert len(vault.api.sign_ins()) == 1 and vault.api.listings() == []
    assert (result["pages"], result["more"]) == (0, False)


def test_a_first_run_without_lookback_fixes_its_start_and_collects_afterwards(vault):
    vault.inputs["initial_lookback_days"] = 0

    (first,) = vault.run()

    assert (first["pages"], first["created_events"]) == (0, 0)
    assert vault.cursor().last_ts == NOW and vault.api.listings() == []

    vault.clock.now = NOW + timedelta(hours=1)
    vault.api.events.append(event(1500, "2026-10-10T12:20:00Z"))
    (second,) = vault.run()

    assert second["created_events"] == 1
    assert [e.action for e in vault.events()] == ["member.invited"]


def test_a_window_is_paged_and_its_cursor_moves_only_when_it_is_complete(vault):
    vault.inputs.update(initial_lookback_days=1, max_pages=2)
    vault.api.size = 1
    vault.api.events = [
        event(1000, f"2026-10-10T0{hour}:00:00Z") for hour in range(1, 6)
    ]

    (first,) = vault.run()

    # The page limit ends the run inside the window: its pages are stored, the
    # cursor stays at the window's start and the paging value is kept.
    assert (first["pages"], first["created_events"], first["more"]) == (2, 2, True)
    cursor = vault.cursor()
    assert cursor.last_ts == NOW - timedelta(days=1)
    assert cursor.meta == {"until": "2026-10-10T12:00:00", "token": "t2"}
    assert len(vault.events()) == 2

    vault.api.requests.clear()
    vault.clock.now = NOW + timedelta(minutes=10)
    vault.inputs["max_pages"] = 10
    (second,) = vault.run()

    # The next run finishes the same window, then reads up to the present.
    window = {"start": "2026-10-09T11:00:00Z", "end": "2026-10-10T12:00:00Z"}
    assert vault.api.listings() == [
        {**window, "continuationToken": "t2"},
        {**window, "continuationToken": "t3"},
        {**window, "continuationToken": "t4"},
        {"start": "2026-10-10T11:00:00Z", "end": "2026-10-10T12:10:00Z"},
    ]
    assert (second["created_events"], second["more"]) == (3, False)
    assert len(vault.events()) == 5
    cursor = vault.cursor()
    assert (cursor.last_ts, cursor.meta) == (NOW + timedelta(minutes=10), {})


def test_no_transaction_is_open_while_bitwarden_answers(vault, monkeypatch):
    open_during = open_while_answering(monkeypatch, Bitwarden, vault.db)
    vault.inputs.update(initial_lookback_days=1, max_pages=2)
    vault.api.size = 1
    vault.api.events = [event(1000, f"2026-10-10T0{hour}:00:00Z") for hour in (1, 2, 3)]
    # The first run creates the cursor and leaves a window part-read; the next
    # finishes it and reads up to the present.
    vault.run()
    vault.clock.now = NOW + timedelta(minutes=10)
    vault.inputs["max_pages"] = 10
    vault.run()

    # Each run signs in, then lists its pages.
    assert len(open_during) == 6 and not any(open_during)


def test_a_paging_value_bitwarden_no_longer_accepts_restarts_its_window(vault):
    vault.inputs.update(initial_lookback_days=1, max_pages=1)
    vault.api.size = 1
    vault.api.events = [event(1000, f"2026-10-10T0{hour}:00:00Z") for hour in (1, 2)]
    vault.run()
    vault.api.requests.clear()
    vault.inputs["max_pages"] = 10

    def answer(request):
        if request.url.params.get("continuationToken") == "t1":
            vault.api.answer = lambda request: None
            return httpx.Response(400, text=MARKER)

    vault.api.answer = answer

    (result,) = vault.run()

    window = {"start": "2026-10-09T11:00:00Z", "end": "2026-10-10T12:00:00Z"}
    assert vault.api.listings()[:2] == [{**window, "continuationToken": "t1"}, window]
    # The window's first page is read again and recognised; the rest is new.
    assert result["created_events"] == 1 and len(vault.events()) == 2
    assert vault.cursor().meta == {}


def test_a_refused_window_is_reported_and_keeps_the_cursor(vault):
    def answer(request):
        if request.url.path.endswith("/events"):
            return httpx.Response(400, text=MARKER)

    vault.api.answer = answer

    (result,) = vault.run()

    assert result == {
        "stream": "events",
        "error": "Bitwarden API HTTP 400; check the credentials, their permissions and the endpoint",
        **RESULT,
    }
    assert len(vault.api.listings()) == 1
    assert vault.cursor().last_ts == NOW - timedelta(days=2)


def test_a_page_that_fails_keeps_the_windows_and_pages_before_it(vault):
    def answer(request):
        if request.url.params.get("end") == "2026-10-10T12:00:00Z":
            return httpx.Response(503, text=MARKER)

    vault.api.answer = answer

    (result,) = vault.run()

    assert result["error"].startswith("Bitwarden API HTTP 503;")
    assert MARKER not in str(result)
    # The first window was committed with its cursor before the failure.
    assert [e.action for e in vault.events()] == ["user.logged_in"]
    assert vault.cursor().last_ts == datetime(2026, 10, 9, 12)


@pytest.mark.parametrize(
    "page,message",
    [
        (
            {"data": [], "continuationToken": "t1"},
            "Bitwarden repeated a page; cursor retained",
        ),
        (
            {"data": [{}] * 1001, "continuationToken": None},
            "Bitwarden returned more events than a page holds",
        ),
        (
            {"data": None, "continuationToken": None},
            "Bitwarden returned an invalid page of events",
        ),
        (
            {"data": [], "continuationToken": {"next": 1}},
            "Bitwarden returned an invalid paging value",
        ),
    ],
    ids=["repeat", "oversized", "not-a-list", "bad-token"],
)
def test_a_page_that_breaks_the_paging_contract_keeps_the_cursor(vault, page, message):
    def answer(request):
        # The first page is sound; the one after it is not.
        if request.url.params.get("continuationToken") == "t1":
            return httpx.Response(200, json=page)
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                json={
                    "data": [event(1000, "2026-10-09T08:00:00Z")],
                    "continuationToken": "t1",
                },
            )

    vault.api.answer = answer

    (result,) = vault.run()

    assert result == {"stream": "events", "error": message, **RESULT}
    cursor = vault.cursor()
    assert cursor.last_ts == NOW - timedelta(days=2)
    assert cursor.meta == {"until": "2026-10-09T12:00:00", "token": "t1"}
    assert len(vault.events()) == 1


def test_paging_values_that_cycle_are_refused(vault):
    tokens = iter(["one", "two", "one"])

    def answer(request):
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200, json={"data": [], "continuationToken": next(tokens)}
            )

    vault.api.answer = answer

    (result,) = vault.run()

    assert result["error"] == "Bitwarden repeated a page; cursor retained"
    assert len(vault.api.listings()) == 3


@pytest.mark.parametrize(
    "meta",
    [
        {"until": "2026-10-11T12:00:00", "token": "t9"},  # later than now
        {"until": "2026-10-08T12:00:00", "token": "t9"},  # not after the cursor
        {"until": "soon", "token": "t9"},
        {"until": "2026-10-09T12:00:00", "token": 9},
        {"until": "2026-10-09T12:00:00"},
    ],
)
def test_a_saved_place_that_does_not_fit_the_cursor_is_set_aside(vault, meta):
    vault.run()
    cursor = vault.cursor()
    cursor.last_ts, cursor.meta = NOW - timedelta(days=2), meta
    vault.db.commit()
    vault.api.requests.clear()

    (result,) = vault.run()

    assert all("continuationToken" not in params for params in vault.api.listings())
    assert result["more"] is False and vault.cursor().meta == {}


def test_event_types_are_named_from_bitwardens_codes(vault):
    deep = {"a": 1}
    for _ in range(80):
        deep = {"n": deep}
    rows = [
        event(1006, "2026-10-10T01:00:00Z"),
        event(1601, "2026-10-10T02:00:00Z", actingUserId=None),
        # A code newer than the table, and types that are no code at all.
        event(4242, "2026-10-10T03:00:00Z"),
        event("1000", "2026-10-10T04:00:00Z"),
        event([1005], "2026-10-10T05:00:00Z", itemId="a\x00b", extra=deep),
        event(True, "2026-10-10T06:00:00Z"),
        "not an object",
    ]

    def answer(request):
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"data": rows})

    vault.api.answer = answer
    vault.inputs["initial_lookback_days"] = 1

    (result,) = vault.run()

    assert (result["created_events"], result["invalid_records"]) == (6, 1)
    assert [(e.action, e.outcome, e.summary) for e in vault.events()] == [
        (
            "user.failed_two_step_login",
            "failure",
            "Bitwarden: Login attempt failed with incorrect two-step login by "
            "8a1c0f6e-0000-4000-8000-000000000001",
        ),
        (
            "organization.vault_purged",
            "info",
            "Bitwarden: Purged organisation vault",
        ),
        (
            "event.4242",
            "info",
            "Bitwarden: Event type 4242 by 8a1c0f6e-0000-4000-8000-000000000001",
        ),
        *[
            (
                "event.unknown",
                "info",
                "Bitwarden: Event of an unreadable type by "
                "8a1c0f6e-0000-4000-8000-000000000001",
            )
        ]
        * 3,
    ]
    odd = vault.events()[4]
    assert odd.raw_pointer["bitwarden"]["type"] is None
    assert odd.normalized_payload["event"]["itemId"] == "a�b"
    assert "[nested too deeply]" in str(odd.normalized_payload)


def test_every_documented_event_type_has_a_distinct_action():
    actions = [action for action, _ in bitwarden.EVENT_TYPES.values()]
    assert len(set(actions)) == len(actions) == 68
    assert all(bitwarden.scan_pattern(r"[a-z]+\.[a-z_]+").fullmatch(a) for a in actions)
    assert bitwarden.FAILURES <= set(bitwarden.EVENT_TYPES)


def test_an_event_without_a_readable_date_is_dated_when_collected(vault):
    def answer(request):
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"data": [event(1000, "last Tuesday")]})

    vault.api.answer = answer
    vault.inputs["initial_lookback_days"] = 1

    (result,) = vault.run()

    (stored,) = vault.events()
    assert result["created_events"] == 1 and stored.timestamp == NOW


def test_two_connections_keep_their_own_cursors_and_events(vault):
    first, second = vault.run("db:one", "db:two")

    assert (first["created_events"], second["created_events"]) == (2, 2)
    assert vault.cursor("db:one").name != vault.cursor("db:two").name
    assert {e.connection_id for e in vault.events()} == {"db:one", "db:two"}
    # Each connection signed in for itself.
    assert len(vault.api.sign_ins()) == 2


@pytest.mark.parametrize(
    "status,message",
    [
        (
            400,
            "Bitwarden sign-in API HTTP 400; check the credentials, their permissions and the endpoint",
        ),
        (
            401,
            "Bitwarden sign-in API HTTP 401; check the credentials, their permissions and the endpoint",
        ),
        (
            429,
            "Bitwarden sign-in rate limit reached; the next run continues from the cursor",
        ),
    ],
)
def test_a_refused_sign_in_reads_nothing_and_reports_only_its_status(
    vault, status, message
):
    vault.api.answer = lambda request: httpx.Response(status, text=MARKER + SECRET)

    (result,) = vault.run()

    assert result == {"stream": "events", "error": message, **RESULT}
    assert vault.api.listings() == [] and vault.cursor() is None


@pytest.mark.parametrize(
    "token",
    [
        {"token_type": "Bearer"},
        {"access_token": "", "token_type": "Bearer"},
        {"access_token": ["a"], "token_type": "Bearer"},
        {"access_token": "two\r\nX-Injected: 1", "token_type": "Bearer"},
        {"access_token": "x" * 16385, "token_type": "Bearer"},
        {"access_token": ACCESS, "token_type": "mac"},
        {"access_token": ACCESS, "token_type": None},
        {"access_token": ACCESS},
    ],
)
def test_a_sign_in_answer_without_a_usable_bearer_token_is_refused(vault, token):
    vault.api.token = token

    (result,) = vault.run()

    assert result["error"] == "Bitwarden sign-in returned no usable access token"
    assert vault.api.listings() == []


def test_a_bearer_token_type_is_accepted_in_any_case(vault):
    vault.api.token = {"access_token": ACCESS, "token_type": "bearer"}

    (result,) = vault.run()

    assert result["created_events"] == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("base_url", "http://93.184.216.34/api"),
        ("base_url", "https://user:pw@93.184.216.34/api"),
        ("base_url", "https://127.0.0.1/api"),
        ("base_url", ""),
        ("identity_url", "http://93.184.216.35/identity"),
        ("identity_url", "https://93.184.216.35/identity?next=1"),
        ("identity_url", "https://169.254.169.254/identity"),
        ("identity_url", ""),
    ],
)
def test_an_endpoint_collection_may_not_reach_sends_nothing(vault, field, value):
    setattr(vault.state, field, value)

    (result,) = vault.run()

    assert result["error"] == (
        "Configure safe HTTPS Bitwarden API and identity URLs "
        "without credentials, query or fragment"
    )
    assert vault.clients == [] and vault.api.requests == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("client_id", ""),
        ("client_id", "user.3f2504e0-4f89-11d3-9a0c-0305e82c3301"),
        ("client_id", CLIENT_ID + "&scope=api"),
        ("secret", ""),
        ("secret", "two words"),
        ("secret", "x" * 513),
    ],
)
def test_an_api_key_that_is_not_an_organisations_sends_nothing(vault, field, value):
    setattr(vault.state, field, value)

    (result,) = vault.run()

    assert result["error"] == (
        "Enter the Bitwarden organisation's client ID, which begins "
        "organization., and the client secret issued with it"
    )
    assert vault.clients == [] and vault.api.requests == []


def test_settings_that_cannot_run_send_nothing(vault, monkeypatch):
    vault.inputs["streams"] = [{"stream": "members"}]
    assert vault.run()[0]["error"] == "Invalid Bitwarden configuration; check streams"
    vault.inputs["streams"] = []
    assert vault.run()[0] == {
        "skipped": True,
        "reason": "Add the Bitwarden events stream",
        **RESULT,
    }
    monkeypatch.setattr(settings, "demo_mode", True)
    assert vault.run()[0]["reason"] == "Bitwarden disabled (or demo mode)"
    assert vault.clients == [] and vault.api.requests == []


def test_an_unexpected_failure_is_described_generically(vault):
    def answer(request):
        raise ValueError("resolved " + MARKER)

    vault.api.answer = answer

    (result,) = vault.run()

    assert result == {
        "stream": "events",
        "error": "Collection failed; check the input, credentials, upstream "
        "service and storage configuration",
        **RESULT,
    }


def test_the_tasks_time_limit_ends_the_whole_run(vault):
    def answer(request):
        raise SoftTimeLimitExceeded()

    vault.api.answer = answer

    with pytest.raises(SoftTimeLimitExceeded):
        vault.run("db:one", "db:two")

    assert len(vault.api.requests) == 1


@pytest.mark.parametrize(
    "config,message",
    [
        ("events", "Bitwarden settings must be an object"),
        ({"streams": "events"}, "Bitwarden collects one stream: events"),
        (
            {"streams": [{"stream": "events"}, {"stream": "events"}]},
            "Bitwarden collects one stream: events",
        ),
        ({"streams": ["events"]}, "Bitwarden collects one stream: events"),
        (
            {"streams": [{"stream": ["events"]}]},
            "Bitwarden collects one stream: events",
        ),
        (
            {"streams": [{"stream": "events", "label": "x" * 129}]},
            "Bitwarden label must be at most 128 characters",
        ),
        (
            {"streams": [{"stream": "events", "label": None}]},
            "Bitwarden label must be at most 128 characters",
        ),
        (
            {"initial_lookback_days": 368},
            "Bitwarden initial_lookback_days must be an integer between 0 and 367",
        ),
        (
            {"window_hours": 0},
            "Bitwarden window_hours must be an integer between 1 and 168",
        ),
        (
            {"overlap_minutes": 1.5},
            "Bitwarden overlap_minutes must be an integer between 0 and 1440",
        ),
        (
            {"max_pages": False},
            "Bitwarden max_pages must be an integer between 1 and 1000",
        ),
    ],
)
def test_settings_outside_their_bounds_are_refused(config, message):
    with pytest.raises(bitwarden.BitwardenError, match="^" + message + "$"):
        bitwarden.validate_config(config)


def test_settings_within_their_bounds_are_accepted():
    config = {
        "streams": [{"stream": "events", "label": "Vault"}],
        "initial_lookback_days": 367,
        "window_hours": 168,
        "overlap_minutes": 0,
        "max_pages": 1000,
    }
    assert bitwarden.validate_config(config) is config
    assert bitwarden.validate_config({}) == {}
