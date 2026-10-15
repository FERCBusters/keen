"""Taiga collector contracts against mock logging/timeline endpoints."""

import contextlib
import httpx
import pytest
from datetime import datetime as _datetime
from unittest.mock import MagicMock, patch

from app.ingest import taiga as tg

ENTRY = {
    "id": 55,
    "created": "2026-10-09T12:00:00+00:00",
    "data": {
        "user": {"username": "Ops"},
        "project": {"name": "Security", "slug": "security"},
        "userstory": {"ref": 34, "subject": "Patch rollout"},
        "comment": "Done",
    },
}


@pytest.fixture
def db():
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = None
    return db


@contextlib.contextmanager
def serving(handler):
    def fake_client(**kwargs):
        kwargs.pop("timeout", None)
        kwargs.pop("follow_redirects", None)
        kwargs.setdefault("base_url", "https://taiga.example")
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            **kwargs,
        )

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(tg, "ingestion_client", fake_client))
        stack.enter_context(patch.object(tg, "is_safe_url", return_value=True))
        stack.enter_context(
            patch.object(tg.settings, "taiga_base_url", "https://taiga.example")
        )
        yield


@contextlib.contextmanager
def auth(token="", username="", password=""):
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(tg.settings, "taiga_token", token))
        stack.enter_context(patch.object(tg.settings, "taiga_username", username))
        stack.enter_context(patch.object(tg.settings, "taiga_password", password))
        warm_cache = {"taiga_auth_token": "fixture-token"}
        stack.enter_context(
            patch.object(tg, "connection_cache", return_value=dict(warm_cache))
        )
        yield


def test_clean_snippet_and_extraction():
    assert tg._clean_snippet("\n  a   b  ") == "a b"
    assert tg._clean_snippet("x" * 200) == "x" * 159 + "…"
    assert tg._extract_object({"userstory": {"ref": 1}}) == ("userstory", {"ref": 1})
    assert tg._extract_object({"epic": None, "issue": {"id": 2}}) == (
        "issue",
        {"id": 2},
    )
    assert tg._extract_object({}) == (None, None)


def test_taiga_ui_url_variants():
    base = "https://taiga.example/"
    data = {
        "project": {"slug": "security"},
        "userstory": {"ref": 34},
    }
    assert (
        tg._taiga_ui_url(base, data) == "https://taiga.example/project/security/us/34"
    )
    task = {"project": {"slug": "security"}, "task": {"id": 7}}
    assert (
        tg._taiga_ui_url(base, task) == "https://taiga.example/project/security/task/7"
    )
    issue = {"project": {"slug": "security"}, "issue": {"id": 7}}
    assert (
        tg._taiga_ui_url(base, issue)
        == "https://taiga.example/project/security/issue/7"
    )
    epic = {"project": {"slug": "security"}, "epic": {"id": 7}}
    assert (
        tg._taiga_ui_url(base, epic) == "https://taiga.example/project/security/epic/7"
    )
    milestone = {"project": {"slug": "security"}, "milestone": {"id": 7}}
    assert tg._taiga_ui_url(base, milestone) is None
    assert tg._taiga_ui_url("", data) is None
    assert tg._taiga_ui_url(base, {"project": {}}) is None
    assert (
        tg._taiga_ui_url(
            base,
            {
                "project": {"slug": "s"},
            },
        )
        is None
    )


def test_build_summary_fields():
    summary, actor, action, extracted = tg._build_summary(ENTRY, 12, None)
    assert actor == "Ops"
    assert action == "taiga:timeline"
    assert summary.startswith("[Security] timeline")
    assert "#34" in summary and "Patch rollout" in summary
    extracted = extracted
    assert extracted["project_name"] == "Security"
    assert extracted["comment"] == "Done"
    labelled = tg._build_summary(ENTRY, 12, "prod")
    assert labelled[0].startswith("[prod] ")
    assert extracted["taiga_entry_id"] == 55


def test_build_summary_long_comment_snippet():
    entry = {
        "event_type": "issue_change",
        "data": {"comment": "c" * 300, "issue": {"id": 1}},
    }
    summary, _actor, action, _extracted = tg._build_summary(entry, 3, None)
    assert "c" * 179 in summary
    assert "taiga:issue_change" == action


def test_login_and_token_priority_and_cache():
    with auth(token="static-token"):
        with patch.object(tg, "_base_client") as base_client:
            assert tg._login_and_get_token() == "static-token"
            base_client.assert_not_called()

    with patch.object(tg.settings, "taiga_token", ""):
        with patch.object(
            tg, "connection_cache", return_value={"taiga_auth_token": "cached-token"}
        ):
            with patch.object(tg, "_base_client") as base_client:
                assert tg._login_and_get_token() == "cached-token"
                base_client.assert_not_called()

    with (
        patch.object(tg.settings, "taiga_token", ""),
        patch.object(tg.settings, "taiga_username", ""),
        patch.object(tg.settings, "taiga_password", ""),
        patch.object(tg, "connection_cache", return_value={}),
    ):
        with pytest.raises(RuntimeError) as no_creds:
            tg._login_and_get_token()
    assert "KEEN_TAIGA_USERNAME" in str(no_creds.value)

    with (
        patch.object(tg.settings, "taiga_token", ""),
        patch.object(tg.settings, "taiga_username", "u"),
        patch.object(tg.settings, "taiga_password", "p"),
        patch.object(tg, "connection_cache", return_value={}),
    ):

        def handler(request):
            assert request.url.path == "/api/v1/auth"
            return httpx.Response(200, json={"auth_token": "fresh-token"})

        cache = {}
        with serving(handler), patch.object(tg, "connection_cache", return_value=cache):
            assert tg._login_and_get_token() == "fresh-token"
            assert cache["taiga_auth_token"] == "fresh-token"

        def empty_token(request):
            return httpx.Response(200, json={})

        with (
            serving(empty_token),
            patch.object(tg, "connection_cache", return_value={}),
        ):
            with pytest.raises(RuntimeError) as missing:
                tg._login_and_get_token()
        assert "auth_token" in str(missing.value)


def test_base_client_rejects_unsafe_url():
    with (
        patch.object(tg, "is_safe_url", return_value=False),
        patch.object(tg.settings, "taiga_base_url", "https://taiga.example"),
    ):
        with pytest.raises(ValueError):
            tg._base_client()


def test_ingest_project_timeline_flow(db):
    with (
        auth(),
        serving(lambda request: httpx.Response(200, json=[ENTRY])),
        patch.object(
            tg, "store_event_with_artifact", return_value={"deduped": False}
        ) as store,
    ):
        result = tg.ingest_taiga_project_timeline(db, 12, "prod")
    assert result["created_events"] == 1
    assert result["cursor_last_ts"] == "2026-10-09T12:00:00"
    call = store.call_args.kwargs
    assert call["source"] == "taiga"
    assert call["system"] == "prod"
    assert call["actor"] == "Ops"
    assert call["external_id"] == "taiga:12:55"
    assert call["raw_pointer"]["taiga"]["timeline_entry_id"] == 55
    assert "security/us/34" in call["raw_pointer"]["taiga"]["url"]
    assert db.add.call_count == 2
    db.commit.assert_called_once()


def test_ingest_project_timeline_unauthorised_refresh(db):
    responses = {"timeline": 0, "auth": 0}
    cache = {}

    def handler(request):
        if request.method == "POST" and request.url.path == "/api/v1/auth":
            responses["auth"] += 1
            return httpx.Response(200, json={"auth_token": "fresh-token"})
        responses["timeline"] += 1
        if responses["timeline"] == 1:
            return httpx.Response(401)
        return httpx.Response(200, json=[ENTRY])

    with serving(handler), patch.object(tg, "connection_cache", return_value=cache):
        with (
            patch.object(tg.settings, "taiga_token", ""),
            patch.object(tg.settings, "taiga_base_url", "https://taiga.example"),
            patch.object(tg.settings, "taiga_username", "u"),
            patch.object(tg.settings, "taiga_password", "p"),
            patch.object(
                tg, "store_event_with_artifact", return_value={"deduped": False}
            ) as store,
        ):
            result = tg.ingest_taiga_project_timeline(db, 12, None)
    assert responses["auth"] == 2
    assert responses["timeline"] == 2
    assert result["created_events"] == 1
    store.assert_called()


def test_ingest_project_timeline_no_id_fingerprint_and_skips(db):
    entry = {"created": "2026-10-08T00:00:00Z", "data": {}}
    with (
        auth(),
        serving(lambda request: httpx.Response(200, json=[entry])),
        patch.object(
            tg, "store_event_with_artifact", return_value={"deduped": False}
        ) as store,
    ):
        result = tg.ingest_taiga_project_timeline(db, 12, None)
    assert result["created_events"] == 1
    assert len(store.call_args.kwargs["external_id"]) == 32
    assert store.call_args.kwargs["raw_pointer"]["taiga"]["timeline_entry_id"] is None


def test_ingest_project_timeline_skips_seen_cursor_entries(db):
    existing = MagicMock(last_ts=_datetime(2026, 10, 9, 12, 0, 0))
    db.query.return_value.filter.return_value.one_or_none.return_value = existing
    entries = [
        {"created": "2026-10-09T12:00:00Z", "data": {}},
        {"created": "2026-10-09T13:00:00Z", "data": {}},
    ]
    with (
        auth(),
        serving(lambda request: httpx.Response(200, json=entries)),
        patch.object(
            tg, "store_event_with_artifact", return_value={"deduped": False}
        ) as store,
    ):
        result = tg.ingest_taiga_project_timeline(db, 12, None)
    assert result["created_events"] == 1
    assert result["cursor_last_ts"] == "2026-10-09T13:00:00"
    store.assert_called_once()


def test_ingest_all(db):
    with patch.object(tg.settings, "taiga_enabled", False):
        results = tg.ingest_taiga_all(db)
        assert results[0]["skipped"] and results[0]["reason"]
    with (
        patch.object(tg.settings, "taiga_enabled", True),
        patch.object(tg, "load_taiga_config", return_value={}),
    ):
        assert tg.ingest_taiga_all(db) == []
    with (
        patch.object(tg.settings, "taiga_enabled", True),
        patch.object(
            tg,
            "load_taiga_config",
            return_value={"projects": [{"id": 1, "label": "prod"}, {"id": "bad"}]},
        ),
        patch.object(
            tg,
            "ingest_taiga_project_timeline",
            side_effect=[
                {"project_id": 1},
                RuntimeError("Collection failed: HTTP 403"),
            ],
        ),
    ):
        results = tg.ingest_taiga_all(db)
    assert results[0]["project_id"] == 1
    assert "error" in results[1] and results[1]["project_id"] == "bad"
    db.rollback.assert_called_once()


def test_load_taiga_config():
    with patch.object(tg, "load_document", return_value={"projects": []}) as document:
        assert tg.load_taiga_config("fixture.yml") == {"projects": []}
    document.assert_called_once_with("taiga", "fixture.yml")
