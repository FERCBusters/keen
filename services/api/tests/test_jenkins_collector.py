"""Jenkins collector contracts against a mock upstream REST API."""

import contextlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx2 as httpx
import pytest
from app.ingest import jenkins as jk
from mosp.core.datetime_utils import to_utc_naive
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.usefixtures("no_evidence_purge")


@contextlib.contextmanager
def serving(handler):
    def fake_client(**kwargs):
        captured["kwargs"] = kwargs
        return contextlib.nullcontext(
            __import__("httpx2").Client(
                transport=__import__("httpx2").MockTransport(handler),
                follow_redirects=False,
                base_url="https://jenkins.example",
            )
        )

    captured = {}
    stack = contextlib.ExitStack()
    stack.enter_context(patch.object(jk, "ingestion_client", fake_client))
    stack.enter_context(patch.object(jk, "is_safe_url", return_value=True))
    with stack:
        yield captured


def build(number, result="SUCCESS", timestamp=1_760_000_000_000):
    return {"number": number, "result": result, "timestamp": timestamp, "url": None}


@pytest.fixture
def db():
    session = MagicMock()
    session.query.return_value.filter.return_value.one_or_none.return_value = None
    return session


def test_job_api_path():
    assert jk._job_api_path("deploy") == "/job/deploy/api/json"


def test_timestamp_normalisation():
    naive = datetime(2026, 10, 9, 12)
    assert to_utc_naive(naive) is naive
    aware = datetime(2026, 10, 9, 23, tzinfo=timezone(timedelta(hours=11)))
    assert to_utc_naive(aware) == naive


def test_client_configuration_and_monitored_auth():
    with serving(
        lambda request: __import__("httpx2").Response(200, json={})
    ) as captured:
        with (
            patch.object(jk.settings, "jenkins_base_url", "https://jenkins.example"),
            patch.object(jk.settings, "jenkins_username", "ci"),
            patch.object(jk.settings, "jenkins_api_token", "fixture-secret"),
        ):
            jk._client()
            assert captured["kwargs"]["auth"] == ("ci", "fixture-secret")
        with (
            patch.object(jk.settings, "jenkins_base_url", "https://jenkins.example"),
            patch.object(jk.settings, "jenkins_username", "ci"),
            patch.object(jk.settings, "jenkins_api_token", ""),
        ):
            jk._client()
            assert captured["kwargs"]["auth"] is None
        with patch.object(jk, "is_safe_url", return_value=False):
            with patch.object(
                jk.settings, "jenkins_base_url", "https://jenkins.example"
            ):
                with pytest.raises(
                    ValueError, match="^Invalid or unsafe Jenkins base URL$"
                ):
                    jk._client()


def test_ingest_job_uses_cursor_and_classifies_outcomes(db):
    job = {
        "allBuilds": [
            build(3, "SUCCESS"),
            build(2, "UNSTABLE"),
            build(1, "FAILURE"),
            {"number": 4, "building": True, "timestamp": 1_760_000_003_000},
        ]
    }
    with serving(lambda request: __import__("httpx2").Response(200, json=job)):
        stored = []
        cursor = MagicMock(last_ts=None)
        with (
            patch.object(jk.settings, "jenkins_base_url", "https://jenkins.example"),
            patch.object(jk, "_get_or_create_cursor", return_value=cursor),
            patch.object(
                jk,
                "store_event_with_artifact",
                side_effect=lambda db, **kw: stored.append(kw) or {"deduped": False},
            ),
        ):
            result = jk.ingest_jenkins_job(db, "deploy", "prod", None)
    # Builds are processed oldest-first; a running build reports RUNNING/info.
    assert [s["outcome"] for s in stored] == ["info", "failed", "failed", "success"]
    assert [s["severity"] for s in stored] == [3, 4, 4, 3]
    assert result["created_events"] == 4
    assert result["cursor_last_ts"] == cursor.last_ts.isoformat()
    assert [s["summary"] for s in stored] == [
        "[prod] jenkins deploy build #4 RUNNING",
        "[prod] jenkins deploy build #1 FAILURE",
        "[prod] jenkins deploy build #2 UNSTABLE",
        "[prod] jenkins deploy build #3 SUCCESS",
    ]
    assert all(s["system"] == "prod" for s in stored)
    # One commit before the request, one once the results are stored.
    assert db.commit.call_count == 2


def test_a_build_with_no_timestamp_is_reported_not_passed_over(db):
    job = {
        "allBuilds": [
            build(2, "SUCCESS"),
            build(1, "SUCCESS", timestamp=None),
            {"number": 0, "building": False},
        ]
    }
    with serving(lambda request: __import__("httpx2").Response(200, json=job)):
        stored = []
        cursor = MagicMock(last_ts=None)
        with (
            patch.object(jk.settings, "jenkins_base_url", "https://jenkins.example"),
            patch.object(jk, "_get_or_create_cursor", return_value=cursor),
            patch.object(
                jk,
                "store_event_with_artifact",
                side_effect=lambda db, **kw: stored.append(kw) or {"deduped": False},
            ),
        ):
            result = jk.ingest_jenkins_job(db, "deploy", None, "release")
    assert [
        (s["raw_pointer"]["jenkins"]["build_number"], s["action"]) for s in stored
    ] == [(2, "release")]
    # The cursor moves past builds 1 and 0, which no later run reads, so the
    # run says that two builds it wanted could not be read.
    assert cursor.meta["last_number"] == 2
    assert (result["created_events"], result["invalid_records"]) == (1, 2)


def test_ingest_job_cursor_blocks_old_builds(db):
    job = {"allBuilds": [build(2, "SUCCESS"), build(1, "FAILURE")]}
    with serving(lambda request: __import__("httpx2").Response(200, json=job)):
        cursor = MagicMock(
            last_ts=datetime(2026, 10, 10, 0, 0, 0),
            isoformat=lambda: "2026-10-10T00:00:00",
        )
        with (
            patch.object(jk.settings, "jenkins_base_url", "https://jenkins.example"),
            patch.object(jk, "_get_or_create_cursor", return_value=cursor),
            patch.object(jk, "store_event_with_artifact") as store,
        ):
            result = jk.ingest_jenkins_job(db, "deploy", None, None)
    # A cursor kept by time stored builds before build numbers were kept: the
    # finished builds it lists are read once more and recognised as stored.
    assert [
        c.kwargs["raw_pointer"]["jenkins"]["build_number"]
        for c in store.mock_calls
        if c.kwargs
    ] == [1, 2]
    assert result["cursor_last_ts"] == "2026-10-10T00:00:00"
    # One commit before the request, one once the results are stored.
    assert db.commit.call_count == 2


def _builds(db, body):
    stored = []
    with serving(lambda request: httpx.Response(200, json=body)):
        with (
            patch.object(jk.settings, "jenkins_base_url", "https://jenkins.example"),
            patch.object(
                jk, "_get_or_create_cursor", return_value=MagicMock(last_ts=None)
            ),
            patch.object(
                jk,
                "store_event_with_artifact",
                side_effect=lambda db, **kw: stored.append(kw) or {"deduped": False},
            ),
        ):
            result = jk.ingest_jenkins_job(db, "deploy", None, None)
    return result, stored


def test_ingest_job_skips_builds_other_than_objects(db):
    body = {"allBuilds": [build(2), "build 1", 7, None, ["number", 1], build(0)]}
    result, stored = _builds(db, body)
    assert result["created_events"] == 2
    assert [s["raw_pointer"]["jenkins"]["build_number"] for s in stored] == [0, 2]
    # One commit before the request, one once the results are stored.
    assert db.commit.call_count == 2


@pytest.mark.parametrize(
    "body", [[build(1)], {"allBuilds": {"0": build(1)}}, {"allBuilds": "builds"}]
)
def test_ingest_job_reads_a_listing_of_another_shape_as_empty(db, body):
    result, stored = _builds(db, body)
    assert (result["created_events"], stored) == (0, [])
    # One commit before the request, one once the results are stored.
    assert db.commit.call_count == 2


def test_ingest_all_collections_and_error_paths(db):
    with patch.object(jk.settings, "jenkins_enabled", False):
        assert jk.ingest_jenkins_all(db) == [
            {"source": "jenkins", "skipped": True, "reason": "Source type is disabled"}
        ]

    def job(db, name, label, kind):
        if name == "boom":
            raise RuntimeError("Collection failed: upstream HTTP 404")
        return {"job": name, "created_events": 2}

    with (
        patch.object(jk.settings, "jenkins_enabled", True),
        patch.object(
            jk,
            "load_jenkins_config",
            return_value={
                "jobs": [
                    {"name": "deploy", "label": "prod", "kind": "release"},
                    {"name": "boom"},
                ]
            },
        ),
        patch.object(jk, "ingest_jenkins_job", side_effect=job),
    ):
        results = jk.ingest_jenkins_all(db)
    # The failed job reports a fixed message; its exception text stays in the log.
    connection = {
        "connection_id": "env:jenkins",
        "connection_name": "Environment default",
    }
    assert results == [
        {"job": "deploy", "created_events": 2, **connection},
        {
            "job": "boom",
            "error": "Collection failed; check the input, credentials, upstream "
            "service and storage configuration",
            **connection,
        },
    ]
    db.rollback.assert_called_once_with()

    with (
        patch.object(jk.settings, "jenkins_enabled", True),
        patch.object(jk, "load_jenkins_config", return_value={}),
    ):
        assert jk.ingest_jenkins_all(db) == []


def test_load_config():
    with patch.object(jk, "load_document", return_value={"jobs": []}) as document:
        assert jk.load_jenkins_config("fixture.yml") == {"jobs": []}
    document.assert_called_once_with("jenkins", "fixture.yml")


def test_cursor_lookup_reuses_an_existing_row(db):
    existing = MagicMock(last_ts=None)
    db.query.return_value.filter.return_value.one_or_none.return_value = existing
    assert jk._get_or_create_cursor(db, "jenkins:deploy") is existing
    db.execute.assert_not_called()
    db.flush.assert_not_called()


def test_cursor_creation_upserts_then_reads_the_row(db):
    # Overlapping runs may both create the cursor; the insert ignores a
    # conflict and the row is read back afterwards.
    created = MagicMock(last_ts=None)
    db.query.return_value.filter.return_value.one.return_value = created
    assert jk._get_or_create_cursor(db, "jenkins:deploy") is created
    (statement,) = [c.args[0] for c in db.execute.call_args_list]
    compiled = statement.compile()
    values = {
        k: v.value for k, v in compiled.binds.items() if k in {"name", "metadata"}
    }
    assert values["name"] == jk.namespace("jenkins:deploy")
    assert values["metadata"] == {}
    assert str(statement.compile(dialect=postgresql.dialect())) == (
        "INSERT INTO ingestion_cursors (id, name, last_ts, metadata, updated_at) "
        "VALUES (%(id)s::UUID, %(name)s, %(last_ts)s, %(metadata)s::JSONB, "
        "%(updated_at)s) ON CONFLICT (name) DO NOTHING"
    )
    db.flush.assert_called_once_with()


def test_deduplicated_builds_count_for_nothing_but_advance_the_cursor(db):
    job = {"allBuilds": [build(2, "SUCCESS", timestamp=1_760_000_005_000), build(1)]}
    with serving(lambda request: __import__("httpx2").Response(200, json=job)):
        cursor = MagicMock(last_ts=None)
        with (
            patch.object(jk.settings, "jenkins_base_url", "https://jenkins.example"),
            patch.object(jk, "_get_or_create_cursor", return_value=cursor),
            patch.object(
                jk,
                "store_event_with_artifact",
                side_effect=[{"deduped": False}, {"deduped": True}],
            ),
        ):
            result = jk.ingest_jenkins_job(db, "deploy", None, None)
    assert result["created_events"] == 1
    assert cursor.last_ts == jk.utc_from_timestamp_naive(1_760_000_005_000 / 1000)


class FakeJenkins:
    """A job's builds, newest first, served as Jenkins pages allBuilds{M,N}."""

    def __init__(self, builds, arriving=(), kept=None):
        self.builds = builds
        self.arriving = list(arriving)
        # Builds Jenkins still holds but no longer lists, read one by one.
        self.kept = dict(kept or {})
        self.ranges = []
        self.scans = []
        self.fetched = []

    def __call__(self, request):
        tree = request.url.params["tree"]
        if not tree.startswith("allBuilds["):
            number = int(request.url.path.split("/")[-3])
            self.fetched.append(number)
            assert tree == jk.BUILD_FIELDS
            listed = {b["number"]: b for b in self.builds if isinstance(b, dict)}
            body = self.kept.get(number, listed.get(number))
            if body is None:
                return httpx.Response(404)
            return httpx.Response(200, json=body)
        fields, span = tree.removeprefix("allBuilds[").split("]")
        start, end = (int(x) for x in span.strip("{}").split(","))
        self.ranges.append((start, end))
        if fields != jk.BUILD_FIELDS:
            # A scan for where collected builds start lists numbers and times.
            assert fields == "number,timestamp"
            self.scans.append((start, end))
        page = self.builds[start:end]
        if self.arriving:
            # Builds started while the run reads move every later page down.
            self.builds.insert(0, self.arriving.pop(0))
        return httpx.Response(200, json={"allBuilds": page})


def _collect(db, server, cursor):
    stored = []
    with serving(server):
        with (
            patch.object(jk.settings, "jenkins_base_url", "https://jenkins.example"),
            patch.object(jk, "_get_or_create_cursor", return_value=cursor),
            patch.object(
                jk,
                "store_event_with_artifact",
                side_effect=lambda db, **kw: stored.append(kw) or {"deduped": False},
            ),
        ):
            result = jk.ingest_jenkins_job(db, "backup", None, None)
    return result, [
        (
            s["raw_pointer"]["jenkins"]["build_number"],
            s["normalized_payload"]["build"].get("result") or "RUNNING",
        )
        for s in stored
    ]


def _cursor(**meta):
    return SimpleNamespace(last_ts=None, meta=meta, updated_at=None)


def test_a_build_running_at_one_run_has_its_final_result_collected_later(db):
    server = FakeJenkins(
        [{"number": 7, "timestamp": 1_760_000_060_000, "building": True}, build(6)]
    )
    cursor = _cursor()
    assert _collect(db, server, cursor)[1] == [(6, "SUCCESS"), (7, "RUNNING")]
    assert cursor.meta == {"last_number": 7, "pending": [7]}

    # Still running: nothing new is stored and it stays pending.
    assert _collect(db, server, cursor)[1] == []
    assert cursor.meta == {"last_number": 7, "pending": [7]}

    server.builds[0] = build(7, "FAILURE", timestamp=1_760_000_060_000)
    assert _collect(db, server, cursor)[1] == [(7, "FAILURE")]
    assert cursor.meta == {"last_number": 7, "pending": []}
    assert _collect(db, server, cursor)[1] == []


def test_every_build_since_the_last_run_is_collected_across_pages(db):
    server = FakeJenkins(
        [build(n, timestamp=1_760_000_000_000 + n) for n in range(130, 0, -1)]
    )
    cursor = _cursor(last_number=10, pending=[])
    result, stored = _collect(db, server, cursor)
    assert [n for n, _ in stored] == list(range(11, 131))
    assert server.ranges == [(0, 50), (50, 100), (100, 150)]
    assert cursor.meta["last_number"] == 130
    assert result["created_events"] == 120


def test_a_backlog_longer_than_one_run_reads_is_collected_oldest_part_first(db):
    total = jk.PAGE * jk.MAX_PAGES * 2 + 30
    server = FakeJenkins([build(n) for n in range(total, 0, -1)])
    cursor = _cursor(last_number=5, pending=[])
    collected = []
    scans = []
    while cursor.meta["last_number"] < total:
        server.scans.clear()
        collected += [n for n, _ in _collect(db, server, cursor)[1]]
        scans.append(bool(server.scans))
    # Every build after 5 is collected once, oldest first, over three runs;
    # the last read to the cursor directly.
    assert collected == list(range(6, total + 1))
    assert scans == [True, True, False]


def test_builds_started_during_a_catch_up_are_not_skipped(db):
    total = jk.PAGE * jk.MAX_PAGES + 200
    server = FakeJenkins(
        [build(n) for n in range(total, 0, -1)],
        arriving=[build(n) for n in range(total + 1, total + 40)],
    )
    cursor = _cursor(last_number=100, pending=[])
    first = [n for n, _ in _collect(db, server, cursor)[1]]
    assert server.scans == [(1000, 2000)]
    # The run reads the oldest pages above build 100 without a gap; a page
    # moved down by new builds repeats a build, which storage deduplicates.
    assert sorted(set(first)) == list(range(101, cursor.meta["last_number"] + 1))
    rest = [n for n, _ in _collect(db, server, cursor)[1]]
    assert sorted(set(first + rest)) == list(range(101, cursor.meta["last_number"] + 1))
    assert cursor.meta["last_number"] == server.builds[0]["number"]


def test_a_backlog_beyond_every_scan_keeps_the_cursor(db, monkeypatch):
    monkeypatch.setattr(jk, "MAX_SCANS", 2)
    server = FakeJenkins([build(n) for n in range(5000, 0, -1)])
    cursor = _cursor(last_number=5, pending=[])
    with pytest.raises(ValueError, match="cursor was not advanced"):
        _collect(db, server, cursor)
    assert cursor.meta == {"last_number": 5, "pending": []}


def test_builds_arriving_faster_than_a_run_reads_keep_the_cursor(db, monkeypatch):
    monkeypatch.setattr(jk, "MAX_PAGES", 2)
    # Each request finds a whole page of new builds ahead of the last.
    server = FakeJenkins([build(n) for n in range(300, 0, -1)])

    def flood(request, real=server.__call__):
        response = real(request)
        top = server.builds[0]["number"]
        server.builds[:0] = [build(n) for n in range(top + 60, top, -1)]
        return response

    cursor = _cursor(last_number=5, pending=[])
    with pytest.raises(ValueError, match="faster than Keen reads"):
        _collect(db, flood, cursor)
    assert cursor.meta == {"last_number": 5, "pending": []}


def test_a_first_run_reads_only_the_newest_page(db):
    server = FakeJenkins([build(n) for n in range(80, 0, -1)])
    cursor = _cursor()
    result, stored = _collect(db, server, cursor)
    assert (server.ranges, len(stored)) == ([(0, 50)], 50)
    assert cursor.meta["last_number"] == 80


def test_a_cursor_kept_by_time_moves_to_build_numbers(db):
    seen = datetime(2026, 10, 10)
    at = int(seen.replace(tzinfo=timezone.utc).timestamp() * 1000)
    server = FakeJenkins(
        [
            build(3, timestamp=at + 1000),
            build(2, timestamp=at),
            build(1, timestamp=at - 1000),
        ]
    )
    cursor = SimpleNamespace(last_ts=seen, meta={}, updated_at=None)
    # The first run reads again the finished builds the time cursor listed, as
    # one it stored running would otherwise never have its result collected.
    assert _collect(db, server, cursor)[1] == [
        (1, "SUCCESS"),
        (2, "SUCCESS"),
        (3, "SUCCESS"),
    ]
    assert cursor.meta == {"last_number": 3, "pending": []}
    # From then on build numbers decide.
    server.builds.insert(0, build(4, timestamp=at - 5000))
    assert _collect(db, server, cursor)[1] == [(4, "SUCCESS")]
    assert cursor.meta == {"last_number": 4, "pending": []}


def test_a_time_cursor_reads_one_page_below_the_first_it_reaches(db):
    seen = datetime(2026, 10, 10)
    at = int(seen.replace(tzinfo=timezone.utc).timestamp() * 1000)
    newer = [build(n, timestamp=at + n) for n in range(100, 90, -1)]
    older = [build(n, timestamp=at - 1000) for n in range(90, 0, -1)]
    # The time cursor stored build 35 running; it finished since.
    older[90 - 35] = build(35, "FAILURE", timestamp=at - 1000)
    server = FakeJenkins([*newer, *older])
    cursor = SimpleNamespace(last_ts=seen, meta={}, updated_at=None)
    result, stored = _collect(db, server, cursor)
    assert server.ranges == [(0, 50), (50, 100)]
    assert (35, "FAILURE") in stored


def test_a_time_cursor_behind_a_long_backlog_reads_one_page_more(db, monkeypatch):
    for name, value in (("PAGE", 5), ("MAX_PAGES", 2), ("SCAN", 10)):
        monkeypatch.setattr(jk, name, value)
    seen = datetime(2026, 10, 10)
    at = int(seen.replace(tzinfo=timezone.utc).timestamp() * 1000)
    server = FakeJenkins(
        [build(n, timestamp=at + (1000 if n > 20 else -1000)) for n in range(40, 0, -1)]
    )
    cursor = SimpleNamespace(last_ts=seen, meta={}, updated_at=None)
    _collect(db, server, cursor)
    assert server.ranges[-1] == (25, 30)


def test_a_time_cursor_follows_builds_still_running(db):
    seen = datetime(2026, 10, 10)
    at = int(seen.replace(tzinfo=timezone.utc).timestamp() * 1000)
    running = {"number": 2, "timestamp": at - 1000, "building": True}
    server = FakeJenkins(
        [build(3, timestamp=at + 1000), running, build(1, timestamp=at - 2000)]
    )
    cursor = SimpleNamespace(last_ts=seen, meta={}, updated_at=None)
    # Build 2 was stored running by the time cursor and is not stored again.
    assert _collect(db, server, cursor)[1] == [(1, "SUCCESS"), (3, "SUCCESS")]
    assert cursor.meta == {"last_number": 3, "pending": [2]}
    server.builds[1] = build(2, "FAILURE", timestamp=at - 1000)
    assert _collect(db, server, cursor)[1] == [(2, "FAILURE")]
    assert cursor.meta == {"last_number": 3, "pending": []}


def test_discarded_builds_and_gaps_do_not_hold_the_cursor(db):
    server = FakeJenkins([build(12), build(9)])
    # Build 8 was running and has since been deleted; 10 and 11 were discarded.
    cursor = _cursor(last_number=8, pending=[8])
    assert _collect(db, server, cursor)[1] == [(9, "SUCCESS"), (12, "SUCCESS")]
    assert cursor.meta == {"last_number": 12, "pending": []}
    assert server.fetched == [8]


def test_a_pending_build_beyond_the_listing_is_read_by_itself(db):
    server = FakeJenkins(
        [build(n) for n in range(80, 30, -1)],
        kept={
            5: build(5, "FAILURE"),
            6: {"number": 6, "timestamp": 1, "building": True},
            7: {"number": 70},
        },
    )
    cursor = _cursor(last_number=30, pending=[5, 6, 7])
    result, stored = _collect(db, server, cursor)
    assert server.fetched == [5, 6, 7]
    # 5 finished, 6 still runs, and 7 answered with another build.
    assert stored[0] == (5, "FAILURE")
    assert [n for n, _ in stored[1:]] == list(range(31, 81))
    assert cursor.meta == {"last_number": 80, "pending": [6]}


def test_a_pending_build_read_by_itself_fails_the_run_on_an_error(db):
    def handler(request):
        if request.url.params["tree"].startswith("allBuilds["):
            return httpx.Response(200, json={"allBuilds": [build(9)]})
        return httpx.Response(500)

    cursor = _cursor(last_number=9, pending=[3])
    with pytest.raises(httpx.HTTPStatusError):
        _collect(db, handler, cursor)
    assert cursor.meta == {"last_number": 9, "pending": [3]}


def test_at_most_max_pending_builds_are_followed(db):
    running = [
        {"number": n, "timestamp": 1, "building": True} for n in range(150, 0, -1)
    ]
    with patch.object(jk, "PAGE", 200):
        server = FakeJenkins(running)
        cursor = _cursor()
        result, _ = _collect(db, server, cursor)
    assert cursor.meta["pending"] == list(range(51, 151))
    assert result["error"].startswith("50 running builds beyond the 100")
    # Those past the cap that the listing no longer shows are read one by one
    # only up to the cap too.
    cursor.meta["pending"] = list(range(1, 151))
    server = FakeJenkins([build(151)], kept={n: build(n) for n in range(1, 151)})
    _collect(db, server, cursor)
    assert server.fetched == list(range(1, 101))


def test_a_job_created_again_under_its_name_is_read_as_new(db):
    server = FakeJenkins([build(n) for n in range(3, 0, -1)])
    cursor = _cursor(last_number=40, pending=[39])
    result, stored = _collect(db, server, cursor)
    assert stored == [(1, "SUCCESS"), (2, "SUCCESS"), (3, "SUCCESS")]
    assert cursor.meta == {"last_number": 3, "pending": []}
    # The build seen running is still looked for, and is gone.
    assert server.fetched == [39]


def test_a_build_running_when_the_newest_were_deleted_is_still_followed(db):
    server = FakeJenkins(
        [build(n, "FAILURE" if n == 90 else "SUCCESS") for n in range(97, 47, -1)]
    )
    cursor = _cursor(last_number=100, pending=[90])
    result, stored = _collect(db, server, cursor)
    assert (90, "FAILURE") in stored
    assert cursor.meta == {"last_number": 97, "pending": []}


def test_unreadable_cursor_state_is_read_as_none(db):
    server = FakeJenkins([build(2), {"timestamp": 1}, "x"])
    cursor = _cursor(last_number="2", pending="7")
    result, stored = _collect(db, server, cursor)
    assert stored == [(2, "SUCCESS")]
    assert cursor.meta == {"last_number": 2, "pending": []}


def test_a_time_cursor_passes_over_builds_with_unreadable_times(db):
    server = FakeJenkins(
        [
            {"number": 3, "timestamp": "soon", "building": True},
            build(2, timestamp=1_793_000_000_000),
            "x",
            build(1, timestamp="soon"),
        ]
    )
    cursor = SimpleNamespace(last_ts=datetime(2026, 10, 10), meta=None, updated_at=None)
    result, stored = _collect(db, server, cursor)
    # Builds 1 and 3 cannot be placed in time and are counted unreadable.
    assert stored == [(2, "SUCCESS")]
    assert result["invalid_records"] == 2


def test_a_time_cursor_pages_until_it_reaches_collected_builds(db):
    seen = datetime(2026, 10, 10)
    at = int(seen.replace(tzinfo=timezone.utc).timestamp() * 1000)
    newer = [build(n, timestamp=at + n) for n in range(60, 11, -1)]
    server = FakeJenkins([*newer, "not a build", build(11, timestamp=at - 1)])
    cursor = SimpleNamespace(last_ts=seen, meta={}, updated_at=None)
    result, stored = _collect(db, server, cursor)
    assert server.ranges == [(0, 50), (50, 100)]
    # Build 11 is on the page that reaches the time cursor and is read again.
    assert [n for n, _ in stored] == list(range(11, 61))
    assert cursor.meta == {"last_number": 60, "pending": []}
