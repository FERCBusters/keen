"""Jenkins collector contracts against a mock upstream REST API."""

import contextlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
from app.core.datetime_utils import to_utc_naive
from app.ingest import jenkins as jk
from sqlalchemy.dialects import postgresql


@contextlib.contextmanager
def serving(handler):
    def fake_client(**kwargs):
        captured["kwargs"] = kwargs
        return contextlib.nullcontext(
            __import__("httpx").Client(
                transport=__import__("httpx").MockTransport(handler),
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
        lambda request: __import__("httpx").Response(200, json={})
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
        "builds": [
            build(3, "SUCCESS"),
            build(2, "UNSTABLE"),
            build(1, "FAILURE"),
            {"number": 4, "building": True, "timestamp": 1_760_000_003_000},
        ]
    }
    with serving(lambda request: __import__("httpx").Response(200, json=job)):
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
    db.commit.assert_called_once_with()


def test_ingest_job_skips_seen_and_unknown_timestamp_builds(db):
    job = {
        "builds": [
            build(2, "SUCCESS"),
            build(1, "SUCCESS", timestamp=None),
            {"number": 0, "building": False},
        ]
    }
    with serving(lambda request: __import__("httpx").Response(200, json=job)):
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
            jk.ingest_jenkins_job(db, "deploy", None, "release")
    assert [
        (s["raw_pointer"]["jenkins"]["build_number"], s["action"]) for s in stored
    ] == [(2, "release")]


def test_ingest_job_cursor_blocks_old_builds(db):
    job = {"builds": [build(2, "SUCCESS"), build(1, "FAILURE")]}
    with serving(lambda request: __import__("httpx").Response(200, json=job)):
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
    store.assert_not_called()
    assert result["created_events"] == 0
    assert result["cursor_last_ts"] == "2026-10-10T00:00:00"
    db.commit.assert_called_once_with()


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
    body = {"builds": [build(2), "build 1", 7, None, ["number", 1], build(0)]}
    result, stored = _builds(db, body)
    assert result["created_events"] == 2
    assert [s["raw_pointer"]["jenkins"]["build_number"] for s in stored] == [0, 2]
    db.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "body", [[build(1)], {"builds": {"0": build(1)}}, {"builds": "builds"}]
)
def test_ingest_job_reads_a_listing_of_another_shape_as_empty(db, body):
    result, stored = _builds(db, body)
    assert (result["created_events"], stored) == (0, [])
    db.commit.assert_called_once_with()


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
    job = {"builds": [build(2, "SUCCESS", timestamp=1_760_000_005_000), build(1)]}
    with serving(lambda request: __import__("httpx").Response(200, json=job)):
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
