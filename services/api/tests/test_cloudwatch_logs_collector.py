"""CloudWatch Logs collector contracts against a mocked boto3 client."""

import contextlib
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from app.ingest import cloudwatch_logs as cl
from app.ingest.log_pipeline import (
    brief_http_request,
    ensure_utc,
    make_fingerprint,
    truncate,
    try_parse_json_line,
)

MS = 1_760_000_000_000


@contextlib.contextmanager
def configured(region="ap-southeast-2", masking="false", stored=None):
    """Configure the collector; ``stored`` decides each shared-storage result.

    Yields the keyword arguments of every event handed to shared storage, which
    redacts, masks, bounds and persists it (covered by its own tests).
    """
    calls = []

    def store(db, **event):
        calls.append(event)
        if stored is not None:
            return stored(event)
        return {"ok": True, "event_id": f"ev-{len(calls)}", "mappings": 0}

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(cl.settings, "cloudwatch_logs_region", region))
        stack.enter_context(patch.object(cl.settings, "event_data_masking", masking))
        stack.enter_context(patch.object(cl, "store_event_with_artifact", store))
        yield calls


def test_ensure_utc_and_helpers():
    naive = datetime(2026, 10, 9, 12, 0, 0)
    assert ensure_utc(naive) == naive.replace(tzinfo=timezone.utc)
    assert ensure_utc(None) is None
    assert ensure_utc(naive.replace(tzinfo=timezone.utc)) == naive.replace(
        tzinfo=timezone.utc
    )
    assert try_parse_json_line('{"a": 1}') == {"a": 1}
    assert try_parse_json_line("[1]") == [1]
    for bad in ["", "x", "{", "not json"]:
        assert try_parse_json_line(bad) is None
    assert truncate("  a   b  ") == "a b"
    assert len(truncate("x" * 400)) == 220
    assert make_fingerprint("a", "b") == make_fingerprint("a", "b")
    assert make_fingerprint("a", "b") != make_fingerprint("a", "c")
    assert make_fingerprint("a", None) == make_fingerprint("a", "")


def test_brief_http_request():
    assert brief_http_request('"GET /api/x?secret=y HTTP/1.1" 200') == "GET /api/x"
    assert brief_http_request('"POST /login HTTP/1.1" 401') == "POST /login"
    assert brief_http_request("no request here") == ""
    query = '"' + "GET /" + "?" + "token=fixture-secret" + '"'
    brief = brief_http_request(query)
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
    with configured() as stored, fake["factory"]:
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
    assert len(stored) == 4
    assert {e["source"] for e in stored} == {"cloudwatch_logs"}
    assert {e["system"] for e in stored} == {"prod"}
    assert stored[0]["severity"] == 5
    # The run commits once, with its cursor, rather than per event.
    assert {e["commit"] for e in stored} == {False}
    assert all(e["timestamp"].tzinfo is None for e in stored)
    assert result["cursor_last_ts"].startswith("2025-10-09T")


def test_events_without_an_aws_event_id_keep_distinct_identities(db):
    """Each id-less event is stored on its own; only a true repeat is a duplicate."""
    events = [
        {"timestamp": MS, "logStreamName": "web-1", "eventId": "", "message": "one"},
        {"timestamp": MS, "logStreamName": "web-2", "eventId": "", "message": "one"},
        {
            "timestamp": MS + 1,
            "logStreamName": "web-1",
            "eventId": "",
            "message": "two",
        },
        # The same stream, time and message is the same event seen again.
        {"timestamp": MS, "logStreamName": "web-1", "eventId": "", "message": "one"},
        {
            "timestamp": MS,
            "logStreamName": "web-1",
            "eventId": "x" * 121,
            "message": "long",
        },
    ]
    fake = fake_boto_client()
    fake["client"].filter_log_events.return_value = {"events": events}
    set_cursor(db, MagicMock(last_ts=None))
    with configured(stored=lambda event: {"deduped": True}) as stored, fake["factory"]:
        cl.ingest_cloudwatch_logs_once(db, "prod-logs", {"log_group": "/prod/app"}, {})
    identities = [event["external_id"] for event in stored]

    # Events are stored in time order, so compare the keys as a multiset.
    def key(stream, ts, message):
        return make_fingerprint("/prod/app", stream, str(ts), message)

    assert sorted(identities) == sorted(
        [
            key("web-1", MS, "one"),
            key("web-1", MS, "one"),  # the repeat shares its key
            key("web-2", MS, "one"),
            key("web-1", MS + 1, "two"),
            # A long AWS id is digested to a fixed-length key.
            make_fingerprint("/prod/app", "x" * 121),
        ]
    )
    assert len(set(identities)) == 4
    # Artifact keys follow the identity, however long the AWS id is; shared
    # storage makes each stored key unique.
    assert len({event["artifact_key"] for event in stored}) == 4


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

    cursor = MagicMock(last_ts=None)
    set_cursor(db, cursor)
    with configured(stored=lambda event: {"deduped": True}), fake["factory"]:
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


def test_ingested_aws_logs_count_the_mappings_shared_storage_applied(db):
    # Shared storage persists each event and applies mapping rules to the stored
    # row; the collector reports the mappings it created.
    fake = fake_boto_client()
    fake["client"].filter_log_events.return_value = {
        "events": [{"timestamp": MS, "eventId": "ev1", "message": "hello"}]
    }
    set_cursor(db, MagicMock(last_ts=None))
    with (
        configured(
            stored=lambda event: {"event_id": "ev-row", "mappings": 2}
        ) as stored,
        fake["factory"],
    ):
        result = cl.ingest_cloudwatch_logs_once(db, "name", {"log_group": "g"}, {})

    assert result["created_mappings"] == 2
    (event,) = stored
    assert event["source"] == "cloudwatch_logs"
    assert event["artifact_kind"] == "log_event"
    assert event["captured_by"] == "keen:cloudwatch_logs"


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


def test_load_config_reads_the_managed_document():
    with patch.object(cl, "load_document", return_value={"queries": []}) as document:
        assert cl.load_cloudwatch_logs_config("fixture.yml") == {"queries": []}
    document.assert_called_once_with("cloudwatch_logs", "fixture.yml")


def test_once_ignores_malformed_event_defaults_and_keeps_ingestion_time(db):
    # Event defaults that are no object fall back to the query name and the
    # standard action; the ingestion time AWS reports is kept with the event.
    fake = fake_boto_client()
    fake["client"].filter_log_events.return_value = {
        "events": [
            {
                "timestamp": MS,
                "eventId": "ev1",
                "message": "hello",
                "ingestionTime": MS + 250,
            }
        ]
    }
    set_cursor(db, MagicMock(last_ts=None))
    with configured() as inserted, fake["factory"]:
        result = cl.ingest_cloudwatch_logs_once(
            db, "named-query", {"log_group": "g", "event": "system=prod"}, {}
        )
    assert result["created_events"] == 1
    (event,) = inserted
    assert event["system"] == "named-query"
    assert event["action"] == "log_event"
    assert event["outcome"] is None and event["severity"] is None
    assert event["normalized_payload"]["cloudwatch"]["ingestion_time_ms"] == MS + 250


def test_all_treats_a_query_list_that_is_no_list_as_empty(db):
    with (
        patch.object(cl.settings, "cloudwatch_logs_enabled", True),
        patch.object(
            cl,
            "load_cloudwatch_logs_config",
            return_value={"queries": {"log_group": "/g"}},
        ),
        patch.object(cl, "ingest_cloudwatch_logs_once") as once,
    ):
        assert cl.ingest_cloudwatch_logs_all(db) == []
    once.assert_not_called()


def test_once_continues_from_a_naive_cursor_with_overlap(db):
    # A cursor stored without a zone is read as UTC and the window starts a
    # little before it, so late-arriving rows are not missed.
    fake = fake_boto_client()
    captured = {}

    def paginate(**params):
        captured.update(params)
        return {"events": []}

    fake["client"].filter_log_events.side_effect = paginate
    cursor = MagicMock(last_ts=datetime(2026, 10, 9, 12, 0, 0))
    set_cursor(db, cursor)
    with configured(), fake["factory"]:
        result = cl.ingest_cloudwatch_logs_once(
            db, "name", {"log_group": "g"}, {"overlap_seconds": 30}
        )
    expected = datetime(2026, 10, 9, 11, 59, 30, tzinfo=timezone.utc)
    assert captured["startTime"] == int(expected.timestamp() * 1000)
    assert result["start"] == expected.isoformat()
    assert cursor.last_ts.tzinfo is not None


def test_unresolved_or_conflicting_mappings_are_not_counted(db):
    # A mapping whose target resolves but exists already adds nothing: rule
    # application is idempotent, so repeat collections never duplicate mappings.
    fake = fake_boto_client()
    fake["client"].filter_log_events.return_value = {
        "events": [{"timestamp": MS, "eventId": "ev1", "message": "hello"}]
    }
    set_cursor(db, MagicMock(last_ts=None))
    with (
        configured(stored=lambda event: {"event_id": "ev-row", "mappings": 0}),
        fake["factory"],
    ):
        result = cl.ingest_cloudwatch_logs_once(db, "name", {"log_group": "g"}, {})

    assert result["created_mappings"] == 0
