"""Loki collection: the query window, cursor, stored evidence and failures.

The Loki server is faked at the HTTP transport, so Keen's own client sends each
request with its configured authentication. Events go through the shared
event storage path into PostgreSQL, with artifacts in a local test bucket.
"""

import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy
import yaml
from app.core import managed_configuration
from app.core.config import settings
from app.db.models import (
    Artifact,
    ControlItem,
    Event,
    Framework,
    IngestionCursor,
    Mapping,
    SourceIngestionState,
)
from app.ingest import common, log_pipeline, loki
from app.ingest.connections import connection_scope, connections
from app.storage.s3 import get_object_stream, iter_stream, parse_s3_uri
from sqlalchemy.orm import sessionmaker

FW = "fw-loki"
NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
LOKI = "https://93.184.216.34"


def _ns(moment):
    return (moment - EPOCH) // timedelta(microseconds=1) * 1000


def _line(moment, text):
    return [str(_ns(moment)), text]


class Upstream:
    """Answers each request with the next queued reply: streams or a response."""

    def __init__(self):
        self.requests = []
        self.replies = []

    def __call__(self, request):
        self.requests.append(request)
        reply = self.replies.pop(0) if self.replies else []
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "streams", "result": reply},
            },
        )

    def window(self, index=-1):
        params = self.requests[index].url.params
        return int(params["start"]), int(params["end"])


@pytest.fixture
def lk(contract_db, monkeypatch, tmp_path):
    if contract_db.get_bind().dialect.name != "postgresql":
        pytest.skip("Loki collection contracts run against PostgreSQL")
    clock = SimpleNamespace(now=NOW)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now if tz else clock.now.replace(tzinfo=None)

    monkeypatch.setattr(loki, "datetime", Clock)
    # Collection reads its query document through its own short session.
    monkeypatch.setattr(
        managed_configuration,
        "SessionLocal",
        sessionmaker(
            bind=contract_db.get_bind(), join_transaction_mode="create_savepoint"
        ),
    )
    upstream = Upstream()
    real_client = httpx.Client
    # Keen's ingestion client keeps its headers, auth, timeout and redirect
    # policy; only the network transport is replaced.
    monkeypatch.setattr(
        loki.httpx,
        "Client",
        lambda **kw: real_client(
            **{k: v for k, v in kw.items() if k != "transport"},
            transport=httpx.MockTransport(upstream),
        ),
    )
    queries = tmp_path / "loki.yml"
    rules = tmp_path / "rules.yml"
    rules.write_text(
        yaml.safe_dump(
            {
                "rules": [
                    {
                        "id": "ssh-logins",
                        "when": {"source": "loki", "action": "login"},
                        "map_to": [
                            {"framework": FW, "ref": "A.8.5", "kind": "control"}
                        ],
                    }
                ]
            }
        )
    )
    for name, value in {
        "loki_enabled": True,
        "loki_base_url": LOKI,
        "loki_username": "reader",
        "loki_password": "synthetic-loki-password",
        "loki_header_name": "X-Scope-OrgID",
        "loki_header_value": "tenant-a",
        "loki_queries_path": str(queries),
        "loki_initial_lookback_minutes": 15,
        "loki_max_query_range_hours": 720,
        "loki_catchup_chunks_per_run": 1,
        "loki_overlong_range_strategy": "chunk",
        "rules_path": str(rules),
        "artifact_storage_backend": "local",
        "artifact_local_dir": str(tmp_path / "artifacts"),
        "event_data_masking": "false",
        "demo_mode": False,
    }.items():
        monkeypatch.setattr(settings, name, value)
    contract_db.add(Framework(slug=FW))
    control = ControlItem(
        framework_slug=FW, type="annex_control", ref="A.8.5", title="Auth"
    )
    contract_db.add(control)
    contract_db.commit()

    def configure(*query_list, defaults=None):
        document = {"queries": list(query_list)}
        if defaults is not None:
            document["defaults"] = defaults
        queries.write_text(yaml.safe_dump(document))

    def query(name="ssh", logql='{app="sshd"}', **extra):
        return {
            "name": name,
            "logql": logql,
            "event": {
                "system": "bastion",
                "action": "login",
                "outcome": "success",
                "severity": 4,
            },
            **extra,
        }

    def run():
        contract_db.expire_all()
        return loki.ingest_loki_all(contract_db)

    def events():
        contract_db.expire_all()
        return contract_db.query(Event).order_by(Event.timestamp).all()

    def cursor():
        contract_db.expire_all()
        (row,) = contract_db.query(IngestionCursor).all()
        return row

    def cursor_name(query_name="ssh"):
        # Cursors belong to the connection the run uses, here the environment's.
        (environment,) = connections(None, "loki")
        with connection_scope(environment):
            return loki.namespace(f"loki:{query_name}")

    def stored(artifact):
        bucket, key = parse_s3_uri(artifact.storage_uri)
        return b"".join(iter_stream(get_object_stream(bucket, key)["Body"]))

    configure(query())
    return SimpleNamespace(
        db=contract_db,
        clock=clock,
        upstream=upstream,
        control=control,
        configure=configure,
        query=query,
        run=run,
        events=events,
        cursor=cursor,
        cursor_name=cursor_name,
        stored=stored,
    )


OSSEC = {
    "rule": {"sidid": 5715, "comment": "SSHD authentication success.", "level": 3},
    "agent_name": "web-1",
    "srcuser": "root",
    "full_log": "Accepted publickey for root from 192.0.2.10 port 22 ssh2",
}
ACCESS = {
    "rule": {"id": 31108, "description": "Web request"},
    "hostname": "web-2",
    "full_log": '203.0.113.5 - - [01/May/2026] "GET /admin/login?next=/secret HTTP/1.1" 200 5',
}


def test_a_run_reads_the_window_since_the_cursor_and_stores_each_line(lk):
    lk.upstream.replies.append(
        [
            {
                "stream": {"app": "sshd", "host": "bastion-1"},
                "values": [
                    _line(NOW - timedelta(minutes=9), json.dumps(OSSEC)),
                    _line(NOW - timedelta(minutes=7), json.dumps(ACCESS)),
                    _line(
                        NOW - timedelta(minutes=5), "password=hunter2 failed for bob"
                    ),
                ],
            }
        ]
    )

    (result,) = lk.run()

    assert (
        result["created_events"],
        result["created_artifacts"],
        result["created_mappings"],
    ) == (3, 3, 3)
    (sent,) = lk.upstream.requests
    assert (sent.method, str(sent.url.copy_with(query=None))) == (
        "GET",
        f"{LOKI}/loki/api/v1/query_range",
    )
    params = dict(sent.url.params)
    assert (params["query"], params["limit"], params["direction"]) == (
        '{app="sshd"}',
        "5000",
        "forward",
    )
    assert "step" not in params
    start, end = lk.upstream.window()
    # A new cursor looks back the configured fifteen minutes.
    assert abs(start - _ns(NOW - timedelta(minutes=15))) < 1000
    assert abs(end - _ns(NOW)) < 1000
    credentials = base64.b64encode(b"reader:synthetic-loki-password").decode()
    assert sent.headers["authorization"] == f"Basic {credentials}"
    assert sent.headers["x-scope-orgid"] == "tenant-a"

    ossec, access, plain = lk.events()
    assert ossec.summary == (
        "[sid:5715] SSHD authentication success. @ web-1 user=root - "
        "Accepted publickey for root from 192.0.2.10 port 22 ssh2"
    )
    assert ossec.actor == "root"
    assert ossec.normalized_payload["extracted"] == {
        "sid": 5715,
        "rule_comment": "SSHD authentication success.",
        "rule_level": 3,
        "agent": "web-1",
        "user": "root",
    }
    # Access-log requests are shortened to the method and path.
    assert access.summary == "[sid:31108] Web request @ web-2 - GET /admin/login"
    assert access.actor is None
    assert "hunter2" not in plain.summary and plain.summary.startswith("password=")
    for event in (ossec, access, plain):
        assert (
            event.source,
            event.system,
            event.action,
            event.outcome,
            event.severity,
        ) == (
            "loki",
            "bastion",
            "login",
            "success",
            4,
        )
        assert event.raw_pointer["loki"]["labels"] == {
            "app": "sshd",
            "host": "bastion-1",
        }
        assert event.raw_pointer["loki"]["query_name"] == "ssh"
    assert ossec.timestamp.replace(tzinfo=timezone.utc) == NOW - timedelta(minutes=9)

    artifacts = {a.event_id: a for a in lk.db.query(Artifact)}
    assert artifacts[ossec.id].content_type == "application/json"
    assert json.loads(lk.stored(artifacts[ossec.id])) == OSSEC
    assert artifacts[plain.id].content_type == "text/plain; charset=utf-8"
    assert b"hunter2" not in lk.stored(artifacts[plain.id])
    assert artifacts[plain.id].redaction_status == "redacted"
    assert all(
        a.kind == "log_line" and a.captured_by == "keen:loki"
        for a in artifacts.values()
    )
    mappings = lk.db.query(Mapping).all()
    assert {(m.event_id, m.control_item_id, m.method) for m in mappings} == {
        (event.id, lk.control.id, "rule") for event in (ossec, access, plain)
    }
    assert lk.cursor().last_ts.replace(tzinfo=timezone.utc) == NOW


@pytest.mark.parametrize(
    "line,summary",
    [
        ('{"message": "  disk   full  "}', "disk full"),
        (
            json.dumps({"full_log": '"POST /api/upload?id=1 HTTP/2" 413'}),
            "POST /api/upload",
        ),
        ('{"text": "nightly backup finished"}', "nightly backup finished"),
        ('{"level": "info", "msg": ""}', '{"level": "info", "msg": ""}'),
        ("[1, 2]", "[1, 2]"),
        ("{not json", "{not json"),
        ("x" * 400, "x" * 339 + "…"),
    ],
    ids=[
        "message-field",
        "request-only",
        "text-field",
        "nothing-to-summarise",
        "json-list",
        "broken-json",
        "long-line",
    ],
)
def test_lines_without_rule_details_are_summarised_from_what_they_hold(
    lk, line, summary
):
    lk.upstream.replies.append(
        [
            {
                "stream": {"app": "disk"},
                "values": [_line(NOW - timedelta(minutes=2), line)],
            }
        ]
    )

    lk.run()

    (event,) = lk.events()
    assert event.summary == summary
    assert event.actor is None


def test_the_next_run_continues_after_the_cursor_and_skips_lines_already_stored(lk):
    first = [
        {
            "stream": {"app": "sshd"},
            "values": [_line(NOW - timedelta(minutes=3), "session opened for alice")],
        }
    ]
    lk.upstream.replies.extend([first, first])
    lk.run()

    lk.clock.now = NOW + timedelta(minutes=5)
    (result,) = lk.run()

    assert result["created_events"] == 0
    assert len(lk.events()) == 1
    start, end = lk.upstream.window()
    assert abs(start - (_ns(NOW) + 1000)) < 1000
    assert abs(end - _ns(NOW + timedelta(minutes=5))) < 1000
    assert lk.cursor().last_ts.replace(tzinfo=timezone.utc) == NOW + timedelta(
        minutes=5
    )


def test_a_full_page_moves_the_cursor_only_to_the_newest_line_seen(lk):
    lk.configure(lk.query(limit=2))
    newest = NOW - timedelta(minutes=4)
    lk.upstream.replies.append(
        [
            {
                "stream": {"app": "sshd"},
                "values": [
                    _line(NOW - timedelta(minutes=6), "one"),
                    _line(newest, "two"),
                ],
            }
        ]
    )

    (result,) = lk.run()

    assert result["chunks"][0]["hit_limit"] is True
    assert dict(lk.upstream.requests[0].url.params)["limit"] == "2"
    # Lines after the newest one returned are read on the next run.
    assert lk.cursor().last_ts.replace(tzinfo=timezone.utc) == newest
    lk.clock.now = NOW + timedelta(minutes=1)
    lk.run()
    start, _ = lk.upstream.window()
    assert abs(start - (_ns(newest) + 1000)) < 1000


def test_a_long_gap_is_read_in_bounded_chunks(lk):
    lk.configure(lk.query(max_query_range_hours=4, catchup_chunks_per_run=2))
    behind = NOW - timedelta(hours=10)
    lk.db.add(IngestionCursor(name=lk.cursor_name(), last_ts=behind, meta={}))
    lk.db.commit()

    (result,) = lk.run()

    windows = [lk.upstream.window(i) for i in range(len(lk.upstream.requests))]
    assert len(windows) == 2
    assert abs(windows[0][0] - (_ns(behind) + 1000)) < 1000
    assert abs(windows[0][1] - _ns(behind + timedelta(hours=4))) < 2000
    assert abs(windows[1][0] - (_ns(behind + timedelta(hours=4)) + 1000)) < 2000
    assert abs(windows[1][1] - _ns(behind + timedelta(hours=8))) < 3000
    assert (
        result["caught_up"],
        result["chunks_remaining"],
        result["chunks_processed"],
    ) == (
        False,
        True,
        2,
    )
    assert abs(
        lk.cursor().last_ts.replace(tzinfo=timezone.utc) - (behind + timedelta(hours=8))
    ) < timedelta(microseconds=5)


def test_the_reset_strategy_skips_a_long_gap_without_querying(lk):
    lk.configure(lk.query(max_query_range_hours=4, overlong_range_strategy="RESET"))
    behind = NOW - timedelta(hours=10)
    lk.db.add(IngestionCursor(name=lk.cursor_name(), last_ts=behind, meta={}))
    lk.db.commit()

    (result,) = lk.run()

    assert lk.upstream.requests == []
    assert (result["skipped"], result["strategy"]) == (True, "reset")
    cursor = lk.cursor()
    assert cursor.last_ts.replace(tzinfo=timezone.utc) == NOW
    assert cursor.meta["last_reset_previous_cursor"] == behind.isoformat()
    assert cursor.meta["last_reset_max_query_range_hours"] == 4


def test_query_settings_override_defaults_and_invalid_values_fall_back(lk):
    lk.configure(
        lk.query(direction="BACKWARD", step_seconds=30),
        lk.query(name="other", logql='{app="web"}', limit=-5, direction="sideways"),
        defaults={"limit": 250, "direction": "backward"},
    )

    lk.run()

    first, second = (dict(r.url.params) for r in lk.upstream.requests)
    assert (first["limit"], first["direction"], first["step"]) == (
        "250",
        "backward",
        "30",
    )
    # An invalid limit uses the built-in default; an unknown direction reads forward.
    assert (second["query"], second["limit"], second["direction"]) == (
        '{app="web"}',
        "5000",
        "forward",
    )
    assert "step" not in second


@pytest.mark.parametrize(
    "response,error",
    [
        (
            httpx.Response(500, text="stack trace with internal detail"),
            "Collection failed: upstream returned HTTP 500",
        ),
        (
            httpx.Response(302, headers={"Location": "https://elsewhere.example/"}),
            "Collection failed: upstream returned HTTP 302",
        ),
    ],
    ids=["server-error", "redirect"],
)
def test_a_failed_query_reports_a_bounded_error_and_keeps_the_cursor(
    lk, response, error
):
    lk.upstream.replies.append(response)

    (result,) = lk.run()

    assert result["error"] == error
    assert "internal detail" not in json.dumps(result)
    # A redirect is reported, never followed.
    assert [str(r.url.copy_with(query=None)) for r in lk.upstream.requests] == [
        f"{LOKI}/loki/api/v1/query_range"
    ]
    assert lk.events() == [] and lk.db.query(IngestionCursor).count() == 0


def test_one_failing_query_does_not_stop_the_next(lk):
    lk.configure(lk.query(name="broken", logql="{"), lk.query(name="ssh"))
    lk.upstream.replies.extend(
        [
            httpx.Response(400, text="parse error"),
            [
                {
                    "stream": {"app": "sshd"},
                    "values": [_line(NOW - timedelta(minutes=1), "ok")],
                }
            ],
        ]
    )

    broken, ssh = lk.run()

    assert broken == {
        "query_name": "broken",
        "error": "Collection failed: upstream returned HTTP 400",
        "connection_id": "env:loki",
        "connection_name": "Environment default",
    }
    assert ssh["created_events"] == 1
    assert [e.summary for e in lk.events()] == ["ok"]


def test_an_unsafe_loki_address_is_refused_before_any_request(lk, monkeypatch):
    monkeypatch.setattr(settings, "loki_base_url", "https://127.0.0.1:3100")

    (result,) = lk.run()

    assert result["error"] == (
        "Collection failed; check the input, credentials, upstream service and "
        "storage configuration"
    )
    assert lk.upstream.requests == []


def test_disabled_or_paused_loki_collects_nothing(lk, monkeypatch):
    lk.db.add(SourceIngestionState(source="loki", paused=True))
    lk.db.commit()
    (paused,) = lk.run()
    assert (paused["paused"], paused["reason"]) == (
        True,
        "Ingestion paused by an administrator",
    )

    monkeypatch.setattr(settings, "loki_enabled", False)
    lk.db.query(SourceIngestionState).delete()
    lk.db.commit()
    (disabled,) = lk.run()

    assert disabled["skipped"] is True
    assert lk.upstream.requests == [] and lk.events() == []


def test_naive_timestamps_are_read_as_utc_for_loki_windows():
    # Cursor timestamps stored without a zone are UTC, so the nanosecond window
    # sent to Loki is the same whether or not the zone is attached.
    naive = datetime(2026, 5, 1, 12, 0, 0)
    assert loki._to_ns(naive) == loki._to_ns(naive.replace(tzinfo=timezone.utc))
    assert loki._to_ns(naive) == 1777636800 * 1_000_000_000


def test_blank_and_non_json_lines_are_never_parsed_as_structured():
    assert log_pipeline.try_parse_json_line("") is None
    assert log_pipeline.try_parse_json_line("   \n") is None
    assert log_pipeline.try_parse_json_line("plain text {not json}") is None
    assert log_pipeline.try_parse_json_line(' {"a": 1} ') == {"a": 1}


def test_unusable_numeric_settings_fall_back_to_the_configured_default():
    # Text, None and negative values fall back; the minimum is enforced even
    # when the fallback itself is below it.
    assert loki._positive_int("lots", 5000) == 5000
    assert loki._positive_int(None, 7, minimum=1) == 7
    assert loki._positive_int(-3, 0, minimum=1) == 1
    assert loki._positive_int("12", 5000) == 12


def test_long_labels_are_bounded_to_the_event_columns(lk):
    # Log lines go through the same storage path as every other collector, so
    # an oversized system label or extracted user is bounded, not rejected.
    lk.configure(
        lk.query(
            event={
                "system": "s" * 300,
                "action": "login",
                "outcome": "success",
                "severity": 4,
            }
        )
    )
    line = json.dumps({**OSSEC, "srcuser": "u" * 300})
    lk.upstream.replies.append(
        [
            {
                "stream": {"app": "sshd"},
                "values": [_line(NOW - timedelta(minutes=5), line)],
            }
        ]
    )

    (result,) = lk.run()

    assert result["created_events"] == 1
    (event,) = lk.events()
    assert len(event.system) == Event.__table__.c.system.type.length
    assert len(event.actor) == Event.__table__.c.actor.type.length


def test_a_retry_after_a_failed_chunk_stores_every_line(lk, monkeypatch):
    # A chunk commits once, with its cursor. When it fails after storing one
    # line's artifact, the rollback leaves that object behind; stored objects
    # are never replaced, so the retry must not reuse its key.
    lines = [
        _line(NOW - timedelta(minutes=9), json.dumps(OSSEC)),
        _line(NOW - timedelta(minutes=7), "second line"),
    ]
    stream = [{"stream": {"app": "sshd"}, "values": lines}]
    real_rules = common.apply_rules
    calls = []

    def failing_second_line(db, event):
        calls.append(event)
        if len(calls) == 2:
            raise RuntimeError("synthetic failure")
        return real_rules(db, event)

    monkeypatch.setattr(common, "apply_rules", failing_second_line)
    lk.upstream.replies.append(stream)
    (first,) = lk.run()
    assert "error" in first
    assert lk.events() == []
    # Both objects the failed attempt stored are queued for the retention worker.
    queued = set(
        lk.db.execute(
            sqlalchemy.text("SELECT storage_uri FROM evidence_object_cleanup")
        ).scalars()
    )
    assert sorted(uri.rsplit(".", 1)[1] for uri in queued) == ["json", "log"]
    assert all("/loki/ssh/" in uri for uri in queued)

    monkeypatch.setattr(common, "apply_rules", real_rules)
    lk.upstream.replies.append(stream)
    (second,) = lk.run()
    assert "error" not in second, second
    assert second["created_events"] == 2
    assert len(lk.events()) == 2
    # The retry stored fresh objects; the failed attempt's objects stay queued.
    stored = {
        artifact.storage_uri for event in lk.events() for artifact in event.artifacts
    }
    assert len(stored) == 2 and not stored & queued
