"""Mosyle collection runs from the scheduled entry point.

A connection supplies an administrator's sign-in and the Mosyle API's access
token. Each run signs in and reads administrator action logs in time windows,
oldest first. An in-memory transport stands in for the Mosyle API.
"""

import hashlib
import json
from calendar import timegm
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx2 as httpx
import pytest
from app.core.config import settings
from app.db.models import Event, IngestionCursor
from app.ingest import mosyle, registry
from app.ingest.connections import Connection, connection_scope, namespace
from app.mapping.collector import collector_id, event_collector
from celery.exceptions import SoftTimeLimitExceeded

from tests.transactions import open_while_answering

HOST = "businessapi.mosyle.com"
LOGIN = "https://businessapi.mosyle.com/v1/login"
LOGS = "https://businessapi.mosyle.com/v1/adminlogs"
TOKEN = "synthetic-api-access-token"
EMAIL = "auditor@example.org"
PASSWORD = "synthetic pass-phrase é"
MARKER = "upstream-body-marker"
NOW = datetime(2026, 10, 10, 12, 0, 0)
RESULT = {"connection_id": "db:mosyle", "connection_name": "Mosyle"}
HTTP = "; check the credentials, their permissions and the endpoint"


def epoch(*when):
    return str(timegm(datetime(*when).timetuple()))


def log(name, *when, **fields):
    """An administrator action log as the Mosyle API lists it."""
    return {
        "action": name,
        "details": {"Type": name, "E-mail": "subject@example.org"},
        "username": "Jane Smith",
        "action_date": datetime(*when).strftime("%Y-%m-%d %H:%M:%S"),
        "ip": "203.0.113.9",
        **fields,
    }


def ok(response):
    return httpx.Response(200, json={"status": "OK", "response": response})


def page(logs, number=1, size=100, rows=None):
    return {
        "logs": logs,
        "rows": str(len(logs)) if rows is None else rows,
        "page_size": size,
        "page": number,
    }


class Mosyle:
    """The Mosyle API: a sign-in and a dated log of administrator actions."""

    def __init__(self):
        self.requests = []
        self.logs = [
            log("Save Device Group", 2026, 10, 9, 8),
            log("Remove Device", 2026, 10, 10, 9, username="John Doe"),
        ]
        self.size = 100
        # A test's own answer to a request, or None for the usual one.
        self.answer = lambda request: None

    def __call__(self, request):
        self.requests.append(request)
        answer = self.answer(request)
        if answer is not None:
            return answer
        assert request.url.host == HOST and request.method == "POST", request.url
        if str(request.url) == LOGIN:
            return httpx.Response(
                200,
                headers={
                    "Authorization": "Bearer synthetic.bearer.jwt",
                    "Set-Cookie": "PHPSESSID=synthetic; domain=.mosyle.com; path=/",
                },
                json={"UserID": "1234", "email": EMAIL},
            )
        assert str(request.url) == LOGS, request.url
        body = json.loads(request.content)
        dates = body["filter_options"]
        start, end = int(dates["start_date"]), int(dates["end_date"])
        # Both ends are inclusive, and the newest log comes first.
        found = sorted(
            (e for e in self.logs if start <= self.time(e) <= end),
            key=self.time,
            reverse=True,
        )
        number = body["page"]
        return ok(
            page(
                found[(number - 1) * self.size : number * self.size],
                number,
                self.size,
                # The count of every log between the dates, as digits.
                rows=str(len(found)),
            )
        )

    @staticmethod
    def time(entry):
        when = datetime.strptime(entry["action_date"], "%Y-%m-%d %H:%M:%S")
        return timegm(when.timetuple())

    def sign_ins(self):
        return [r for r in self.requests if str(r.url) == LOGIN]

    def asked(self):
        return [json.loads(r.content) for r in self.requests if str(r.url) == LOGS]

    def logs_answer(self, respond):
        """Answer every request for logs with ``respond(page number)``."""

        def answer(request):
            if str(request.url) != LOGS:
                return None
            return respond(json.loads(request.content)["page"])

        self.answer = answer


@pytest.fixture
def fleet(contract_db, monkeypatch, tmp_path):
    for name, value in {
        "demo_mode": False,
        "mosyle_enabled": True,
        "event_data_masking": "false",
        "artifact_storage_backend": "local",
        "artifact_local_dir": str(tmp_path),
    }.items():
        monkeypatch.setattr(settings, name, value)
    api = Mosyle()
    clients = []

    def client(**kwargs):
        clients.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(api), **kwargs)

    monkeypatch.setattr(mosyle, "ingestion_client", client)
    clock = SimpleNamespace(now=NOW)
    monkeypatch.setattr(mosyle, "utc_now_naive", lambda: clock.now)
    inputs = {
        "streams": [{"stream": "adminlogs", "label": "Fleet"}],
        "initial_lookback_days": 2,
    }
    state = SimpleNamespace(email=EMAIL, password=PASSWORD, access_token=TOKEN)

    def connection(key="db:mosyle"):
        return Connection(
            key,
            "mosyle",
            "Mosyle",
            {"email": state.email},
            {"password": state.password, "access_token": state.access_token},
            inputs,
        )

    def run(*keys):
        chosen = [connection(key) for key in keys or ("db:mosyle",)]
        with patch("app.ingest.connections.connections", return_value=chosen):
            return mosyle.ingest_mosyle_all(contract_db)

    def cursor(key="db:mosyle"):
        digest = hashlib.sha256(f"{mosyle.API}:adminlogs".encode()).hexdigest()
        with connection_scope(connection(key)):
            name = namespace("mosyle:" + digest)
        return contract_db.query(IngestionCursor).filter_by(name=name).one_or_none()

    def events():
        return (
            contract_db.query(Event)
            .filter_by(source="mosyle")
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


def test_a_run_signs_in_and_reads_the_log_in_windows_oldest_first(fleet):
    (result,) = fleet.run()

    assert result == {
        "stream": "adminlogs",
        "pages": 2,
        "created_events": 2,
        "invalid_records": 0,
        "more": False,
        "cursor": "2026-10-10T12:00:00Z",
        **RESULT,
    }
    # No credential is a default header of the client; redirects are off.
    (options,) = fleet.clients
    assert options["headers"] == {"Accept": "application/json"}
    assert options["follow_redirects"] is False and options["verify"] is True
    # The password goes to the sign-in alone, in the request body.
    sign_in, *requests = fleet.api.requests
    assert (sign_in.method, str(sign_in.url)) == ("POST", LOGIN)
    assert sign_in.headers["accesstoken"] == TOKEN
    assert "authorization" not in sign_in.headers
    assert json.loads(sign_in.content) == {"email": EMAIL, "password": PASSWORD}
    # Every other request carries the access token and the bearer token, and
    # neither the password nor the cookie the sign-in set.
    assert len(requests) == 2
    for request in requests:
        assert str(request.url) == LOGS
        assert request.headers["accesstoken"] == TOKEN
        assert request.headers["authorization"] == "Bearer synthetic.bearer.jwt"
        assert "cookie" not in request.headers
        assert PASSWORD.encode() not in request.content
    # Two day-long windows from the lookback to now, each reaching back an hour.
    assert fleet.api.asked() == [
        {
            "page": 1,
            "filter_options": {
                "start_date": epoch(2026, 10, 8, 11),
                "end_date": epoch(2026, 10, 9, 12),
            },
        },
        {
            "page": 1,
            "filter_options": {
                "start_date": epoch(2026, 10, 9, 11),
                "end_date": epoch(2026, 10, 10, 12),
            },
        },
    ]

    saved, removed = fleet.events()
    assert (
        saved.action,
        saved.outcome,
        saved.severity,
        saved.actor,
        saved.system,
        saved.timestamp,
        saved.summary,
    ) == (
        "admin.save_device_group",
        "info",
        3,
        "Jane Smith",
        "Fleet",
        datetime(2026, 10, 9, 8),
        "Mosyle admin: Save Device Group by Jane Smith",
    )
    assert (removed.action, removed.actor) == ("admin.remove_device", "John Doe")
    assert saved.raw_pointer["mosyle"] == {"section": "streams", "stream": "adminlogs"}
    assert saved.normalized_payload == {
        "stream": "adminlogs",
        "event": log("Save Device Group", 2026, 10, 9, 8),
    }
    assert event_collector(
        {"source": "mosyle", "raw_pointer": saved.raw_pointer}
    ) == collector_id("mosyle", "streams", "adminlogs")
    cursor = fleet.cursor()
    assert (cursor.last_ts, cursor.meta) == (NOW, {})


def test_a_log_is_described_by_the_fields_mosyle_gives_it(fleet):
    fleet.inputs.update(initial_lookback_days=1, streams=[{"stream": "adminlogs"}])
    fleet.api.logs = [
        log(
            "Create API Integration",
            2026,
            10,
            10,
            9,
            6,
            47,
            details={"Operating System": "iOS", "Profile Name": "keen"},
        ),
        log("User Login", 2026, 10, 10, 8, username=None),
    ]

    fleet.run()

    login, created = fleet.events()
    assert (
        created.action,
        created.actor,
        created.system,
        created.timestamp,
        created.summary,
    ) == (
        "admin.create_api_integration",
        "Jane Smith",
        # Without a label the system is the product.
        "Mosyle",
        # Mosyle writes a date and a time of day, read as UTC.
        datetime(2026, 10, 10, 9, 6, 47),
        "Mosyle admin: Create API Integration by Jane Smith",
    )
    assert created.normalized_payload["event"]["details"] == {
        "Operating System": "iOS",
        "Profile Name": "keen",
    }
    # The address in a sign-in's details is its subject; it names no actor.
    assert (login.action, login.actor, login.summary) == (
        "admin.user_login",
        None,
        "Mosyle admin: User Login",
    )
    assert login.normalized_payload["event"]["details"]["E-mail"] == (
        "subject@example.org"
    )


def test_a_later_run_reads_from_the_cursor_and_stores_nothing_twice(fleet):
    fleet.run()
    fleet.api.requests.clear()
    fleet.clock.now = NOW + timedelta(hours=2)
    fleet.api.logs.append(log("Wipe Device", 2026, 10, 10, 13))

    (result,) = fleet.run()

    # The window reaches back an hour, over a log the first run stored.
    assert fleet.api.asked() == [
        {
            "page": 1,
            "filter_options": {
                "start_date": epoch(2026, 10, 10, 11),
                "end_date": epoch(2026, 10, 10, 14),
            },
        }
    ]
    assert (result["pages"], result["created_events"]) == (1, 1)
    assert [e.action for e in fleet.events()] == [
        "admin.save_device_group",
        "admin.remove_device",
        "admin.wipe_device",
    ]
    assert fleet.cursor().last_ts == NOW + timedelta(hours=2)
    # Each run signs in again; no token is kept between runs.
    assert len(fleet.api.sign_ins()) == 1


def test_a_run_with_nothing_new_to_read_asks_for_nothing(fleet):
    fleet.inputs["initial_lookback_days"] = 0

    (result,) = fleet.run()

    assert (result["pages"], result["more"]) == (0, False)
    assert fleet.api.requests == [] and fleet.cursor().last_ts == NOW


def test_a_window_is_paged_and_its_cursor_moves_only_when_it_is_complete(fleet):
    fleet.inputs.update(initial_lookback_days=1, max_pages=2)
    fleet.api.size = 1
    fleet.api.logs = [log(f"Step {n}", 2026, 10, 10, n) for n in (1, 2, 3)]

    (first,) = fleet.run()

    # The page limit ended the run inside the window: the cursor keeps the
    # window's start, with the page that follows.
    assert (first["pages"], first["created_events"], first["more"]) == (2, 2, True)
    assert first["cursor"] == "2026-10-09T12:00:00Z"
    cursor = fleet.cursor()
    assert cursor.last_ts == NOW - timedelta(days=1)
    assert cursor.meta == {"until": "2026-10-10T12:00:00", "page": 3}

    fleet.api.requests.clear()
    fleet.clock.now = NOW + timedelta(minutes=10)
    fleet.inputs["max_pages"] = 3
    (second,) = fleet.run()

    # The window continues from its third page, with the dates it began with.
    # That page is full, which says nothing of a fourth, so the fourth is
    # asked for and ends the window; the next window follows it.
    assert [
        (b["page"], b["filter_options"]["end_date"]) for b in fleet.api.asked()
    ] == [
        (3, epoch(2026, 10, 10, 12)),
        (4, epoch(2026, 10, 10, 12)),
        (1, epoch(2026, 10, 10, 12, 10)),
    ]
    assert (second["created_events"], second["more"]) == (1, False)
    assert [e.action for e in fleet.events()] == [
        "admin.step_1",
        "admin.step_2",
        "admin.step_3",
    ]
    cursor = fleet.cursor()
    assert (cursor.last_ts, cursor.meta) == (NOW + timedelta(minutes=10), {})


def test_no_transaction_is_open_while_mosyle_answers(fleet, monkeypatch):
    open_during = open_while_answering(monkeypatch, Mosyle, fleet.db)
    fleet.inputs.update(initial_lookback_days=1, max_pages=2)
    fleet.api.size = 1
    fleet.api.logs = [log(f"Step {n}", 2026, 10, 10, n) for n in (1, 2, 3)]
    # The first run creates the cursor and leaves a window part-read; the next
    # finishes it and reads up to the present.
    fleet.run()
    fleet.clock.now = NOW + timedelta(minutes=10)
    fleet.inputs["max_pages"] = 3
    fleet.run()

    # Each run signs in, then lists its pages.
    assert len(open_during) == 7 and not any(open_during)


@pytest.mark.parametrize(
    "rows,size,asked",
    [
        # A page shorter than the page size, whose count reaches no further
        # page, is the last: one request reads the window.
        ("1", 100, [1]),
        (1, 100, [1]),
        # A count that reaches a further page, a full page, and a count or a
        # page size Keen cannot read all leave the next page to be asked for.
        ("250", 100, [1, 2]),
        ("1", 1, [1, 2]),
        ("many", 100, [1, 2]),
        ("1" * 13, 100, [1, 2]),
        (True, 100, [1, 2]),
        ({}, 100, [1, 2]),
        ("1", "100", [1, 2]),
        ("1", True, [1, 2]),
    ],
)
def test_a_window_ends_at_a_short_page_only_when_its_count_agrees(
    fleet, rows, size, asked
):
    fleet.inputs["initial_lookback_days"] = 1
    logs = [log("Save Device Group", 2026, 10, 10, 8)]
    fleet.api.logs_answer(
        lambda number: ok(page(logs if number == 1 else [], number, size, rows))
    )

    (result,) = fleet.run()

    assert [b["page"] for b in fleet.api.asked()] == asked
    assert (result["created_events"], result["more"]) == (1, False)
    assert fleet.cursor().last_ts == NOW


@pytest.mark.parametrize(
    "meta",
    [
        {"until": "2026-10-10T12:00:00"},
        {"until": "2026-10-10T12:00:00", "page": 1},
        {"until": "2026-10-10T12:00:00", "page": "3"},
        {"until": "2026-10-10T12:00:00", "page": True},
        {"until": "2026-10-10T12:00:00", "page": 1001},
        {"until": "not a time", "page": 3},
        # Before the cursor, after now, and longer than a window.
        {"until": "2026-10-08T12:00:00", "page": 3},
        {"until": "2026-10-10T13:00:00", "page": 3},
        {"until": "2026-10-10T11:30:00", "page": 3},
    ],
)
def test_a_saved_place_that_does_not_fit_the_cursor_is_set_aside(fleet, meta):
    fleet.inputs["initial_lookback_days"] = 1
    fleet.run()
    cursor = fleet.cursor()
    cursor.last_ts, cursor.meta = NOW - timedelta(days=1, hours=1), meta
    fleet.db.commit()
    fleet.api.requests.clear()

    fleet.run()

    assert [b["page"] for b in fleet.api.asked()] == [1, 1]
    assert fleet.cursor().meta == {}


def test_a_window_with_more_pages_than_it_holds_is_refused(fleet, monkeypatch):
    monkeypatch.setattr(mosyle, "MAX_WINDOW_PAGES", 2)
    fleet.inputs["initial_lookback_days"] = 1
    # Every page is full and claims a great many more.
    fleet.api.logs_answer(
        lambda number: ok(
            page([log(f"Step {number}", 2026, 10, 10, number)], number, 1, "9" * 12)
        )
    )

    (result,) = fleet.run()

    assert result == {
        "stream": "adminlogs",
        "error": "Mosyle lists more pages than a window holds; lower "
        "window_hours. Cursor retained",
        **RESULT,
    }
    # The pages read are kept, and the window is not counted as read.
    assert [b["page"] for b in fleet.api.asked()] == [1, 2]
    assert len(fleet.events()) == 2
    assert fleet.cursor().last_ts == NOW - timedelta(days=1)


@pytest.mark.parametrize(
    "body,message",
    [
        (
            {"status": "OK", "response": {"page": 1, "message": MARKER}},
            "Mosyle returned no listing of logs; cursor retained",
        ),
        (
            {"status": "OK", "response": {"logs": {"0": log("X", 2026, 10, 10)}}},
            "Mosyle returned no listing of logs; cursor retained",
        ),
        (
            {"status": "OK", "response": [log("X", 2026, 10, 10)]},
            "Mosyle returned no listing of logs; cursor retained",
        ),
        (
            {"status": "OK", "info": MARKER},
            "Mosyle returned no listing of logs; cursor retained",
        ),
        (
            {"status": "ERROR", "response": page([]), "info": MARKER},
            "Mosyle refused the request; check the API profile and its permissions",
        ),
        (
            {"response": page([])},
            "Mosyle refused the request; check the API profile and its permissions",
        ),
        (
            {"status": "OK", "response": page([{}] * 5001)},
            "Mosyle returned more events than a page holds",
        ),
    ],
)
def test_an_answer_keen_cannot_read_keeps_the_cursor(fleet, body, message):
    fleet.inputs["initial_lookback_days"] = 1
    fleet.api.logs_answer(lambda number: httpx.Response(200, json=body))

    (result,) = fleet.run()

    assert result == {"stream": "adminlogs", "error": message, **RESULT}
    assert fleet.events() == []
    assert fleet.cursor().last_ts == NOW - timedelta(days=1)


def test_entries_that_are_not_logs_are_left_out_and_counted(fleet):
    fleet.inputs["initial_lookback_days"] = 1
    logs = ["text", None, log("Save Device Group", 2026, 10, 10, 8), 7]
    fleet.api.logs_answer(lambda number: ok(page(logs, rows="4")))

    (result,) = fleet.run()

    assert (result["created_events"], result["invalid_records"]) == (1, 3)
    assert fleet.cursor().last_ts == NOW


def test_hostile_values_are_stored_without_stopping_the_run(fleet):
    fleet.inputs["initial_lookback_days"] = 1
    logs = [
        log(
            "<script>alert(1)</script>\x00" + "A" * 5000,
            2026,
            10,
            10,
            8,
            username="  Mallory\ud800  ",
            details="<img src=x onerror=alert(1)>",
        ),
        log(None, 2026, 10, 10, 9, username=["not", "text"]),
        log("éè !!", 2026, 10, 10, 10, action_date="soon", username=""),
        log("Wipe Device", 2026, 10, 10, 11, action_date=1791630000),
        log("Erase Device", 2026, 10, 10, 11, action_date="9999-99-99 99:99:99"),
    ]
    # As JSON escapes, which is how a lone surrogate and a NUL arrive.
    body = json.dumps({"status": "OK", "response": page(logs)})
    fleet.api.logs_answer(lambda number: httpx.Response(200, content=body.encode()))

    (result,) = fleet.run()

    assert (result["created_events"], result["invalid_records"]) == (5, 0)
    hostile, unnamed, erased, wordless, wiped = fleet.events()
    # An action name is built from the plain words in its first 64 characters.
    assert hostile.action == "admin.script_alert_1_script_" + "a" * 38
    assert hostile.actor == "Mallory\ufffd"
    assert hostile.summary.startswith(
        "Mosyle admin: <script>alert(1)</script>\ufffdAAA"
    )
    assert len(hostile.summary) == len("Mosyle admin: ") + 200 + len(
        " by Mallory\ufffd"
    )
    assert hostile.normalized_payload["event"]["details"] == (
        "<img src=x onerror=alert(1)>"
    )
    assert (unnamed.action, unnamed.actor, unnamed.summary, unnamed.timestamp) == (
        "admin.unknown",
        None,
        "Mosyle admin: action",
        datetime(2026, 10, 10, 9),
    )
    assert (wordless.action, wordless.summary) == (
        "admin.unknown",
        "Mosyle admin: \u00e9\u00e8 !!",
    )
    # A log without a time Keen can read is dated when it was collected:
    # words, a number, and a date no calendar holds.
    assert (erased.action, wiped.action) == ("admin.erase_device", "admin.wipe_device")
    assert [e.timestamp for e in (erased, wordless, wiped)] == [NOW] * 3


def test_two_connections_keep_their_own_cursors_events_and_sign_ins(fleet):
    results = fleet.run("db:one", "db:two")

    assert [r["created_events"] for r in results] == [2, 2]
    assert {r["connection_id"] for r in results} == {"db:one", "db:two"}
    assert len(fleet.events()) == 4
    assert {e.connection_id for e in fleet.events()} == {"db:one", "db:two"}
    assert fleet.cursor("db:one").name != fleet.cursor("db:two").name
    assert fleet.cursor() is None
    # Each connection's run signs in for itself and opens its own client.
    assert len(fleet.api.sign_ins()) == 2 and len(fleet.clients) == 2


@pytest.mark.parametrize(
    "status,message",
    [
        (401, "Mosyle sign-in API HTTP 401" + HTTP),
        (404, "Mosyle sign-in API HTTP 404" + HTTP),
        (
            429,
            "Mosyle sign-in rate limit reached; the next run continues from the cursor",
        ),
    ],
)
def test_a_refused_sign_in_reports_only_its_status(fleet, status, message):
    fleet.api.answer = lambda request: httpx.Response(
        status, json={"error": MARKER + PASSWORD}
    )

    (result,) = fleet.run()

    assert result == {"stream": "adminlogs", "error": message, **RESULT}
    # The password is sent once and no log is asked for.
    assert len(fleet.api.requests) == 1
    assert fleet.cursor().last_ts == NOW - timedelta(days=2)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer two words"},
        {"Authorization": "bearer lower.case.scheme"},
        {"Authorization": "Bearer " + "x" * 16385},
        {"Authorization": 'Bearer quoted"value'},
        [("Authorization", "Bearer one.jwt"), ("Authorization", "Bearer two.jwt")],
    ],
)
def test_a_sign_in_answer_without_a_usable_bearer_token_is_refused(fleet, headers):
    fleet.api.answer = lambda request: httpx.Response(
        200, headers=headers, json={"UserID": "1"}
    )

    (result,) = fleet.run()

    assert result["error"] == "Mosyle sign-in returned no usable bearer token"
    assert fleet.api.asked() == [] and len(fleet.api.requests) == 1


def test_a_sign_in_that_redirects_is_refused_without_being_followed(fleet):
    fleet.api.answer = lambda request: httpx.Response(
        307, headers={"Location": "https://collector.example.net/v1/login"}
    )

    (result,) = fleet.run()

    assert result["error"] == "Mosyle sign-in API HTTP 307" + HTTP
    # The password went to Mosyle once and nowhere else.
    (request,) = fleet.api.requests
    assert request.url.host == HOST


def test_a_page_that_redirects_is_refused_without_being_followed(fleet):
    fleet.api.logs_answer(
        lambda number: httpx.Response(
            302, headers={"Location": "https://collector.example.net/v1/adminlogs"}
        )
    )

    (result,) = fleet.run()

    assert result["error"] == "Mosyle API HTTP 302" + HTTP
    assert {r.url.host for r in fleet.api.requests} == {HOST}
    assert fleet.cursor().last_ts == NOW - timedelta(days=2)


def test_a_bearer_token_mosyle_stops_accepting_is_renewed_once(fleet):
    refused = []

    def answer(request):
        if str(request.url) == LOGS and not refused:
            refused.append(request)
            return httpx.Response(401, json={"error": "Unauthorized"})
        return None

    fleet.api.answer = answer

    (result,) = fleet.run()

    # The refused page is read again after one further sign-in.
    assert (result["pages"], result["created_events"]) == (2, 2)
    assert [str(r.url) for r in fleet.api.requests] == [LOGIN, LOGS, LOGIN, LOGS, LOGS]


def test_a_token_refused_again_after_renewal_is_reported(fleet):
    fleet.api.logs_answer(lambda number: httpx.Response(401))

    (result,) = fleet.run()

    assert result["error"] == "Mosyle API HTTP 401" + HTTP
    # One renewal, then the error: the password is sent twice at most.
    assert [str(r.url) for r in fleet.api.requests] == [LOGIN, LOGS, LOGIN, LOGS]
    assert fleet.cursor().last_ts == NOW - timedelta(days=2)


def test_a_second_refusal_later_in_the_run_is_not_renewed_again(fleet):
    seen = []

    def answer(request):
        if str(request.url) != LOGS:
            return None
        seen.append(request)
        # The first page and the third are refused.
        return httpx.Response(401) if len(seen) in (1, 3) else None

    fleet.api.answer = answer

    (result,) = fleet.run()

    assert result["error"] == "Mosyle API HTTP 401" + HTTP
    assert len(fleet.api.sign_ins()) == 2
    # The first window was stored before the second refusal.
    assert fleet.cursor().last_ts == NOW - timedelta(days=1)


@pytest.mark.parametrize(
    "response,message",
    [
        (
            httpx.Response(200, content=b"[" * 9_000_000),
            "Mosyle response exceeds the 8 MiB limit",
        ),
        (
            httpx.Response(200, content=b"<html>" + MARKER.encode()),
            "Mosyle returned invalid JSON",
        ),
        (httpx.Response(200, content=b"[" * 100_000), "Mosyle returned invalid JSON"),
        (httpx.Response(200, json=[MARKER]), "Mosyle returned an invalid JSON object"),
        (httpx.Response(500, text=MARKER), "Mosyle API HTTP 500" + HTTP),
        (
            httpx.Response(429, text=MARKER),
            "Mosyle rate limit reached; the next run continues from the cursor",
        ),
    ],
)
def test_a_page_that_fails_keeps_the_windows_before_it(fleet, response, message):
    def answer(request):
        if str(request.url) != LOGS:
            return None
        return None if len(fleet.api.asked()) == 1 else response

    fleet.api.answer = answer

    (result,) = fleet.run()

    assert result == {"stream": "adminlogs", "error": message, **RESULT}
    # The first window was stored and the cursor stands at its end.
    assert [e.action for e in fleet.events()] == ["admin.save_device_group"]
    assert fleet.cursor().last_ts == NOW - timedelta(days=1)


def test_a_network_failure_is_reported_and_keeps_the_cursor(fleet):
    def answer(request):
        if str(request.url) == LOGS:
            raise httpx.ConnectError("connect to " + MARKER)
        return None

    fleet.api.answer = answer

    (result,) = fleet.run()

    assert result["error"] == "Mosyle network or TLS failure; cursor retained"
    assert fleet.cursor().last_ts == NOW - timedelta(days=2)


@pytest.mark.parametrize(
    "field,value",
    [
        ("access_token", ""),
        ("access_token", "two words"),
        ("access_token", "x" * 513),
        ("access_token", None),
        ("email", ""),
        ("email", "not-an-address"),
        ("email", "one@example.org, two@example.org"),
        ("password", ""),
        ("password", "line\nbreak"),
        ("password", "x" * 1025),
        ("password", None),
    ],
)
def test_credentials_that_cannot_be_used_send_nothing(fleet, field, value):
    setattr(fleet.state, field, value)

    (result,) = fleet.run()

    assert result == {
        "error": "Set the Mosyle API access token and the email address and "
        "password of the administrator it signs in as",
        **RESULT,
    }
    assert fleet.clients == [] and fleet.api.requests == []


def test_settings_that_cannot_run_send_nothing(fleet, monkeypatch):
    fleet.inputs["streams"] = [{"stream": "devices"}]
    assert fleet.run()[0]["error"] == "Invalid Mosyle configuration; check streams"
    fleet.inputs["streams"] = []
    assert fleet.run()[0] == {
        "skipped": True,
        "reason": "Add the Mosyle adminlogs stream",
        **RESULT,
    }
    monkeypatch.setattr(settings, "demo_mode", True)
    assert fleet.run()[0]["reason"] == "Mosyle disabled (or demo mode)"
    assert fleet.clients == [] and fleet.api.requests == []


def test_an_unexpected_failure_is_described_generically(fleet):
    def answer(request):
        raise ValueError("resolved " + MARKER)

    fleet.api.answer = answer

    (result,) = fleet.run()

    assert result == {
        "stream": "adminlogs",
        "error": "Collection failed; check the input, credentials, upstream "
        "service and storage configuration",
        **RESULT,
    }


def test_the_tasks_time_limit_ends_the_whole_run(fleet):
    def answer(request):
        raise SoftTimeLimitExceeded()

    fleet.api.answer = answer

    with pytest.raises(SoftTimeLimitExceeded):
        fleet.run("db:one", "db:two")

    assert len(fleet.api.requests) == 1


def test_the_server_is_mosyles_own_and_no_setting_names_another():
    assert mosyle.API == "https://businessapi.mosyle.com/v1"
    declared = registry.get("mosyle")
    # A connection holds a sign-in and a token, and no endpoint to send them to.
    assert declared.public_fields == ("email",)
    assert declared.secret_fields == ("access_token", "password")
    assert not [
        name
        for name in type(settings).model_fields
        if name.startswith("mosyle_") and "url" in name
    ]


ONE = "Mosyle collects one stream: adminlogs"


@pytest.mark.parametrize(
    "config,message",
    [
        ("adminlogs", "Mosyle settings must be an object"),
        ({"streams": "adminlogs"}, ONE),
        ({"streams": [{"stream": "adminlogs"}] * 2}, ONE),
        ({"streams": ["adminlogs"]}, ONE),
        ({"streams": [{"stream": ["adminlogs"]}]}, ONE),
        ({"streams": [{"stream": "action_logs"}]}, ONE),
        (
            {"streams": [{"stream": "adminlogs", "label": "x" * 129}]},
            "Mosyle label must be at most 128 characters",
        ),
        (
            {"streams": [{"stream": "adminlogs", "label": None}]},
            "Mosyle label must be at most 128 characters",
        ),
        (
            {"initial_lookback_days": 368},
            "Mosyle initial_lookback_days must be an integer between 0 and 367",
        ),
        (
            {"window_hours": 0},
            "Mosyle window_hours must be an integer between 1 and 168",
        ),
        (
            {"overlap_minutes": 1.5},
            "Mosyle overlap_minutes must be an integer between 0 and 1440",
        ),
        (
            {"max_pages": False},
            "Mosyle max_pages must be an integer between 1 and 1000",
        ),
    ],
)
def test_settings_outside_their_bounds_are_refused(config, message):
    with pytest.raises(mosyle.MosyleError, match="^" + message + "$"):
        mosyle.validate_config(config)


def test_settings_within_their_bounds_are_accepted():
    config = {
        "streams": [{"stream": "adminlogs", "label": "Fleet"}],
        "initial_lookback_days": 367,
        "window_hours": 168,
        "overlap_minutes": 1440,
        "max_pages": 1000,
    }
    assert mosyle.validate_config(config) is config
