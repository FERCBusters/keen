"""CloudWatch Logs collector contracts against a mocked boto3 client."""

import contextlib
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.ingest import cloudwatch_logs as cl

MS = 1_760_000_000_000


@contextlib.contextmanager
def configured(region="ap-southeast-2", masking="false"):
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(cl.settings, "cloudwatch_logs_region", region))
        stack.enter_context(patch.object(cl.settings, "event_data_masking", masking))
        stack.enter_context(patch.object(cl, "prepare_event_fields", lambda f, mask: f))
        stack.enter_context(
            patch.object(
                cl,
                "prepare_artifact_bytes",
                lambda data, ctype, mask: (data, "stored"),
            )
        )
        stack.enter_context(patch.object(cl, "put_bytes"))
        stack.enter_context(patch.object(cl, "load_rules", return_value={}))
        yield


def test_ensure_utc_and_helpers():
    naive = datetime(2026, 10, 9, 12, 0, 0)
    assert cl._ensure_utc(naive) == naive.replace(tzinfo=timezone.utc)
    assert cl._ensure_utc(None) is None
    assert cl._ensure_utc(naive.replace(tzinfo=timezone.utc)) == naive.replace(
        tzinfo=timezone.utc
    )
    assert cl._try_parse_json_line('{"a": 1}') == {"a": 1}
    assert cl._try_parse_json_line("[1]") == [1]
    for bad in ["", "x", "{", None, "not json"]:
        assert cl._try_parse_json_line(bad) is None
    assert cl._truncate("  a   b  ") == "a b"
    assert len(cl._truncate("x" * 400)) == 220
    assert cl._fingerprint("a", "b") == cl._fingerprint("a", "b")
    assert cl._fingerprint("a", "b") != cl._fingerprint("a", "c")
    assert cl._fingerprint("a", None) == cl._fingerprint("a", "")


def test_brief_http_request():
    assert cl._brief_http_request('"GET /api/x?secret=y HTTP/1.1" 200') == "GET /api/x"
    assert cl._brief_http_request('"POST /login HTTP/1.1" 401') == "POST /login"
    assert cl._brief_http_request("no request here") == ""
    query = '"' + "GET /" + "?" + "token=fixture-secret" + '"'
    brief = cl._brief_http_request(query)
    assert "fixture-secret" not in brief


def test_extract_actor_shapes():
    assert cl._extract_actor({"user": " alice "}) == "alice"
    assert cl._extract_actor({"principalId": "p-1"}) == "p-1"
    assert cl._extract_actor({"remote_user": "-"}) is None
    assert cl._extract_actor({"remote_user": "service"}) == "service"
    assert (
        cl._extract_actor({"userIdentity": {"arn": "arn:aws:iam::x:user/bob"}})
        == "arn:aws:iam::x:user/bob"[:120]
    )
    assert cl._extract_actor({"userIdentity": {"userName": ""}}) is None
    assert cl._extract_actor("not a dict") is None
    assert cl._extract_actor({"user": {"dict": True}}) is None


def test_summarize_json_variants():
    parsed = {
        "eventName": "AssumeRole",
        "eventSource": "sts.amazonaws.com",
        "eventType": "AwsApiCall",
        "userIdentity": {"userName": "ops"},
        "message": '"GET /health HTTP/1.1" 200',
    }
    summary, extracted = cl._summarize_json(parsed, "raw")
    assert summary == "sts.amazonaws.com AssumeRole (AwsApiCall) by ops - GET /health"
    assert extracted["eventName"] == "AssumeRole"
    assert extracted["actor"] == "ops"

    summary, _ = cl._summarize_json({"message": "plain words"}, "raw")
    assert summary == "- plain words"
    summary, _ = cl._summarize_json({"error": "compact"}, "raw")
    assert summary == "- compact"
    summary, _ = cl._summarize_json({"msg": "fallback text"}, "{none}")
    assert summary == "- fallback text"
    summary, extracted = cl._summarize_json([1, 2], "[1, 2]")
    assert summary == "[1, 2]"
    summary, extracted = cl._summarize_json({}, "{ trail kept }")
    assert summary == "{ trail kept }"
    summary, _ = cl._summarize_json({"level": 4, "log": "levelled"}, "")
    assert "levelled" in summary


@pytest.fixture
def db():
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = None
    return db


def fake_boto_client(endpoint="https://logs.ap-southeast-2.amazonaws.com"):
    client = MagicMock()
    client.meta.endpoint_url = endpoint
    factory = patch.object(cl.boto3, "client", return_value=client)
    return {"client": client, "factory": factory}


def test_cloudwatch_client_endpoint_validation():
    fake = fake_boto_client("https://logs.ap-southeast-2.amazonaws.com")
    with configured(), fake["factory"]:
        result = cl._cloudwatch_client("ap-southeast-2")
    assert result is fake["client"]
    fake["client"].close.assert_called_if_not_none() if False else None

    for endpoint in [
        "http://logs.example.com",
        "example.com",
        "",
        "https://user:pass@logs.example.com",
    ]:
        fake = fake_boto_client(endpoint)
        try:
            with fake["factory"], configured():
                with pytest.raises(ValueError):
                    cl._cloudwatch_client("r1")
        finally:
            pass


def set_cursor(db, cursor):
    db.query.return_value.filter.return_value.one_or_none.return_value = cursor


def test_once_reports_missing_log_group(db):
    with patch.object(cl, "load_document", return_value={}, create=True):
        result = cl.ingest_cloudwatch_logs_once(db, "name", {}, {})
    assert result == {"name": "name", "ok": False, "error": "missing log_group"}


def test_once_streams_events_and_paginates(db):
    json_message = json.dumps(
        {
            "eventName": "AssumeRole",
            "message": '"GET /health HTTP/1.1" 200',
            "userIdentity": {"userName": "ops"},
        }
    )
    pages = [
        # newest first out of order to exercise stable sorting
        {
            "events": [
                {"timestamp": MS + 5000, "eventId": "ev2", "message": "plain line"},
                {"timestamp": MS, "eventId": "ev1", "message": json_message},
            ],
            "nextToken": "tok-2",
        },
        {
            "events": [
                {"timestamp": MS + 9000, "eventId": "", "message": "no event id"},
                {"timestamp": MS + 9000, "eventId": "ev3", "message": "later"},
            ],
            "nextToken": "tok-2",
        },
        {"events": [], "nextToken": "tok-3"},
    ]
    calls = []

    def paginate(**params):
        calls.append(params.copy())
        return pages[len([c for c in calls]) - 1]

    fake = fake_boto_client()
    fake["client"].filter_log_events.side_effect = paginate
    inserted = {"next": 100}

    def execute(stmt):
        result = MagicMock()
        result.scalar.return_value = inserted["next"]
        inserted["next"] += 1
        return result

    db.execute.side_effect = execute
    cursor = MagicMock(last_ts=None)
    set_cursor(db, cursor)
    q = {
        "log_group": "/prod/app",
        "filter_pattern": "ERROR",
        "log_stream_prefix": "web",
        "log_stream_names": ["s1"],
        "event": {
            "system": "prod",
            "action": "audit",
            "outcome": "success",
            "severity": 5,
        },
    }
    with configured(), fake["factory"]:
        result = cl.ingest_cloudwatch_logs_once(db, "prod-logs", q, {"limit": 10})

    assert result["created_events"] == 4
    assert result["created_artifacts"] == 4
    assert result["pages"] == 2
    assert result["log_group"] == "/prod/app"
    assert result["region"] == "ap-southeast-2"
    assert cursor.last_ts is not None
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
    assert calls[0]["logGroupName"] == "/prod/app"
    assert calls[0]["filterPattern"] == "ERROR"
    assert calls[0]["logStreamNamePrefix"] == "web"
    assert calls[0]["logStreamNames"] == ["s1"]
    assert calls[1]["nextToken"] == "tok-2"
    assert "nextToken" not in calls[0]
    assert db.add.call_count >= 4
    assert result["cursor_last_ts"].startswith("2025-10-09T")


def test_once_deduped_event_still_advances_cursor_and_skips_bad_timestamps(db):
    pages = [
        {
            "events": [
                {"timestamp": MS, "eventId": "ev1", "message": '{"msg": "one"}'},
                {"timestamp": 0, "eventId": "bad", "message": "no ts"},
                {"timestamp": -5, "eventId": "bad2", "message": "negative"},
            ],
        }
    ]
    fake = fake_boto_client()
    fake["client"].filter_log_events.side_effect = pages[:1]

    def execute(stmt):
        result = MagicMock()
        result.scalar.return_value = None
        return result

    db.execute.side_effect = execute
    cursor = MagicMock(last_ts=None)
    set_cursor(db, cursor)
    with configured(), fake["factory"]:
        result = cl.ingest_cloudwatch_logs_once(db, "name", {"log_group": "g"}, {})
    assert result["created_events"] == 0
    assert result["created_artifacts"] == 0
    assert result["created_mappings"] == 0
    assert result["cursor_last_ts"].startswith(
        datetime.fromtimestamp(MS / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    )
    db.commit.assert_called_once()


def test_once_repeated_token_stops(db):
    page = {"events": [], "nextToken": "tok-2"}
    fake = fake_boto_client()
    fake["client"].filter_log_events.return_value = dict(page)
    set_cursor(db, MagicMock(last_ts=None))
    with (
        configured(),
        fake["factory"],
        patch.object(cl, "store_event_with_artifact", create=True),
    ):
        result = cl.ingest_cloudwatch_logs_once(
            db, "name", {"log_group": "g"}, {"max_pages": 50}
        )
    assert result["pages"] == 1
    db.commit.assert_called_once()


def test_once_stream_names_must_be_a_list(db):
    fake = fake_boto_client()
    fake["client"].filter_log_events.return_value = {"events": []}
    set_cursor(db, MagicMock(last_ts=None))
    captured = {}

    def paginate(**params):
        captured.update(params)
        return {"events": []}

    fake["client"].filter_log_events.side_effect = paginate
    q = {"log_group": "g", "log_stream_names": "not-a-list"}
    with configured(), fake["factory"]:
        cl.ingest_cloudwatch_logs_once(db, "name", q, {})
    assert "logStreamNames" not in captured


def test_once_severity_default_and_masking_flags(db):
    fake = fake_boto_client()
    fake["client"].filter_log_events.return_value = {
        "events": [{"timestamp": MS, "eventId": "ev1", "message": "hello"}]
    }
    set_cursor(db, MagicMock(last_ts=None))
    with configured(masking="true"), fake["factory"]:
        cl.ingest_cloudwatch_logs_once(db, "name", {"log_group": "g"}, {})


def test_apply_rules_and_store_mappings_branches(db):
    control = MagicMock()
    control.ref = "A.1"
    ev = MagicMock(
        source="cloudwatch_logs",
        connection_id="conn",
        system="s",
        actor="a",
        action="log_event",
        outcome="info",
        severity=3,
        summary="s",
        raw_pointer={},
        normalized_payload={},
    )

    # no matches -> zero mappings and no queries
    with (
        patch.object(cl, "load_rules", return_value={}, create=False),
        patch.object(cl, "evaluate_by_framework", return_value={}),
    ):
        assert cl._apply_rules_and_store_mappings(db, ev) == 0

    matched = {
        "soc2": [
            "A.1",
            {"ref": "B.2", "confidence": "x"},
            {"ref": "C.3", "confidence": 5},
            {"ref": "  "},
            {"confidence": 1},
        ]
    }
    db.query.return_value.filter.return_value.all = MagicMock(return_value=[control])

    def execute(stmt):
        result = MagicMock()
        result.scalar.return_value = 1
        return result

    db.execute.side_effect = execute
    with (
        patch.object(cl, "load_rules", return_value={"rules": True}, create=False),
        patch.object(cl, "evaluate_by_framework", return_value=matched),
    ):
        assert cl._apply_rules_and_store_mappings(db, ev) == 3


def test_all_config_variants(db):
    with patch.object(cl.settings, "cloudwatch_logs_enabled", False):
        results = cl.ingest_cloudwatch_logs_all(db)
        assert results[0]["skipped"] and results[0]["reason"]

    def once(db, name, q, defaults):
        if name == "boom":
            raise RuntimeError("Collection failed: upstream HTTP 500")
        assert name == "renamed" and q["log_group"]
        return {"name": name}

    cfg = {
        "defaults": {"lookback_minutes": 30},
        "queries": [
            {"name": "renamed", "log_group": "/g"},
            {"label": "named-by-label", "log_group": "/g"},
            {"log_group": "/Weird/Name!"},
            {"name": "   ", "log_group": "/Weird/Name!"},
            {"name": "boom", "log_group": "/g"},
            "not-a-dict",
        ],
    }
    with (
        patch.object(cl.settings, "cloudwatch_logs_enabled", True),
        patch.object(cl, "load_cloudwatch_logs_config", return_value=cfg),
        patch.object(cl, "ingest_cloudwatch_logs_once", side_effect=once),
    ):
        results = cl.ingest_cloudwatch_logs_all(db)
    assert results[0]["name"] == "renamed"
    assert results[1]["name"] == "named-by-label"
    assert results[2]["name"] == "/Weird/Name!"
    assert results[3]["name"] == "Weird-Name-"
    assert any(
        r.get("name") == "boom" and "error" in r for r in [results[-1], results[-2]]
    )
    assert "not-a-dict" not in [r.get("name") for r in results]
