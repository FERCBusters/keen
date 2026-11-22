"""1Password collection runs from the scheduled entry point.

A connection supplies the Events API server and its bearer token; each run
reads the selected streams a page at a time, stores every event as evidence
and saves the position 1Password returned with each page. An in-memory
transport stands in for 1Password.
"""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx2 as httpx
import pytest
from app.core.config import settings
from app.db.models import Event, IngestionCursor
from app.ingest import op_events as onepassword
from app.ingest.connections import Connection, connection_scope, namespace
from app.mapping.collector import collector_id, event_collector
from celery.exceptions import SoftTimeLimitExceeded
from myf.core.datetime_utils import utc_now_naive

from tests.transactions import open_while_answering

BASE = "https://events.1password.com"
TOKEN = "synthetic.onepassword.token"
MARKER = "upstream-body-marker"


def sign_in(n, category="success", **fields):
    return {
        "uuid": f"SIGNIN{n:04d}",
        "session_uuid": f"SESSION{n:04d}",
        "timestamp": f"2026-10-01T10:{n:02d}:00Z",
        "category": category,
        "type": "credentials_ok",
        "country": "AU",
        "target_user": {
            "uuid": "USER1",
            "name": "Jane Smith",
            "email": "jane@example.org",
        },
        "client": {"app_name": "1Password Browser", "ip_address": "203.0.113.9"},
        **fields,
    }


AUDIT = {
    "uuid": "AUDIT0001",
    "timestamp": "2026-10-01T09:00:00Z",
    "actor_uuid": "USER2",
    "actor_details": {"uuid": "USER2", "name": "Sam Admin", "email": "sam@example.org"},
    "action": "suspend",
    "object_type": "user",
    "object_uuid": "USER9",
}
USAGE = {
    "uuid": "USAGE0001",
    "timestamp": "2026-10-01T09:30:00Z",
    "action": "reveal",
    "vault_uuid": "VAULT1",
    "item_uuid": "ITEM1",
    "user": {"uuid": "USER1", "name": "Jane Smith", "email": "jane@example.org"},
}


class OnePassword:
    """Streams of events served from a position: ``p-<offset>-<page size>``."""

    def __init__(self):
        self.requests = []
        self.events = {
            "auditevents": [AUDIT],
            "itemusages": [USAGE],
            "signinattempts": [sign_in(1), sign_in(2, "credentials_failed")],
        }
        # A test's own answer to a request, or None for the stream's events.
        self.answer = lambda request, body: None

    def __call__(self, request):
        self.requests.append(request)
        body = json.loads(request.content)
        answer = self.answer(request, body)
        if answer is not None:
            return answer
        rows = self.events[request.url.path.rsplit("/", 1)[-1]]
        if "cursor" in body:
            _, offset, limit = body["cursor"].split("-")
            offset, limit = int(offset), int(limit)
        else:
            offset, limit = 0, body["limit"]
        chunk = rows[offset : offset + limit]
        position = offset + len(chunk)
        return httpx.Response(
            200,
            json={
                "cursor": f"p-{position}-{limit}",
                "has_more": position < len(rows),
                "items": chunk,
            },
        )

    def bodies(self, stream="signinattempts"):
        return [
            json.loads(r.content) for r in self.requests if r.url.path.endswith(stream)
        ]


@pytest.fixture
def vault(contract_db, monkeypatch, tmp_path):
    for name, value in {
        "demo_mode": False,
        "op_events_enabled": True,
        "event_data_masking": "false",
        "artifact_storage_backend": "local",
        "artifact_local_dir": str(tmp_path),
    }.items():
        monkeypatch.setattr(settings, name, value)
    api = OnePassword()
    clients = []

    def client(**kwargs):
        clients.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(api), **kwargs)

    monkeypatch.setattr(onepassword, "ingestion_client", client)
    inputs = {"streams": [{"stream": "signinattempts", "label": "Company vault"}]}
    state = SimpleNamespace(base_url=BASE, token=TOKEN)

    def connection(key="db:op_events"):
        return Connection(
            key,
            "op_events",
            "1Password",
            {"base_url": state.base_url},
            {"token": state.token},
            inputs,
        )

    def run(*keys):
        chosen = [connection(key) for key in keys or ("db:op_events",)]
        with patch("app.ingest.connections.connections", return_value=chosen):
            return onepassword.ingest_op_events_all(contract_db)

    def cursor(stream="signinattempts", key="db:op_events"):
        import hashlib

        digest = hashlib.sha256(f"{BASE}:{stream}".encode()).hexdigest()
        with connection_scope(connection(key)):
            name = namespace("op_events:" + digest)
        return contract_db.query(IngestionCursor).filter_by(name=name).one_or_none()

    def events():
        return (
            contract_db.query(Event)
            .filter_by(source="op_events")
            .order_by(Event.timestamp, Event.action)
            .all()
        )

    return SimpleNamespace(
        db=contract_db,
        api=api,
        clients=clients,
        inputs=inputs,
        state=state,
        run=run,
        cursor=cursor,
        events=events,
    )


IDENTITY = {"connection_id": "db:op_events", "connection_name": "1Password"}


def test_a_run_stores_a_streams_events_and_later_runs_continue_from_its_position(
    vault,
):
    (result,) = vault.run()

    assert result == {
        "stream": "signinattempts",
        "pages": 1,
        "created_events": 2,
        "invalid_records": 0,
        "more": False,
        **IDENTITY,
    }
    (options,) = vault.clients
    assert options["headers"] == {
        "Authorization": "Bearer " + TOKEN,
        "Accept": "application/json",
    }
    assert options["follow_redirects"] is False and options["verify"] is True
    (request,) = vault.api.requests
    assert (request.method, str(request.url)) == (
        "POST",
        BASE + "/api/v2/signinattempts",
    )
    assert TOKEN not in str(request.url) and TOKEN.encode() not in request.content
    # The first request starts the stream, a week back by default.
    (first,) = vault.api.bodies()
    assert set(first) == {"limit", "start_time"} and first["limit"] == 100
    since = datetime.fromisoformat(first["start_time"].removesuffix("Z"))
    assert (
        timedelta(days=6, hours=23)
        < utc_now_naive() - since
        < timedelta(days=7, minutes=1)
    )

    succeeded, failed = vault.events()
    assert (
        succeeded.action,
        succeeded.outcome,
        succeeded.severity,
        succeeded.actor,
        succeeded.system,
        succeeded.timestamp,
    ) == (
        "signin.success",
        "success",
        3,
        "jane@example.org",
        "Company vault",
        datetime(2026, 10, 1, 10, 1),
    )
    assert succeeded.summary == (
        "1Password sign-in attempt: success by jane@example.org"
    )
    assert (failed.action, failed.outcome, failed.severity) == (
        "signin.credentials_failed",
        "failure",
        6,
    )
    assert succeeded.raw_pointer["op_events"] == {
        "section": "streams",
        "stream": "signinattempts",
        "uuid": "SIGNIN0001",
    }
    assert succeeded.normalized_payload == {
        "stream": "signinattempts",
        "event": sign_in(1),
    }
    # A rule can name the stream these events came from.
    assert event_collector(
        {"source": "op_events", "raw_pointer": succeeded.raw_pointer}
    ) == collector_id("op_events", "streams", "signinattempts")
    cursor = vault.cursor()
    assert cursor.meta == {"cursor": "p-2-100", "limit": 100}
    assert cursor.last_ts == datetime(2026, 10, 1, 10, 2)

    vault.api.requests.clear()
    vault.api.events["signinattempts"].append(sign_in(3, "mfa_failed"))
    (later,) = vault.run()

    # A later run sends only the saved position and stores only what is new.
    assert vault.api.bodies() == [{"cursor": "p-2-100"}]
    assert later["created_events"] == 1
    assert [e.action for e in vault.events()][-1] == "signin.mfa_failed"
    assert vault.cursor().meta["cursor"] == "p-3-100"


def test_each_stream_describes_its_own_events(vault):
    vault.inputs["streams"] = [
        {"stream": "auditevents"},
        {"stream": "itemusages"},
        {"stream": "signinattempts"},
    ]
    vault.api.events["signinattempts"] = [sign_in(1, "firewall_reported_success")]

    results = vault.run()

    assert [(r["stream"], r["created_events"]) for r in results] == [
        ("auditevents", 1),
        ("itemusages", 1),
        ("signinattempts", 1),
    ]
    audit, usage, attempt = vault.events()
    assert (audit.action, audit.actor, audit.outcome, audit.system) == (
        "audit.user.suspend",
        "sam@example.org",
        "info",
        "1Password",
    )
    assert audit.summary == "1Password audit: suspend user by sam@example.org"
    assert (usage.action, usage.actor, usage.summary) == (
        "item.reveal",
        "jane@example.org",
        "1Password item usage: reveal by jane@example.org",
    )
    # A sign-in the firewall only reported went ahead.
    assert (attempt.action, attempt.outcome) == (
        "signin.firewall_reported_success",
        "success",
    )
    assert [r.url.path.rsplit("/", 1)[-1] for r in vault.api.requests] == [
        "auditevents",
        "itemusages",
        "signinattempts",
    ]


def test_each_page_is_kept_with_its_position_before_the_next_is_read(vault):
    vault.inputs.update(page_size=1, max_pages=1)
    vault.api.events["signinattempts"].append(sign_in(3))

    (first,) = vault.run()

    # The page limit ends the run with more to read, and the position is kept.
    assert (first["pages"], first["created_events"], first["more"]) == (1, 1, True)
    assert vault.cursor().meta == {"cursor": "p-1-1", "limit": 1}
    assert len(vault.events()) == 1

    vault.inputs["max_pages"] = 5
    (second,) = vault.run()

    assert (second["pages"], second["created_events"], second["more"]) == (2, 2, False)
    assert vault.api.bodies()[1:] == [{"cursor": "p-1-1"}, {"cursor": "p-2-1"}]
    assert len(vault.events()) == 3


def test_a_page_that_fails_keeps_the_pages_before_it_and_the_other_streams(vault):
    vault.inputs.update(page_size=1)
    vault.inputs["streams"].append({"stream": "auditevents"})

    def answer(request, body):
        if body.get("cursor") == "p-1-1":
            return httpx.Response(500, text=MARKER)

    vault.api.answer = answer

    failed, audit = vault.run()

    assert failed == {
        "stream": "signinattempts",
        "error": "1Password API HTTP 500; check the credentials, their permissions and the endpoint",
        **IDENTITY,
    }
    assert MARKER not in str(failed)
    # The first page and its position were committed before the failure.
    assert vault.cursor().meta == {"cursor": "p-1-1", "limit": 1}
    assert [e.action for e in vault.events()] == [
        "audit.user.suspend",
        "signin.success",
    ]
    assert audit["created_events"] == 1

    vault.api.answer = lambda request, body: None
    resumed, _ = vault.run()
    assert resumed["created_events"] == 1


def test_a_position_1password_no_longer_accepts_restarts_from_the_newest_event(vault):
    vault.run()
    vault.api.requests.clear()
    vault.api.events["signinattempts"].append(sign_in(3))

    def answer(request, body):
        if body.get("cursor") == "p-2-100":
            return httpx.Response(400, text=MARKER)

    vault.api.answer = answer

    (result,) = vault.run()

    refused, restart = vault.api.bodies()
    assert refused == {"cursor": "p-2-100"}
    # The restart reaches back five minutes before the newest stored event.
    assert restart == {"limit": 100, "start_time": "2026-10-01T09:57:00Z"}
    # Events read again are recognised; only the new one is stored.
    assert result["created_events"] == 1 and len(vault.events()) == 3


def test_no_transaction_is_open_while_1password_answers(vault, monkeypatch):
    open_during = open_while_answering(monkeypatch, OnePassword, vault.db)
    # The first run starts the stream; the next continues from its position,
    # which 1Password refuses, so it starts again from the newest event.
    vault.run()
    vault.api.answer = lambda request, body: (
        httpx.Response(400, text=MARKER) if "cursor" in body else None
    )
    vault.run()

    assert len(open_during) == 3 and not any(open_during)


@pytest.mark.parametrize("status", [400, 401])
def test_a_refused_first_request_is_reported_and_tried_once(vault, status):
    vault.api.answer = lambda request, body: httpx.Response(status, text=MARKER)

    (result,) = vault.run()

    assert result["error"].startswith(f"1Password API HTTP {status};")
    assert len(vault.api.requests) == 1
    assert vault.cursor() is None and vault.events() == []


def test_a_refused_restart_is_reported(vault):
    vault.run()
    vault.api.requests.clear()
    vault.api.answer = lambda request, body: httpx.Response(400)

    (result,) = vault.run()

    # The saved position, then one restart: no more.
    assert [set(body) for body in vault.api.bodies()] == [
        {"cursor"},
        {"limit", "start_time"},
    ]
    assert result["error"].startswith("1Password API HTTP 400;")
    assert vault.cursor().meta == {"cursor": "p-2-100", "limit": 100}


def test_a_changed_page_size_starts_the_stream_again(vault):
    vault.run()
    vault.api.requests.clear()
    vault.inputs["page_size"] = 50

    (result,) = vault.run()

    (body,) = vault.api.bodies()
    assert body == {"limit": 50, "start_time": "2026-10-01T09:57:00Z"}
    assert result["created_events"] == 0
    assert vault.cursor().meta == {"cursor": "p-2-50", "limit": 50}


def test_rate_limiting_keeps_the_position_for_the_next_run(vault):
    vault.api.answer = lambda request, body: httpx.Response(429, text=MARKER)

    (result,) = vault.run()

    assert result["error"] == (
        "1Password rate limit reached; the next run continues from the cursor"
    )


@pytest.mark.parametrize(
    "page,message",
    [
        (
            {"cursor": "p-0-100", "has_more": True, "items": []},
            "1Password repeated a page; cursor retained",
        ),
        (
            {"cursor": "next", "has_more": False, "items": [{}] * 101},
            "1Password returned more events than a page holds",
        ),
        (
            {"cursor": "next", "has_more": False, "items": {"a": {}}},
            "1Password returned an invalid page of events",
        ),
        (
            {"has_more": False, "items": []},
            "1Password returned invalid paging data",
        ),
        (
            {"cursor": "next", "has_more": "no", "items": []},
            "1Password returned invalid paging data",
        ),
        (
            {"cursor": "a b", "has_more": False, "items": []},
            "1Password returned an invalid paging value",
        ),
    ],
    ids=["repeat", "oversized", "not-a-list", "no-cursor", "no-flag", "bad-cursor"],
)
def test_a_page_that_breaks_the_paging_contract_stores_nothing(vault, page, message):
    def answer(request, body):
        # The first page is sound; the one after it is not.
        if "cursor" in body:
            return httpx.Response(200, json=page)
        return httpx.Response(
            200, json={"cursor": "p-0-100", "has_more": True, "items": [sign_in(1)]}
        )

    vault.api.answer = answer

    (result,) = vault.run()

    assert result == {"stream": "signinattempts", "error": message, **IDENTITY}
    # The sound page is kept with its position.
    assert vault.cursor().meta == {"cursor": "p-0-100", "limit": 100}
    assert len(vault.events()) == 1


def test_positions_that_cycle_are_refused(vault):
    positions = iter(["one", "two", "one"])

    vault.api.answer = lambda request, body: httpx.Response(
        200, json={"cursor": next(positions), "has_more": True, "items": []}
    )

    (result,) = vault.run()

    assert result["error"] == "1Password repeated a page; cursor retained"
    assert len(vault.api.requests) == 3


def test_records_a_page_cannot_use_are_counted_and_the_rest_stored(vault):
    deep = {"a": 1}
    for _ in range(80):
        deep = {"n": deep}
    vault.api.events["signinattempts"] = [
        "not an object",
        # No identifier and no readable time: told apart by content, dated now.
        {"category": "success", "timestamp": "soon"},
        # Codes that are not simple names, and text PostgreSQL cannot hold.
        {
            "uuid": "ODD\x001",
            "timestamp": "2026-10-01T10:00:00Z",
            "category": "<b>fail</b> " * 20,
            "target_user": "nobody",
            "details": deep,
        },
        None,
    ]

    (result,) = vault.run()

    assert (result["created_events"], result["invalid_records"]) == (2, 2)
    odd, undated = vault.events()
    assert (odd.action, odd.outcome, odd.actor) == ("signin.unknown", "failure", None)
    assert odd.summary == "1Password sign-in attempt: unknown"
    assert odd.raw_pointer["op_events"]["uuid"] == "ODD\ufffd1"
    assert "[nested too deeply]" in json.dumps(odd.normalized_payload)
    assert undated.raw_pointer["op_events"]["uuid"] is None
    assert utc_now_naive() - undated.timestamp < timedelta(minutes=1)

    # Read again from the start, neither is stored twice.
    vault.inputs["page_size"] = 99
    (again,) = vault.run()
    assert again["created_events"] == 0 and len(vault.events()) == 2


def test_a_long_identifier_is_clipped_in_the_pointer_and_whole_in_the_identity(vault):
    vault.api.events["signinattempts"] = [
        sign_in(1, uuid="A" * 500),
        sign_in(1, uuid="A" * 499 + "B"),
    ]

    (result,) = vault.run()

    assert result["created_events"] == 2
    assert {e.raw_pointer["op_events"]["uuid"] for e in vault.events()} == {"A" * 128}


def test_an_event_dated_ahead_never_carries_the_cursor_past_now(vault):
    vault.api.events["signinattempts"] = [sign_in(1, timestamp="2999-01-01T00:00:00Z")]

    vault.run()

    (event,) = vault.events()
    assert event.timestamp == datetime(2999, 1, 1)
    assert vault.cursor().last_ts <= utc_now_naive()


def test_an_empty_stream_keeps_its_position_without_a_time(vault):
    vault.api.events["signinattempts"] = []

    (result,) = vault.run()

    assert (result["pages"], result["created_events"]) == (1, 0)
    cursor = vault.cursor()
    assert cursor.last_ts is None and cursor.meta == {"cursor": "p-0-100", "limit": 100}


def test_two_connections_keep_their_own_positions_and_events(vault):
    first, second = vault.run("db:one", "db:two")

    assert (first["created_events"], second["created_events"]) == (2, 2)
    assert {first["connection_id"], second["connection_id"]} == {"db:one", "db:two"}
    assert vault.cursor(key="db:one").name != vault.cursor(key="db:two").name
    assert {e.connection_id for e in vault.events()} == {"db:one", "db:two"}
    # Each connection began its own stream instead of sharing a position.
    assert [set(body) for body in vault.api.bodies()] == [{"limit", "start_time"}] * 2


@pytest.mark.parametrize(
    "base_url",
    [
        "https://events.1password.example",
        "https://events.1password.com.attacker.example",
        "https://events.1password.com/api/../x",
        "https://user@events.1password.com",
        "http://events.1password.com",
        "",
    ],
)
def test_the_token_is_sent_to_1passwords_own_servers_alone(vault, base_url):
    vault.state.base_url = base_url

    (result,) = vault.run()

    assert result["error"].startswith(
        "Set the 1Password Events API server to one of https://events.1password.com,"
    )
    assert vault.clients == [] and vault.api.requests == []


@pytest.mark.parametrize("server", onepassword.SERVERS)
def test_every_documented_server_is_accepted(vault, server):
    vault.state.base_url = server + "/"

    vault.run()

    assert str(vault.api.requests[0].url) == server + "/api/v2/signinattempts"


@pytest.mark.parametrize("token", ["", "   ", "two\nlines", "café", "a b"])
def test_a_token_that_is_no_header_value_sends_nothing(vault, token):
    vault.state.token = token

    (result,) = vault.run()

    assert result["error"] == "Set the 1Password Events API bearer token"
    assert vault.clients == []


def test_settings_that_cannot_run_send_nothing(vault, monkeypatch):
    vault.inputs["streams"] = [{"stream": "vaults"}]
    assert vault.run()[0]["error"] == "Invalid 1Password configuration; check streams"
    vault.inputs["streams"] = []
    assert vault.run()[0] == {
        "skipped": True,
        "reason": "Add a 1Password stream to collect",
        **IDENTITY,
    }
    monkeypatch.setattr(settings, "demo_mode", True)
    assert vault.run()[0]["reason"] == "1Password disabled (or demo mode)"
    assert vault.clients == [] and vault.api.requests == []


def test_an_unexpected_failure_is_described_generically(vault):
    def answer(request, body):
        raise ValueError("resolved " + MARKER)

    vault.api.answer = answer

    (result,) = vault.run()

    assert result["error"] == (
        "Collection failed; check the input, credentials, upstream service "
        "and storage configuration"
    )


def test_the_tasks_time_limit_ends_the_whole_run(vault):
    def answer(request, body):
        raise SoftTimeLimitExceeded()

    vault.api.answer = answer
    vault.inputs["streams"].append({"stream": "auditevents"})

    with pytest.raises(SoftTimeLimitExceeded):
        vault.run()

    assert len(vault.api.requests) == 1


@pytest.mark.parametrize(
    "config,message",
    [
        ([], "1Password settings must be an object"),
        (
            {"streams": {"stream": "auditevents"}},
            "1Password streams must list each stream at most once",
        ),
        (
            {"streams": [{"stream": "auditevents"}] * 4},
            "1Password streams must list each stream at most once",
        ),
        (
            {"streams": ["auditevents"]},
            "Choose a 1Password stream: auditevents, itemusages or signinattempts",
        ),
        (
            {"streams": [{"stream": ["auditevents"]}]},
            "Choose a 1Password stream: auditevents, itemusages or signinattempts",
        ),
        (
            {"streams": [{"stream": "auditevents"}, {"stream": "auditevents"}]},
            "Duplicate 1Password stream",
        ),
        (
            {"streams": [{"stream": "auditevents", "label": "x" * 129}]},
            "1Password label must be at most 128 characters",
        ),
        (
            {"streams": [{"stream": "auditevents", "label": 5}]},
            "1Password label must be at most 128 characters",
        ),
        (
            {"page_size": 1001},
            "1Password page_size must be an integer between 1 and 1000",
        ),
        (
            {"max_pages": True},
            "1Password max_pages must be an integer between 1 and 1000",
        ),
        (
            {"initial_lookback_days": -1},
            "1Password initial_lookback_days must be an integer between 0 and 365",
        ),
    ],
)
def test_settings_outside_their_bounds_are_refused(config, message):
    with pytest.raises(onepassword.OnePasswordError, match="^" + message + "$"):
        onepassword.validate_config(config)


def test_settings_within_their_bounds_are_accepted():
    config = {
        "streams": [{"stream": name, "label": "Vault"} for name in onepassword.STREAMS],
        "initial_lookback_days": 0,
        "page_size": 1000,
        "max_pages": 1,
    }
    assert onepassword.validate_config(config) is config
    assert onepassword.validate_config({}) == {}
