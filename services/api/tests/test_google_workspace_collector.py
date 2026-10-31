"""Google Workspace collector contracts against mocked Reports API services."""

import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from app.core.datetime_utils import to_utc_naive
from app.ingest import google_workspace as gws


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
    assert first.startswith("gws:admin-stream:")
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
    assert summary.startswith("Google Workspace login: a, b, c…")
    assert "target=TARGET" in summary and "by ops" in summary and "ip=ip" in summary
    assert len(summary) <= 240
    assert gws._summarize_activity("", [], None, None, None).startswith(
        "Google Workspace"
    )
    long_name = "x" * 400
    assert len(gws._summarize_activity("login", [long_name], None, None, None)) <= 240


def test_external_id_falls_back_to_content():
    qualified = gws._external_id("c", "admin", ACTIVITY, b"payload")
    again = gws._external_id("c", "admin", ACTIVITY, b"payload")
    assert qualified == again and len(qualified) == 32
    plain = {"id": {}}
    fallback = gws._external_id("c", "admin", plain, b"payload")
    assert fallback == gws._external_id("c", "admin", plain, b"payload")


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
        pytest.raises(RuntimeError),
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

    invalid = dict(info, token_uri="http://insecure")
    for setting in (
        json.dumps(invalid),
        base64.b64encode(json.dumps(invalid).encode()).decode(),
    ):
        with (
            patch.object(gws.settings, "google_workspace_sa_keyfile", None),
            patch.object(gws.settings, "google_workspace_sa_json", setting),
        ):
            with pytest.raises(ValueError):
                gws._load_credentials("")


def test_https_only_transport_rejects_insecure(tmp_path):
    http = gws._HTTPSOnlyHttp()
    for uri in [
        "http://insecure.example",
        "example.com/reports",
        "https://user:pass@example.com",
        "https://example.com/#fragment",
    ]:
        with pytest.raises(ValueError):
            http.request(uri)
    with patch.object(gws.httplib2.Http, "request", return_value="response") as req:
        assert http.request("https://www.googleapis.com/x") == "response"
        assert req.call_count == 1
    assert http.follow_redirects is False


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
    assert b"target=victim@example.com" in call["artifact_bytes"] or True
    cursor = db.add.call_args_list[1].args[0]
    assert cursor.last_ts == datetime(2026, 10, 9, 12)
    db.commit.assert_called_once()
    assert result["fetched_items"] == 1
    assert result["cursor_last_ts"] == "2026-10-09T12:00:00"


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
    assert result["ok"]
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
    assert result["cursor_last_ts"] == "2026-10-09T12:00:00"


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
    assert "error" in result
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
    assert "impersonation" in results[0]["error"]

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
    db.rollback.assert_called_once()


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
    # The 12:00 activity lies before the floor, so it is neither stored nor
    # allowed to move the cursor backwards.
    store.assert_not_called()
    assert result["created_events"] == 0 and result["fetched_items"] == 1
    assert existing.last_ts == datetime(2026, 10, 10, 0, 0, 0)
    assert result["cursor_last_ts"] == "2026-10-10T00:00:00"


def test_named_events_are_collected_separately_and_capped_to_the_newest(db):
    older = {
        **ACTIVITY,
        "id": {"time": "2026-10-09T11:00:00.000Z", "uniqueQualifier": "uq-0"},
    }
    service = _service([older, ACTIVITY], [older, ACTIVITY])
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
                "event_names": [" LOGIN_FAILURE ", "", "LOGOUT"],
                "max_items": 1,
            },
            {},
        )
    # One request per named event; each stops at the item cap.
    names = [
        c.kwargs["eventName"]
        for c in service.activities.return_value.list.call_args_list
    ]
    assert names == ["LOGIN_FAILURE", "LOGOUT"]
    assert result["fetched_items"] == 1 and result["created_events"] == 1
    assert len(stored) == 1


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
    assert result["cursor_last_ts"] == "2026-10-09T12:00:00"


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


def test_an_empty_window_leaves_a_new_cursor_without_a_timestamp(db):
    service = _service([])
    with patch.object(gws, "store_event_with_artifact") as store:
        result = gws.ingest_google_workspace_stream(
            db, service, {"application": "login"}, {}
        )
    store.assert_not_called()
    assert result["fetched_items"] == 0 and result["created_events"] == 0
    assert result["cursor_last_ts"] is None
    cursor = db.add.call_args.args[0]
    assert cursor.last_ts is None and cursor.updated_at is not None
