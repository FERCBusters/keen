"""Gitea and Forgejo collector contracts against upstream REST and RSS fixtures."""

import contextlib
from datetime import datetime
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.ingest import forgejo as fg
from app.ingest import gitea as gt

ACTIVITY = {
    "id": 9,
    "op_type": "commit_repo",
    "created": "2026-10-09T12:00:00Z",
    "act_user": {"login": "alice"},
    "title": "alice pushed",
    "content": "branch main",
}

RSS = (
    "<rss><channel>"
    "<item><title>alice pushed</title><pubDate>Tue, 08 Oct 2026 00:00:00 GMT"
    "</pubDate><author>alice</author><guid>g1</guid><link>https://host.example/1"
    "</link></item>"
    "<item><title>stale</title><pubDate>Mon, 01 Jan 2001 00:00:00 GMT</pubDate></item>"
    "<item><title>no timestamp</title></item>"
    "</channel></rss>"
)


# The fixtures carry absolute timestamps and the collectors keep a 14-day initial
# lookback, so pin the clock a day after the newest fixture item.
NOW = datetime(2026, 10, 10, 12, 0, 0)


@pytest.fixture(autouse=True)
def pinned_clock():
    with (
        patch.object(fg, "utc_now_naive", return_value=NOW),
        patch.object(gt, "utc_now_naive", return_value=NOW),
    ):
        yield


@pytest.fixture(params=[fg, gt], ids=["forgejo", "gitea"])
def collector(request):
    return request.param


def prefix(collector):
    return "forgejo_" if collector is fg else "gitea_"


def source(collector):
    return prefix(collector).rstrip("_")


def feed_fn(collector):
    return getattr(collector, "ingest_" + prefix(collector) + "repo_feed")


def all_fn(collector):
    return getattr(collector, "ingest_" + prefix(collector) + "all")


@contextlib.contextmanager
def configured(collector, **values):
    with contextlib.ExitStack() as stack:
        for key, value in values.items():
            attribute = key if key == "demo_mode" else prefix(collector) + key
            stack.enter_context(patch.object(collector.settings, attribute, value))
        yield


@contextlib.contextmanager
def serving(collector, handler, safe=True):
    def fake_client(**kwargs):
        kwargs.pop("timeout", None)
        kwargs.pop("follow_redirects", None)
        return contextlib.nullcontext(
            httpx.Client(
                transport=httpx.MockTransport(handler),
                follow_redirects=False,
                **kwargs,
            )
        )

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(collector, "ingestion_client", fake_client))
        stack.enter_context(patch.object(collector, "is_safe_url", return_value=safe))
        yield


@contextlib.contextmanager
def connections_env(collector):
    from app.ingest.connections import Connection

    src = source(collector)
    connection = Connection(
        "env:" + src, src, "Environment default", {}, {}, None, True, "environment"
    )

    @contextlib.contextmanager
    def fake_run_lock(db, connection_id):
        yield True

    with (
        patch("app.ingest.connections.connections", return_value=[connection]),
        patch("app.ingest.connections.run_lock", fake_run_lock),
    ):
        yield


def test_auth_header_modes(collector):
    # Provenance note: an empty auth mode falls through to the token header
    # because the collector defaults it with `auth_mode or "token"`. This is
    # read from the code, not documented upstream; flag to the maintainer if
    # the intent was different.
    with configured(collector, auth_mode="none", token="t"):
        assert collector._auth_headers() == {}
    with configured(collector, auth_mode="", token=""):
        assert collector._auth_headers() == {}
    with configured(collector, auth_mode="token", token="t"):
        assert collector._auth_headers() == {"Authorization": "token t"}
    with configured(collector, auth_mode="bearer", token="t"):
        assert collector._auth_headers() == {"Authorization": "Bearer t"}
    with configured(collector, auth_mode="cookie", cookie="sf=1", token="t"):
        assert collector._auth_headers() == {"Cookie": "sf=1"}
    with configured(collector, auth_mode="cookie", cookie="", token="t"):
        assert collector._auth_headers() == {"Authorization": "token t"}
    with configured(collector, auth_mode="token", token="   "):
        assert collector._auth_headers() == {}


def test_basic_auth(collector):
    with configured(collector, username="alice", token="t"):
        assert collector._basic_auth() == ("alice", "t")
    with configured(collector, username=" ", token="t"):
        assert collector._basic_auth() is None
    with configured(collector, username="alice", token=""):
        assert collector._basic_auth() is None


def test_with_query_token(collector):
    url = "https://host.example/team/repo.rss?limit=1"
    with configured(collector, token="t"):
        rewritten = collector._with_query_token(url)
        assert rewritten.startswith(url.split("?")[0])
        assert "limit=1" in rewritten
        assert "token=t" in rewritten
    with configured(collector, token=""):
        assert collector._with_query_token(url) == url


def test_fetch_api_activities_pagination(collector):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.params["page"] == "1":
            return httpx.Response(200, json=[{"id": 1}, {"id": 2}, {"id": 3}])
        return httpx.Response(200, json=[{"id": 4}])

    with (
        configured(collector, auth_mode="token", token="fixture-secret"),
        serving(collector, handler),
    ):
        items = collector._fetch_api_activities(
            base_url="https://host.example",
            owner="team",
            repo="repo",
            max_pages=5,
            limit=3,
        )
    assert [i["id"] for i in items] == [1, 2, 3, 4]
    assert requests[0].url.path == "/api/v1/repos/team/repo/activities/feeds"
    assert requests[0].url.params["page"] == "1"
    assert requests[0].url.params["limit"] == "3"
    assert requests[1].url.params["page"] == "2"
    # Upstream documents the JSON activity feed informally; assert only the
    # loader forwards the Accept header and that auth never goes in the query.
    assert requests[0].headers.get("Accept") == "application/json"
    assert requests[0].headers["Authorization"] == "token fixture-secret"
    assert "token" not in str(requests[0].url)


def test_fetch_api_activities_stops_on_short_pages_and_non_list(collector):
    def handler(request):
        if int(request.url.params["page"]) < 2:
            return httpx.Response(200, json=[{"id": 1}, {"id": 2}])
        return httpx.Response(200, json={"not": "a list"})

    with serving(collector, handler):
        assert collector._fetch_api_activities(
            base_url="https://host.example",
            owner="team",
            repo="repo",
            max_pages=5,
            limit=50,
        ) == [{"id": 1}, {"id": 2}]


def test_fetch_api_activities_pagination_limit_raises(collector):
    with serving(collector, lambda request: httpx.Response(200, json=[{"id": 1}])):
        with pytest.raises(ValueError) as info:
            collector._fetch_api_activities(
                base_url="https://host.example",
                owner="team",
                repo="repo",
                max_pages=1,
                limit=1,
            )
    assert "pagination" in str(info.value)


def test_fetch_api_activities_status_error_hides_credentials(collector):
    with (
        configured(collector, auth_mode="token", token="fixture-secret"),
        serving(
            collector,
            lambda request: httpx.Response(
                302,
                headers={"location": "https://host.example/login?token=fixture-secret"},
            ),
        ),
    ):
        with pytest.raises(RuntimeError) as info:
            collector._fetch_api_activities(
                base_url="https://host.example", owner="team", repo="repo"
            )
    assert "fixture-secret" not in str(info.value)
    assert "302" in str(info.value)


def test_store_api_activity_items_sorts_deduplicates_and_updates_cursor(collector):
    db = MagicMock()
    cursor_last = datetime(2026, 10, 9, 10, 0, 0)
    cur = MagicMock(last_ts=cursor_last)
    items = [
        dict(
            ACTIVITY,
            id=1,
            act_user=None,
            user={"name": "carl"},
            title=None,
            content="detail " * 40,
        ),
        dict(
            ACTIVITY,
            id=2,
            act_user={"username": "beck"},
            created="2026-10-09T11:30:00Z",
        ),
        {"id": 3},
        dict(ACTIVITY, id=4, created="2020-01-01T00:00:00Z"),
        dict(ACTIVITY, id=5, created="2026-10-09T09:00:00Z"),
    ]
    with patch.object(
        collector,
        "store_event_with_artifact",
        side_effect=lambda db, **kw: {"deduped": False},
    ) as store:
        result = collector._store_api_activity_items(
            db,
            cur,
            all_items=items,
            cursor_name="cursor-name",
            feed_url="https://host.example/team/repo.rss",
            host="host.example",
            owner="team",
            repo="repo",
            label="Lab",
            since=datetime(2026, 10, 7),
            cursor_last=cursor_last,
            mode="api",
        )
    assert result["created_events"] == 2
    assert result["mode"] == "api"
    assert result["cursor_last_ts"] == "2026-10-09T12:00:00"
    calls = [c.kwargs for c in store.call_args_list]
    assert [c["actor"] for c in calls] == ["beck", "carl"]
    assert all(c["source"] == source(collector) for c in calls)
    assert all(c["artifact_kind"] == source(collector) + "_activity" for c in calls)
    assert calls[0]["summary"].startswith("[Lab] ")
    assert "by beck" in calls[0]["summary"]
    assert calls[1]["action"] == "commit_repo"
    assert "detail" in calls[1]["summary"]
    assert len(calls[1]["summary"]) < len("detail " * 40) + 60
    assert all(c["system"] == "Lab" for c in calls)
    assert all(c["captured_by"] == "keen:" + source(collector) for c in calls)
    assert calls[0]["raw_pointer"][source(collector)]["feed"].endswith("repo.rss")
    assert calls[0]["normalized_payload"]["activity"]["id"] == 2
    assert cur.last_ts == datetime(2026, 10, 9, 12, 0, 0)
    assert cur.updated_at is not None
    db.add.assert_called_once_with(cur)
    db.commit.assert_called_once()


def test_store_api_activity_items_label_and_content_summary(collector):
    db = MagicMock()
    cur = MagicMock(last_ts=None)
    item = dict(
        ACTIVITY,
        id=1,
        act_user=None,
        user={"name": "carl"},
        title=None,
        content="detail " * 40,
    )
    assert item["user"] == {"name": "carl"}
    with patch.object(
        collector,
        "store_event_with_artifact",
        return_value={"deduped": True},
    ) as store:
        result = collector._store_api_activity_items(
            db,
            cur,
            all_items=[item],
            cursor_name="cursor-name",
            feed_url="https://host.example/team/repo.rss",
            host="host.example",
            owner="team",
            repo="repo",
            label="Lab",
            since=datetime(2026, 10, 7),
            cursor_last=None,
        )
    assert result["created_events"] == 0
    summary = store.call_args.kwargs["summary"]
    assert summary.startswith("[Lab] ")
    assert "detail" in summary
    assert len([b for b in summary.split() if b]) < 45
    assert cur.last_ts == datetime(2026, 10, 9, 12, 0, 0)


def test_repo_feed_api_success(collector):
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = None

    def handler(request):
        assert request.url.path == "/api/v1/repos/team/repo/activities/feeds"
        return httpx.Response(200, json=[ACTIVITY])

    with (
        configured(collector),
        serving(collector, handler),
        patch.object(
            collector, "store_event_with_artifact", return_value={"deduped": False}
        ) as store,
    ):
        result = feed_fn(collector)(
            db, feed_url="https://host.example/team/repo.rss?token=fixture-secret"
        )
    assert result["mode"] == "api"
    assert result["created_events"] == 1
    assert result["cursor_last_ts"] == "2026-10-09T12:00:00"
    assert b"fixture-secret" not in store.call_args.kwargs["artifact_bytes"]
    cursor_obj = db.add.call_args_list[0].args[0]
    assert cursor_obj.meta["url"] == "https://host.example/team/repo.rss?token=REDACTED"
    db.commit.assert_called_once()


def test_repo_feed_rss_fallback(collector):
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = None

    def handler(request):
        if request.url.path.endswith("/activities/feeds"):
            return httpx.Response(403)
        assert request.url.path == "/team/repo.rss"
        return httpx.Response(
            200,
            headers={"content-type": "application/rss+xml; charset=utf-8"},
            text=RSS,
        )

    stored = []
    with (
        configured(collector),
        serving(collector, handler),
        patch.object(
            collector,
            "store_event_with_artifact",
            side_effect=lambda db, **kw: stored.append(kw) or {"deduped": False},
        ),
    ):
        result = feed_fn(collector)(db, feed_url="https://host.example/team/repo.rss")
    assert result["mode"] == "rss_fallback"
    assert result["api_error"]
    assert result["created_events"] == 1
    assert stored[0]["source"] == source(collector)
    assert stored[0]["artifact_kind"] == source(collector) + "_rss"
    assert stored[0]["action"] == "push"
    assert stored[0]["outcome"] == "success"
    assert stored[0]["actor"] == "alice"
    assert stored[0]["raw_pointer"][source(collector)]["guid"] == "g1"
    assert stored[0]["system"] == "host.example/team/repo"
    db.rollback.assert_called_once()
    db.commit.assert_called_once()


def test_repo_feed_failures_report_without_credentials(collector):
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = None

    def handler(request):
        return httpx.Response(403)

    with (
        configured(collector, token="fixture-secret", base_url="https://host.example"),
        serving(collector, handler),
    ):
        result = feed_fn(collector)(
            db, feed_url="https://host.example/team/repo.rss?token=fixture-secret"
        )
    assert result["mode"] == "failed"
    assert result["created_events"] == 0
    assert result["api_error"] and result["rss_error"]
    assert "fixture-secret" not in result["api_error"] + result["rss_error"]
    db.rollback.assert_called()


def test_repo_feed_cannot_infer_api_target(collector):
    db = MagicMock()
    with configured(collector), serving(collector, lambda request: httpx.Response(403)):
        result = feed_fn(collector)(db, feed_url="https://host.example/missing")
    assert result["mode"] == "failed"
    assert "Could not infer" in result["api_error"]


def test_repo_feed_uses_cursor_and_skips_seen_items(collector):
    db = MagicMock()
    existing = MagicMock(last_ts=datetime(2026, 10, 9, 12, 0, 0), meta={})
    db.query.return_value.filter.return_value.one_or_none.return_value = existing

    def handler(request):
        assert request.url.path == "/api/v1/repos/team/repo/activities/feeds"
        return httpx.Response(200, json=[ACTIVITY])

    with (
        configured(collector),
        serving(collector, handler),
        patch.object(collector, "store_event_with_artifact") as store,
    ):
        result = feed_fn(collector)(db, feed_url="https://host.example/team/repo.rss")
    assert result["created_events"] == 0
    assert result["cursor_last_ts"] == "2026-10-09T12:00:00"
    store.assert_not_called()
    db.add.assert_called_once_with(existing)
    db.commit.assert_called_once()
    assert existing.meta.get("url") is None


def test_discover_repositories(collector):
    repos = [
        {"name": "repo-1", "owner": {"login": "Team"}},
        {"name": "repo_2", "owner": {"login": "TEAM"}},
    ]

    def handler(request):
        if request.url.params["page"] == "1":
            return httpx.Response(200, json=repos)
        return httpx.Response(200, json=[])

    with (
        configured(collector, auth_mode="basic", username="u", token="t"),
        serving(collector, handler),
    ):
        assert (
            collector._discover_repositories(
                "https://host.example", "organizations", "team"
            )
            == repos
        )
        assert (
            collector._discover_repositories("https://host.example", "users", "team")
            == repos
        )


def test_discover_repositories_rejects_hostile_accounts(collector):
    with serving(collector, lambda request: httpx.Response(200, json=[])):
        for section, name in [
            ("accounts", "team"),
            ("organizations", "../etc/passwd"),
            ("users", "."),
            ("users", ".."),
            ("users", "a" * 101),
        ]:
            with pytest.raises(ValueError):
                collector._discover_repositories("https://host.example", section, name)


def test_discover_repositories_requires_safe_base_url(collector):
    with patch.object(collector, "is_safe_url", return_value=False):
        with pytest.raises(ValueError):
            collector._discover_repositories("http://host.example", "users", "team")


def test_discover_repositories_response_validation(collector):
    def non_dict_row(request):
        return httpx.Response(
            200,
            json=[{"name": "repo-1", "owner": {"login": "team"}}, "not-a-dict"],
        )

    with serving(collector, non_dict_row):
        with pytest.raises(ValueError):
            collector._discover_repositories("https://host.example", "users", "team")

    def other_owner(request):
        return httpx.Response(
            200, json=[{"name": "repo", "owner": {"login": "someone-else"}}]
        )

    with serving(collector, other_owner):
        with pytest.raises(ValueError):
            collector._discover_repositories("https://host.example", "users", "team")

    def invalid_name(request):
        return httpx.Response(
            200, json=[{"name": "../escape", "owner": {"login": "team"}}]
        )

    with serving(collector, invalid_name):
        with pytest.raises(ValueError):
            collector._discover_repositories("https://host.example", "users", "team")

    def repeats_page(request):
        return httpx.Response(
            200, json=[{"name": "repo-1", "owner": {"login": "team"}}]
        )

    with serving(collector, repeats_page):
        with pytest.raises(ValueError):
            collector._discover_repositories("https://host.example", "users", "team")

    def not_a_list(request):
        return httpx.Response(200, json={"repos": []})

    with serving(collector, not_a_list):
        with pytest.raises(ValueError):
            collector._discover_repositories("https://host.example", "users", "team")

    def empty_page(request):
        return httpx.Response(200, json=[])

    with serving(collector, empty_page):
        assert (
            collector._discover_repositories("https://host.example", "users", "team")
            == []
        )

    def status_error(request):
        return httpx.Response(403)

    with serving(collector, status_error):
        with pytest.raises(RuntimeError):
            collector._discover_repositories("https://host.example", "users", "team")


def test_ingest_all_discovers_and_deduplicates_feeds(collector):
    db = MagicMock()
    feeds = []

    def handler(request):
        if request.url.params["page"] == "1":
            return httpx.Response(
                200, json=[{"name": "repo-a", "owner": {"login": "team"}}]
            )
        return httpx.Response(200, json=[])

    def feed(db, *, feed_url, label=None):
        feeds.append((feed_url, label))
        return {"feed": feed_url, "created_events": 0}

    feed_name = feed_fn(collector).__name__
    with (
        configured(
            collector,
            enabled=True,
            base_url="https://host.example",
            config_path="fixture.yml",
            demo_mode=True,
        ),
        connections_env(collector),
        patch.object(
            collector,
            "load_document",
            return_value={
                "organizations": [{"org": "team", "label": "Team label"}],
                "feeds": [{"url": "https://other.example/dup.rss"}],
            },
        ),
        patch.object(collector, feed_name, side_effect=feed),
        serving(collector, handler),
    ):
        results = all_fn(collector)(db)
    assert any(
        r.get("scope") == "organizations" and r.get("discovered_repositories") == 1
        for r in results
    )
    assert ("https://host.example/team/repo-a.rss", "Team label") in feeds
    assert ("https://other.example/dup.rss", None) in feeds
    assert len(feeds) == 2


def test_ingest_all_handles_discovery_and_feed_failures(collector):
    db = MagicMock()
    feed_name = feed_fn(collector).__name__
    with (
        configured(
            collector,
            enabled=True,
            base_url="https://host.example",
            config_path="fixture.yml",
            demo_mode=True,
        ),
        connections_env(collector),
        patch.object(
            collector,
            "load_document",
            return_value={"feeds": [{"url": "https://h.example/a.rss", "label": "A"}]},
        ),
        patch.object(
            collector,
            feed_name,
            side_effect=RuntimeError("Collection failed: upstream HTTP 503"),
        ),
        patch.object(
            collector, "_discover_repositories", side_effect=ValueError("bad account")
        ),
    ):
        results = all_fn(collector)(db)
    assert any(r.get("error") for r in results)
    assert not [r for r in results if r.get("discovered_repositories")]
    db.rollback.assert_called()


def test_ingest_all_skips_and_empty_configuration(collector):
    db = MagicMock()
    with patch.object(collector.settings, prefix(collector) + "enabled", False):
        result = all_fn(collector)(db)
        assert result[0]["skipped"]
        assert "disabled" in result[0]["reason"]
    with (
        configured(collector, enabled=True, config_path="fixture.yml", demo_mode=True),
        connections_env(collector),
        patch.object(collector, "load_document", return_value={}),
    ):
        results = all_fn(collector)(db)
    assert results and results[0]["skipped"]
    assert "configured" in results[0]["reason"]


def test_load_config(collector):
    with patch.object(collector, "load_document", return_value={"feeds": [1]}) as doc:
        name = "load_" + prefix(collector) + "config"
        assert getattr(collector, name)("fixture.yml") == {"feeds": [1]}
    doc.assert_called_once_with(source(collector), "fixture.yml")
