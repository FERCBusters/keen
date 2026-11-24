"""Google Workspace collector contracts against mocked Reports API services."""

import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.ingest import google_workspace as gws
from mosp.core.datetime_utils import to_utc_naive

pytestmark = pytest.mark.usefixtures("no_evidence_purge")


@pytest.fixture
def db():
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = None
    return db


ACTIVITY = {
    "id": {"time": "2026-10-09T12:00:00.000Z", "uniqueQualifier": "uq-1"},
    "actor": {"email": " ops@example.com "},
    "ipAddress": "203.0.113.9",
    "events": [
        {
            "name": "LOGIN_FAILURE",
            "parameters": [
                {"name": "USER_EMAIL", "value": "victim@example.com"},
                {"name": "ATTEMPT", "intValue": 3},
                {"name": "FLAG", "boolValue": True},
            ],
        },
        "not-a-dict",
        {"name": "LOGIN_FAILURE", "parameters": "broken"},
        {"name": " LOGOUT ", "parameters": [{"name": ""}, {"intValue": 1}]},
    ],
}


def test_timestamp_parsing_and_formatting():
    assert gws._parse_rfc3339("2026-10-09T12:00:00+11:00") == datetime(
        2026, 10, 9, 1, 0, 0
    )
    assert gws._parse_rfc3339("2026-10-09T12:00:00Z") == datetime(2026, 10, 9, 12)
    for value in (None, "", "   ", "not-a-date"):
        assert gws._parse_rfc3339(value) is None
    assert gws._rfc3339(datetime(2026, 10, 9, 12)) == "2026-10-09T12:00:00Z"
    assert gws._rfc3339(
        datetime(2026, 10, 9, 23, tzinfo=timezone(timedelta(hours=4)))
    ) == ("2026-10-09T19:00:00Z")
    assert gws._sanitize_hint("  Admin Login (Fall Back)! ") == "admin-login-fall-back"
    assert len(gws._sanitize_hint("x" * 80)) == 40
    assert gws._sanitize_hint("") == ""


def test_cursor_name_stable_and_independent():
    stream = {"name": "Admin Stream", "application": " admin ", "user_key": "ALL"}
    first = gws._cursor_name(stream)
    again = gws._cursor_name(dict(stream))
    assert first == "gws:admin-stream:4b6ea10c79e2"
    assert first == again
    assert gws._cursor_name({"name": "  "}) == gws._cursor_name({"name": ""})
    assert gws._cursor_name({"application": "login"}) != gws._cursor_name(
        {"application": "admin"}
    )
    stream["event_names"] = ["b", "a"]
    other = gws._cursor_name(stream)
    stream["event_names"] = ["a", "b"]
    assert other == gws._cursor_name(stream)
    stream["event_names"] = "not-a-list"
    assert gws._cursor_name(stream) == gws._cursor_name(
        {"name": "Admin Stream", "application": "admin", "user_key": "all"}
    )


def test_event_names_and_params():
    assert gws._event_names(ACTIVITY) == ["LOGIN_FAILURE", "LOGOUT"]
    assert gws._event_names({}) == []
    params = gws._event_params(ACTIVITY)
    assert params["USER_EMAIL"] == "victim@example.com"
    assert params["ATTEMPT"] == "3"
    assert params["FLAG"] == "True"
    assert not gws._event_params({})
    assert "LOGOUT" not in params or "LOGOUT" in params
    assert gws._pick(params, ["USER_EMAIL"]) == "victim@example.com"
    assert gws._pick(params, ["MISSING", "USER_EMAIL"]) == "victim@example.com"
    assert gws._pick(params, ["  "]) == ""
    assert gws._pick({}, ["x"]) == ""


def test_outcome_and_summary():
    assert gws._outcome_from_names(["LOGIN_FAILURE"]) == "failure"
    assert gws._outcome_from_names(["login_success"]) == "success"
    assert gws._outcome_from_names(["rejected_login"]) == "failure"
    assert gws._outcome_from_names(["logged_in"]) == "success"
    assert gws._outcome_from_names(["something"]) == "info"
    summary = gws._summarize_activity(
        "login", ["a", "b", "c", "d"], "ops", "TARGET", "ip"
    )
    assert summary == "Google Workspace login: a, b, c… target=TARGET by ops ip=ip"
    assert "target=TARGET" in summary and "by ops" in summary and "ip=ip" in summary
    assert len(summary) <= 240
    assert gws._summarize_activity("", [], None, None, None).startswith(
        "Google Workspace"
    )
    long_name = "x" * 400
    assert len(gws._summarize_activity("login", [long_name], None, None, None)) <= 240


def test_external_id_falls_back_to_content():
    qualified = gws._external_id("c", "admin", ACTIVITY)
    again = gws._external_id("c", "admin", ACTIVITY)
    assert qualified == again and len(qualified) == 32
    # Without Google's ID, the same activity read again is the same record.
    plain = {"id": {}, "events": [{"name": "X"}]}
    fallback = gws._external_id("c", "admin", plain)
    assert fallback == gws._external_id("c", "admin", dict(plain))
    assert fallback != gws._external_id("c", "admin", {"id": {}})


def test_load_credentials_sources(monkeypatch):
    info = {
        "type": "service_account",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_email": "sa@example.iam.gserviceaccount.com",
    }
    with (
        patch.object(gws.settings, "google_workspace_sa_keyfile", None),
        patch.object(gws.settings, "google_workspace_sa_json_b64", None),
        patch.object(gws.settings, "google_workspace_sa_json", None),
        pytest.raises(
            RuntimeError,
            match="^Google Workspace service-account credentials are missing$",
        ),
    ):
        gws._load_credentials("")
    captured = {}
    fake_creds = MagicMock()
    fake_creds.with_subject.return_value = fake_creds

    def from_info(data, scopes=None):
        captured["scopes"] = scopes
        return fake_creds

    with (
        patch.object(
            gws.service_account.Credentials,
            "from_service_account_info",
            staticmethod(from_info),
        ),
        patch.object(gws.settings, "google_workspace_sa_json", json.dumps(info)),
        patch.object(gws.settings, "google_workspace_sa_keyfile", None),
    ):
        creds = gws._load_credentials("admin@example.com")
        assert creds is fake_creds
        assert captured["scopes"] == gws.SCOPES
        fake_creds.with_subject.assert_called_once_with("admin@example.com")

    invalid = json.dumps(dict(info, token_uri="http://insecure"))
    for raw, encoded in ((invalid, None), (None, base64.b64encode(invalid.encode()))):
        with (
            patch.object(gws.settings, "google_workspace_sa_keyfile", None),
            patch.object(gws.settings, "google_workspace_sa_json", raw),
            patch.object(
                gws.settings,
                "google_workspace_sa_json_b64",
                encoded.decode() if encoded else None,
            ),
        ):
            with pytest.raises(
                ValueError,
                match="^Google Workspace token endpoint must use HTTPS without "
                "embedded credentials or a fragment$",
            ):
                gws._load_credentials("")


def test_https_only_transport_rejects_insecure():
    http = gws._HTTPSOnlyHttp()
    with patch.object(gws.httplib2.Http, "request", return_value="response") as req:
        for uri in [
            "http://insecure.example",
            "example.com/reports",
            "https://user:pass@example.com",
            "https://example.com/#fragment",
        ]:
            # Refused before any connection, so a credential header goes nowhere.
            with pytest.raises(
                ValueError, match="^Google Workspace requests require HTTPS$"
            ):
                http.request(uri, headers={"Authorization": "synthetic-secret"})
        assert req.call_args_list == []
        assert http.request("https://www.googleapis.com/x") == "response"
        req.assert_called_once_with("https://www.googleapis.com/x")
    assert (
        http.follow_redirects,
        http.follow_all_redirects,
        http.disable_ssl_certificate_validation,
        http.proxy_info,
    ) == (False, False, False, None)


def test_iter_activities_follows_pagination():
    pages = [{"items": [{"k": 1}]}, {"items": [{"k": 2}, "junk"]}, {"items": []}]
    calls = []
    service = MagicMock()

    def list_next(req, resp):
        calls.append(1)
        return None if req.page >= len(pages) else MagicMock(page=req.page + 1)

    service.activities.return_value.list.side_effect = lambda *a, **kw: MagicMock(
        page=0
    )
    service.activities.side_effect = None

    requests = {}
    for index in range(len(pages)):
        req = MagicMock(execute=MagicMock(return_value=pages[index]))
        requests[index] = req
    service.activities.return_value.list.side_effect = lambda *a, **kw: requests[0]
    service.activities.return_value.list_next.side_effect = list_next
    for index in range(1, len(pages)):
        requests.setdefault(
            index, MagicMock(execute=MagicMock(return_value=pages[index]))
        )
    req = MagicMock(page=0)
    service.activities.return_value.list.return_value = MagicMock(page=0)
    service.activities.return_value.list_next.side_effect = list_next

    def mixed(req, resp):
        current = req.page
        if current >= len(pages) - 1:
            return None
        return requests[current + 1]

    requests[0].page = 0
    for index in range(1, len(pages)):
        requests[index].page = index
    service.activities.return_value.list_next.side_effect = mixed
    service.activities.return_value.list.return_value = requests[0]
    items = list(
        gws._iter_activities(
            service,
            user_key="all",
            application="admin",
            start_time="2026-10-09T00:00:00Z",
            end_time=None,
            event_name=None,
            max_results=10,
        )
    )
    assert [i.get("k") for i in items if i] == [1, 2]
    service.activities.return_value.list.assert_called_with(
        userKey="all",
        applicationName="admin",
        startTime="2026-10-09T00:00:00Z",
        maxResults=10,
        eventName=None,
        endTime=None,
    )


def _read_to_now(value, lag=timedelta(seconds=gws.DEFAULT_REPORT_LAG_SECONDS)):
    """Whether a cursor moved as far as a run reading to now moves it: to the
    application's report lag before now, in whole seconds."""
    at = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    settled = datetime.now(timezone.utc).replace(tzinfo=None) - lag
    return at.microsecond == 0 and timedelta(0) <= settled - at < timedelta(minutes=1)


def test_stream_rejects_and_skips(db):
    assert gws.ingest_google_workspace_stream(
        db, None, {"enabled": False, "name": "s"}, {}
    ) == {"skipped": True, "reason": "stream disabled", "name": "s"}
    assert gws.ingest_google_workspace_stream(db, None, {}, {}) == {
        "ok": False,
        "error": "missing application",
        "name": None,
    }


def test_stream_ingests_and_updates_cursor(db):
    service = MagicMock()
    service.activities.return_value.list.return_value = MagicMock(
        execute=MagicMock(return_value={"items": [ACTIVITY]})
    )
    service.activities.return_value.list_next.return_value = None
    stored = []
    with patch.object(
        gws,
        "store_event_with_artifact",
        side_effect=lambda db, **kw: stored.append(kw) or {"deduped": False},
    ) as store:
        result = gws.ingest_google_workspace_stream(
            db,
            service,
            {
                "application": "login",
                "name": "Logins ",
                "user_key": "all",
                "system": "prod-gws",
                "severity": "not-a-number",
                "event_names": "not-a-list",
                "max_results": 5000,
            },
            {},
        )
    assert result["ok"] is True
    assert result["name"] == "Logins"
    assert result["created_events"] == 1
    call = store.call_args.kwargs
    assert call["source"] == "google_workspace"
    assert call["system"] == "prod-gws"
    assert call["actor"] == "ops@example.com"
    assert call["action"] == "LOGIN_FAILURE+LOGOUT"
    assert call["outcome"] == "failure"
    assert call["severity"] == 3
    assert call["external_id"].startswith("f" * 32) or len(call["external_id"]) == 32
    assert call["raw_pointer"]["google_workspace"]["ip"] == "203.0.113.9"
    assert call["raw_pointer"]["google_workspace"]["stream"] == "Logins"
    assert "victim" not in str(call["raw_pointer"])
    assert call["captured_by"] == "keen:google_workspace"
    # The evidence artifact keeps the activity as fetched, the subject included,
    # while the pointer above leaves it out.
    artifact = json.loads(call["artifact_bytes"])
    assert artifact["activity"]["events"][0]["parameters"][0] == {
        "name": "USER_EMAIL",
        "value": "victim@example.com",
    }
    # The cursor moves to the report lag before the present it read to.
    cursor = db.add.call_args_list[1].args[0]
    assert _read_to_now(cursor.last_ts)
    assert result["fetched_items"] == 1 and result["caught_up"] is True
    assert _read_to_now(result["cursor_last_ts"])


def test_stream_event_filter_and_outcome_overrides(db):
    service = MagicMock()
    service.activities.return_value.list.return_value = MagicMock(
        execute=MagicMock(return_value={"items": [ACTIVITY]})
    )
    service.activities.return_value.list_next.return_value = None
    stored = []
    with patch.object(
        gws,
        "store_event_with_artifact",
        side_effect=lambda db, **kw: stored.append(kw) or {"deduped": False},
    ):
        result = gws.ingest_google_workspace_stream(
            db,
            service,
            {
                "application": "login",
                "outcome": "warning",
                "severity": 9,
                "user_key": "user@example.com",
            },
            {},
        )
    assert result["ok"] is True
    assert stored[0]["outcome"] == "warning"
    assert stored[0]["severity"] == 9
    call = service.activities.return_value.list.call_args.kwargs
    assert call["userKey"] == "user@example.com"


def test_stream_skips_items_before_cursor_floor(db):
    existing = MagicMock(last_ts=datetime(2026, 10, 9, 12, 0, 0))
    db.query.return_value.filter.return_value.one_or_none.return_value = existing
    service = MagicMock()
    service.activities.return_value.list.return_value = MagicMock(
        execute=MagicMock(return_value={"items": [ACTIVITY]})
    )
    service.activities.return_value.list_next.return_value = None
    with patch.object(gws, "store_event_with_artifact", return_value={"deduped": True}):
        result = gws.ingest_google_workspace_stream(
            db, service, {"application": "login"}, {"overlap_seconds": 3600}
        )
    assert result["created_events"] == 0
    assert _read_to_now(result["cursor_last_ts"])


def test_stream_http_error_returns_report(db):
    from googleapiclient.errors import HttpError

    service = MagicMock()

    def raise_http(*args, **kwargs):
        req = MagicMock()
        req.execute.side_effect = HttpError(MagicMock(status=403), b"forbidden")
        return req

    service.activities.return_value.list.side_effect = raise_http
    result = gws.ingest_google_workspace_stream(
        db, service, {"application": "login"}, {}
    )
    assert result["ok"] is False
    assert result == {
        "ok": False,
        "name": "login",
        "application": "login",
        "error": "Collection failed; check the input, credentials, upstream service and storage configuration",
    }
    assert result["application"] == "login"


def test_all_orchestrates_streams_and_failure_reporting(db):
    with patch.object(gws.settings, "google_workspace_enabled", False):
        results = gws.ingest_google_workspace_all(db)
        assert results[0]["skipped"] and results[0]["reason"]

    service = MagicMock()
    service.activities.return_value.list.return_value = MagicMock(
        execute=MagicMock(return_value={"items": [ACTIVITY]})
    )
    service.activities.return_value.list_next.return_value = None
    with (
        patch.object(gws.settings, "google_workspace_enabled", True),
        patch.object(
            gws, "load_google_workspace_config", return_value={"enabled": False}
        ),
    ):
        result = gws.ingest_google_workspace_all(db)
    assert result[0]["skipped"] and result[0]["reason"] == "config enabled=false"

    with (
        patch.object(gws.settings, "google_workspace_enabled", True),
        patch.object(gws, "load_google_workspace_config", return_value={}),
    ):
        results = gws.ingest_google_workspace_all(db)
    assert results[0]["ok"] is False
    assert results[0]["error"] == (
        "Missing impersonation email. Set GOOGLE_WORKSPACE_IMPERSONATE or "
        "config: impersonate"
    )

    with (
        patch.object(gws.settings, "google_workspace_enabled", True),
        patch.object(
            gws,
            "load_google_workspace_config",
            return_value={
                "impersonate": "admin@example.com",
                "since_minutes": 60,
                "overlap_seconds": 90,
                "streams": [
                    {"enabled": False, "name": "skipped"},
                    {"application": "login"},
                    "junk",
                ],
            },
        ),
        patch.object(gws, "_reports_service", return_value=service),
        patch.object(
            gws,
            "store_event_with_artifact",
            return_value={"deduped": False},
        ),
    ):
        results = gws.ingest_google_workspace_all(db)
    assert results[0]["skipped"] and results[0]["name"] == "skipped"
    assert results[1]["ok"] is True and results[1]["created_events"] == 1
    assert len(results) == 2
    db.commit.assert_called()


def test_all_reports_stream_failures(db):
    service = MagicMock()

    def raise_error(*args, **kwargs):
        raise RuntimeError("Collection failed: transport")

    service.activities.return_value.list.return_value = MagicMock(
        execute=MagicMock(return_value={"items": []})
    )
    with (
        patch.object(gws.settings, "google_workspace_enabled", True),
        patch.object(
            gws,
            "load_google_workspace_config",
            return_value={
                "impersonate": "admin@example.com",
                "streams": [{"application": "login"}, "junk"],
            },
        ),
        patch.object(gws, "_reports_service", return_value=service),
        patch.object(gws, "ingest_google_workspace_stream", side_effect=raise_error),
    ):
        results = gws.ingest_google_workspace_all(db)
    assert len(results) >= 1 and any(
        r.get("ok") is False and r.get("error") for r in results
    )
    db.rollback.assert_called_once_with()


def test_load_config():
    with patch.object(gws, "load_document", return_value={"streams": []}) as document:
        assert gws.load_google_workspace_config("fixture.yml") == {"streams": []}
    document.assert_called_once_with("google_workspace", "fixture.yml")


def test_reports_service_builds_authorized_client():
    with (
        patch.object(gws, "_load_credentials", return_value=MagicMock()) as load,
        patch("app.ingest.google_workspace.AuthorizedHttp") as authorized,
        patch("app.ingest.google_workspace.build") as build,
        patch("app.ingest.google_workspace._HTTPSOnlyHttp") as https_only,
    ):
        service = gws._reports_service("admin@example.com")
    load.assert_called_once_with("admin@example.com")
    build.assert_called_once_with(
        "admin",
        "reports_v1",
        http=authorized.return_value,
        cache_discovery=False,
    )
    assert service is build.return_value
    https_only.assert_called_once_with()


def test_reports_discovery_tracks_service_activity_calls():
    service = MagicMock()
    service.activities.return_value.list.return_value = MagicMock(page="req")
    assert gws._pick({}, []) == ""


def _service(*pages):
    service = MagicMock()
    service.activities.return_value.list.return_value = MagicMock(
        execute=MagicMock(side_effect=[{"items": list(items)} for items in pages])
    )
    service.activities.return_value.list_next.return_value = None
    return service


def test_one_unreadable_activity_neither_stops_nor_freezes_the_stream(db):
    db.query.return_value.filter.return_value.one_or_none.return_value = None
    unreadable = {
        **ACTIVITY,
        "id": {**ACTIVITY["id"], "uniqueQualifier": "uq-2"},
        "ipAddress": {"v4": "192.0.2.1"},
    }
    service = _service([unreadable, ACTIVITY])
    with patch.object(
        gws, "store_event_with_artifact", return_value={"deduped": False}
    ) as store:
        result = gws.ingest_google_workspace_stream(
            db, service, {"application": "login"}, {}
        )
    assert (result["created_events"], result["invalid_records"]) == (1, 1)
    assert store.call_count == 1


def test_naive_timestamps_pass_through_unchanged():
    naive = datetime(2026, 10, 9, 12)
    assert to_utc_naive(naive) is naive


def test_a_zone_aware_cursor_sets_the_start_time_and_floor(db):
    # Cursors read back with a zone are normalised, so the start time sent to
    # Google and the overlap floor both follow the stored instant.
    existing = MagicMock(last_ts=datetime(2026, 10, 10, 0, 0, 0, tzinfo=timezone.utc))
    db.query.return_value.filter.return_value.one_or_none.return_value = existing
    service = _service([ACTIVITY])
    with patch.object(gws, "store_event_with_artifact") as store:
        result = gws.ingest_google_workspace_stream(
            db, service, {"application": "login"}, {"overlap_seconds": 60}
        )
    call = service.activities.return_value.list.call_args.kwargs
    assert call["startTime"] == "2026-10-09T23:59:00Z"
    # The 12:00 activity lies before the floor, so it is not stored; the
    # window read in full moves the cursor to its end.
    store.assert_not_called()
    assert result["created_events"] == 0 and result["fetched_items"] == 1
    assert _read_to_now(existing.last_ts)


class _Request:
    def __init__(self, pages, size, offset=0):
        self.pages, self.size, self.offset = pages, size, offset

    def execute(self):
        return {"items": self.pages[self.offset : self.offset + self.size]}


class FakeReports:
    """Reports activities.list over a fixed activity set: filtered by time and
    event name, paged by maxResults, newest first unless ``oldest_first``."""

    def __init__(self, activities, oldest_first=False):
        self.activities_ = activities
        self.oldest_first = oldest_first
        self.calls = []

    def activities(self):
        return self

    def list(self, **kw):
        self.calls.append(kw)
        start = gws._parse_rfc3339(kw["startTime"])
        end = gws._parse_rfc3339(kw["endTime"]) if kw["endTime"] else None
        found = [
            a
            for a in self.activities_
            if gws._parse_rfc3339(a["id"]["time"]) >= start
            and (end is None or gws._parse_rfc3339(a["id"]["time"]) < end)
            and (kw["eventName"] in (None, a["events"][0]["name"]))
        ]
        found.sort(key=lambda a: a["id"]["time"], reverse=not self.oldest_first)
        return _Request(found, kw["maxResults"])

    def list_next(self, req, resp):
        nxt = req.offset + req.size
        if nxt >= len(req.pages):
            return None
        return _Request(req.pages, req.size, nxt)


def _activity(minute, name="LOGIN_FAILURE", n=0):
    return {
        "id": {
            "time": f"2026-10-09T12:{minute:02d}:{n % 60:02d}.{n // 60:03d}Z",
            "uniqueQualifier": f"uq-{minute}-{n}-{name}",
        },
        "events": [{"name": name}],
    }


def _run_stream(db, service, cfg, cursor, now):
    db.query.return_value.filter.return_value.one_or_none.return_value = cursor
    stored = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    with (
        patch.object(gws, "datetime", Clock),
        patch.object(
            gws,
            "store_event_with_artifact",
            side_effect=lambda db, **kw: stored.append(kw) or {"deduped": False},
        ),
    ):
        result = gws.ingest_google_workspace_stream(db, service, cfg, {})
    return result, stored


@pytest.fixture
def reports():
    return FakeReports


NOW = datetime(2026, 10, 9, 13, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("oldest_first", [False, True])
def test_named_events_each_read_to_their_end(db, reports, oldest_first):
    # Earlier names' volume no longer crowds later names out of the run.
    service = reports(
        [_activity(1, "LOGIN_FAILURE", n) for n in range(25)]
        + [_activity(2, "LOGIN_SUCCESS"), _activity(3, "LOGIN_SUCCESS")],
        oldest_first=oldest_first,
    )
    cursor = MagicMock(last_ts=datetime(2026, 10, 9, 12, 0, 30))
    result, stored = _run_stream(
        db,
        service,
        {
            "application": "login",
            "event_names": ["LOGIN_FAILURE", "LOGIN_SUCCESS"],
            "max_items": 25,
            "max_results": 10,
            "overlap_seconds": 60,
        },
        cursor,
        NOW,
    )
    assert [s["action"] for s in stored].count("LOGIN_SUCCESS") == 2
    assert result["fetched_items"] == 27
    # The cursor stays the report lag behind the present it read to.
    assert cursor.last_ts == datetime(2026, 10, 9, 12, 50)


@pytest.mark.parametrize("oldest_first", [False, True])
def test_a_listing_over_max_items_is_read_in_narrower_windows(
    db, reports, oldest_first
):
    # 30 activities after the cursor, the one that matters first, read with a
    # cap of 10: whichever end Google lists first, nothing is passed over.
    target = _activity(1, "CHANGE_PASSWORD")
    later = [_activity(m, n=n) for m in range(20, 50) for n in range(1)]
    service = reports([target, *later], oldest_first=oldest_first)
    cursor = MagicMock(last_ts=datetime(2026, 10, 9, 12, 0, 30))
    cfg = {"application": "admin", "max_items": 10, "overlap_seconds": 1}
    seen = []
    for _ in range(10):
        result, stored = _run_stream(db, service, cfg, cursor, NOW)
        assert result["ok"] is True
        seen += [
            s["raw_pointer"]["google_workspace"]["uniqueQualifier"] for s in stored
        ]
        if cursor.last_ts >= datetime(2026, 10, 9, 12, 49):
            break
    assert target["id"]["uniqueQualifier"] in seen
    assert {a["id"]["uniqueQualifier"] for a in later} <= set(seen)
    # Narrowed reads name their end; the cursor never passed an unread one.
    assert any(c["endTime"] for c in service.calls)


def test_a_minute_holding_more_than_max_items_is_reported(db, reports):
    burst = [_activity(5, n=n) for n in range(12)]
    service = reports(burst)
    cursor = MagicMock(last_ts=datetime(2026, 10, 9, 12, 5, 0))
    now = datetime(2026, 10, 9, 12, 5, 50, tzinfo=timezone.utc)
    result, stored = _run_stream(
        db,
        service,
        {"application": "admin", "max_items": 5, "overlap_seconds": 1},
        cursor,
        now,
    )
    assert result["ok"] is False
    assert result["error"].startswith("More than 5 activities in under a minute")
    assert result["created_events"] == 5
    assert result["caught_up"] is True
    # The cursor stays the report lag behind, so the next run reads the
    # minute again without counting it, and stores the rest.
    assert cursor.last_ts == datetime(2026, 10, 9, 12, 5)
    assert cursor.meta["minutes_over_limit"] == ["2026-10-09T12:05:00Z"]
    later = datetime(2026, 10, 9, 12, 10, tzinfo=timezone.utc)
    again, rest = _run_stream(
        db,
        service,
        {"application": "admin", "max_items": 5, "overlap_seconds": 1},
        cursor,
        later,
    )
    assert again["ok"] is True
    stored_ids = {s["raw_pointer"]["google_workspace"]["uniqueQualifier"] for s in rest}
    assert {a["id"]["uniqueQualifier"] for a in burst} <= stored_ids


def test_activities_without_a_time_are_stamped_on_arrival(db):
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    untimed = {**{k: v for k, v in ACTIVITY.items() if k != "id"}, "actor": "ops"}
    service = _service([untimed])
    stored = []
    with patch.object(
        gws,
        "store_event_with_artifact",
        side_effect=lambda db, **kw: stored.append(kw) or {"deduped": False},
    ):
        result = gws.ingest_google_workspace_stream(
            db, service, {"application": "login"}, {}
        )
    assert result["created_events"] == 1
    (call,) = stored
    assert call["timestamp"] >= before
    assert len(call["external_id"]) == 32
    # An actor that is no object is unknown; it is never coerced from text.
    assert call["actor"] is None


def test_repeated_deliveries_of_one_activity_count_once(db):
    service = _service([ACTIVITY, ACTIVITY])
    with patch.object(
        gws,
        "store_event_with_artifact",
        side_effect=[{"deduped": False}, {"deduped": True}],
    ):
        result = gws.ingest_google_workspace_stream(
            db, service, {"application": "login"}, {}
        )
    assert result["fetched_items"] == 2 and result["created_events"] == 1
    assert _read_to_now(result["cursor_last_ts"])


def test_all_notes_when_no_streams_are_configured(db):
    for streams in ([], "login", None):
        with (
            patch.object(gws.settings, "google_workspace_enabled", True),
            patch.object(
                gws,
                "load_google_workspace_config",
                return_value={"impersonate": "admin@example.com", "streams": streams},
            ),
            patch.object(gws, "_reports_service") as reports,
        ):
            (result,) = gws.ingest_google_workspace_all(db)
        assert result["ok"] is True and result["created_events"] == 0
        assert result["note"] == "no streams configured"
        reports.assert_not_called()


def test_an_empty_window_read_in_full_moves_a_new_cursor_to_its_end(db):
    service = _service([])
    with patch.object(gws, "store_event_with_artifact") as store:
        result = gws.ingest_google_workspace_stream(
            db, service, {"application": "login"}, {}
        )
    store.assert_not_called()
    assert result["fetched_items"] == 0 and result["created_events"] == 0
    cursor = db.add.call_args.args[0]
    assert _read_to_now(cursor.last_ts) and cursor.updated_at is not None


def test_unnamed_events_and_parameters_without_one_value_are_left_out(db):
    activity = {
        "id": {"time": "2026-10-09T12:00:00.000Z", "uniqueQualifier": "uq-9"},
        "actor": {"email": "admin@example.com"},
        "events": [
            {"name": "  ", "parameters": [{"name": "USER_EMAIL", "value": "a@x.test"}]},
            {
                "name": "ADD_GROUP_MEMBER",
                "parameters": [
                    # A list-valued parameter has no single value to record.
                    {"name": "MEMBERS", "multiValue": ["b@x.test", "c@x.test"]},
                    {"name": "GROUP_EMAIL", "multiValue": ["d@x.test"]},
                    {"name": "GROUP_EMAIL", "value": "team@x.test"},
                ],
            },
        ],
    }
    stored = []
    with patch.object(
        gws,
        "store_event_with_artifact",
        side_effect=lambda db, **kw: stored.append(kw) or {"deduped": False},
    ):
        gws.ingest_google_workspace_stream(
            db, _service([activity]), {"application": "admin"}, {}
        )

    (call,) = stored
    assert call["action"] == "ADD_GROUP_MEMBER"
    parsed = json.loads(call["artifact_bytes"])["parsed"]
    assert parsed == {
        "event_names": ["ADD_GROUP_MEMBER"],
        "params": {"USER_EMAIL": "a@x.test", "GROUP_EMAIL": "team@x.test"},
    }


def test_a_burst_inside_the_overlap_never_holds_the_stream_back(db, reports):
    # More than max_items activities within the default three-minute overlap:
    # the burst is reported once, and every activity after it is still read.
    burst = [_activity(10, n=n) for n in range(200)]
    later = [_activity(m, "CHANGE_PASSWORD") for m in (11, 20, 45)]
    service = reports([*burst, *later])
    cursor = SimpleNamespace(last_ts=datetime(2026, 10, 9, 12, 0), meta={})
    cfg = {"application": "admin", "max_items": 50}
    stored, results = [], []
    for minutes in (15, 45, 75, 105):
        now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc) + timedelta(
            minutes=minutes
        )
        result, run = _run_stream(db, service, cfg, cursor, now)
        results.append(result)
        stored += [s["raw_pointer"]["google_workspace"]["uniqueQualifier"] for s in run]
    assert {a["id"]["uniqueQualifier"] for a in later} <= set(stored)
    errors = [r["error"] for r in results if not r["ok"]]
    assert len(errors) == 1
    assert errors[0].startswith("More than 50 activities in under a minute")
    assert results[-1]["caught_up"] is True
    assert cursor.last_ts == datetime(2026, 10, 9, 13, 35)


def test_activity_google_reports_late_is_still_collected(db, reports):
    service = reports([_activity(1, "CHANGE_PASSWORD")])
    cursor = SimpleNamespace(last_ts=datetime(2026, 10, 9, 12, 0), meta={})
    cfg = {"application": "admin", "overlap_seconds": 60}
    seen = set()
    for minutes, late in ((15, None), (45, _activity(10, "ADD_PRIVILEGE")), (75, None)):
        if late:
            # Reported eight minutes after the run at 12:15 read past it.
            service.activities_.append(late)
        now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc) + timedelta(
            minutes=minutes
        )
        result, run = _run_stream(db, service, cfg, cursor, now)
        seen |= {s["action"] for s in run}
        assert result["ok"] is True
    assert seen == {"CHANGE_PASSWORD", "ADD_PRIVILEGE"}
    assert cursor.last_ts == datetime(2026, 10, 9, 13, 5)


def test_a_narrowed_window_is_kept_for_the_next_run(db, reports):
    activities = [_activity(m, n=n) for m in range(0, 60) for n in range(10)]
    service = reports(activities)
    cursor = SimpleNamespace(last_ts=datetime(2026, 10, 9, 12, 0), meta={})
    cfg = {"application": "admin", "max_items": 15, "report_lag_seconds": 0}
    now = datetime(2026, 10, 9, 13, 0, tzinfo=timezone.utc)
    _run_stream(db, service, cfg, cursor, now)
    kept = cursor.meta["window_seconds"]
    assert 60 <= kept < 3600
    service.calls.clear()
    _run_stream(db, service, cfg, cursor, now)
    first = service.calls[0]
    span = gws._parse_rfc3339(first["endTime"]) - cursor_start(first)
    assert span <= timedelta(seconds=kept) + timedelta(seconds=180)


def cursor_start(call):
    return gws._parse_rfc3339(call["startTime"])


def test_volume_over_max_items_each_poll_is_caught_up_over_runs(db, reports):
    activities = [_activity(m, n=n) for m in range(0, 60) for n in range(10)]
    service = reports(activities)
    cursor = SimpleNamespace(last_ts=datetime(2026, 10, 9, 12, 0), meta={})
    cfg = {"application": "admin", "max_items": 15, "overlap_seconds": 180}
    now = datetime(2026, 10, 9, 13, 0, tzinfo=timezone.utc)
    seen, caught_up = set(), []
    for _ in range(60):
        result, run = _run_stream(db, service, cfg, cursor, now)
        caught_up.append(result["caught_up"])
        seen |= {s["raw_pointer"]["google_workspace"]["uniqueQualifier"] for s in run}
        assert result["ok"] is True
        if result["caught_up"]:
            break
    # A run that stops short of now says so, and later runs finish the work.
    assert caught_up[0] is False and caught_up[-1] is True
    assert {a["id"]["uniqueQualifier"] for a in activities} <= seen


def test_reading_again_for_late_activity_is_bounded_too(db, reports):
    activities = [_activity(m, n=n) for m in range(0, 60) for n in range(10)]
    service = reports(activities)
    cursor = SimpleNamespace(
        last_ts=datetime(2026, 10, 9, 12, 0),
        meta={"read_to": "2026-10-09T13:00:00Z"},
    )
    cfg = {"application": "admin", "max_items": 5, "report_lag_seconds": 0}
    now = datetime(2026, 10, 9, 13, 0, tzinfo=timezone.utc)
    seen = set()
    for _ in range(10):
        result, run = _run_stream(db, service, cfg, cursor, now)
        seen |= {s["raw_pointer"]["google_workspace"]["uniqueQualifier"] for s in run}
        if result["caught_up"]:
            break
    # The span already read holds more than ten times max_items, so it is read
    # in narrower windows rather than whole.
    assert any(
        gws._parse_rfc3339(c["endTime"]) - gws._parse_rfc3339(c["startTime"])
        < timedelta(minutes=30)
        for c in service.calls
    )
    assert {a["id"]["uniqueQualifier"] for a in activities} <= seen


def test_a_minute_already_reported_is_listed_once(db, reports):
    service = reports([_activity(5, n=n) for n in range(12)])
    cursor = SimpleNamespace(
        last_ts=datetime(2026, 10, 9, 12, 5),
        meta={"minutes_over_limit": ["2026-10-09T12:05:00Z"]},
    )
    now = datetime(2026, 10, 9, 12, 5, 50, tzinfo=timezone.utc)
    cfg = {"application": "admin", "max_items": 5, "overlap_seconds": 1}
    result, _ = _run_stream(db, service, cfg, cursor, now)
    assert result["ok"] is False
    assert cursor.meta["minutes_over_limit"] == ["2026-10-09T12:05:00Z"]


def test_activities_stored_already_are_not_prepared_again(db, reports):
    stored, fresh = _activity(1, "STORED"), _activity(2, "FRESH")
    service = reports([stored, fresh])
    known = gws._unique_id("admin", stored)
    db.query.return_value.filter.return_value.__iter__ = lambda self: iter([(known,)])
    cursor = SimpleNamespace(last_ts=datetime(2026, 10, 9, 12, 0), meta={})
    result, run = _run_stream(db, service, {"application": "admin"}, cursor, NOW)
    assert [s["action"] for s in run] == ["FRESH"]
    assert result["fetched_items"] == 2
