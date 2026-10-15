"""Jenkins collector contracts against a mock upstream REST API."""

import contextlib
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.ingest import jenkins as jk


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
    assert jk._to_utc_naive(None) is None
    aware = datetime(2026, 10, 9, 12, tzinfo=None)
    assert jk._to_utc_naive(aware) == aware


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
                with pytest.raises(ValueError):
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
    assert stored[0]["summary"].startswith("[prod] jenkins deploy build #4 RUNNING")
    assert stored[3]["summary"].startswith("[prod] jenkins deploy build #3 SUCCESS")
    assert all(s["system"] == "prod" for s in stored)
    db.commit.assert_called_once()


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
    assert len(stored) == 1
    assert stored[0]["action"] == "release"


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
    assert result["cursor_last_ts"] is not None
    db.commit.assert_called_once()


def test_ingest_all_collections_and_error_paths(db):
    with patch.object(jk.settings, "jenkins_enabled", False):
        results = jk.ingest_jenkins_all(db)
        assert results[0]["skipped"] and results[0]["reason"]

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
    assert results[0]["job"] == "deploy" and results[0]["created_events"] == 2
    assert results[1]["job"] == "boom"
    assert "Collection failed" in results[1]["error"]
    db.rollback.assert_called_once()

    with (
        patch.object(jk.settings, "jenkins_enabled", True),
        patch.object(jk, "load_jenkins_config", return_value={}),
    ):
        assert jk.ingest_jenkins_all(db) == []


def test_load_config():
    with patch.object(jk, "load_document", return_value={"jobs": []}) as document:
        assert jk.load_jenkins_config("fixture.yml") == {"jobs": []}
    document.assert_called_once_with("jenkins", "fixture.yml")
