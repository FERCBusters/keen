"""GitHub collection against a fake GitHub API, with real event and artifact storage.

The fake answers at the HTTP transport, so Keen's collectors choose
endpoints, send credentials, follow cursors and store events through the
same code that runs in production.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import Artifact, Event, IngestionCursor
from app.ingest import github

TOKEN = "synthetic-github-token-0123456789abcdef"


class FakeGitHub:
    def __init__(self):
        self.requests = []
        self.routes = {}

    def __call__(self, request):
        self.requests.append(request)
        route = self.routes.get((request.url.host, request.url.path))
        if route is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return route(request) if callable(route) else route

    def paths(self):
        return [(r.url.host, r.url.path) for r in self.requests]


@pytest.fixture
def gh(contract_db, monkeypatch, tmp_path):
    fake = FakeGitHub()
    monkeypatch.setattr(
        github,
        "ingestion_client",
        lambda **kwargs: httpx.Client(transport=httpx.MockTransport(fake), **kwargs),
    )
    for name, value in {
        "github_base_url": "https://api.github.com",
        "github_token": "",
        "github_username": "",
        "artifact_storage_backend": "local",
        "artifact_local_dir": str(tmp_path),
    }.items():
        monkeypatch.setattr(settings, name, value)

    def events(source="github"):
        contract_db.expire_all()
        return (
            contract_db.query(Event)
            .filter(Event.source == source)
            .order_by(Event.timestamp, Event.created_at)
            .all()
        )

    def cursor(name):
        contract_db.expire_all()
        return contract_db.query(IngestionCursor).filter_by(name=name).one_or_none()

    return SimpleNamespace(
        db=contract_db, fake=fake, events=events, cursor=cursor, root=tmp_path
    )


def _event(event_id, created_at, *, kind="PushEvent", repo="acme/app", actor="alice"):
    return {
        "id": event_id,
        "type": kind,
        "created_at": created_at,
        "actor": {"login": actor},
        "repo": {"name": repo},
        "payload": {"size": 1, "ref": "refs/heads/main"},
    }


# --- Organisation events ---------------------------------------------------------


@pytest.mark.parametrize(
    "token,username,login_status,expected_path,mode",
    [
        (TOKEN, "", 200, "/users/octo/events/orgs/acme", "user"),
        (TOKEN, "configured", 200, "/users/configured/events/orgs/acme", "user"),
        (TOKEN, "", 401, "/orgs/acme/events", "org"),
        ("", "", 200, "/orgs/acme/events", "org"),
    ],
    ids=["detected-user", "configured-user", "detection-fails", "anonymous"],
)
def test_org_events_choose_the_dashboard_the_credentials_allow(
    gh, monkeypatch, token, username, login_status, expected_path, mode
):
    monkeypatch.setattr(settings, "github_token", token)
    monkeypatch.setattr(settings, "github_username", username)
    gh.fake.routes[("api.github.com", "/user")] = httpx.Response(
        login_status, json={"login": "octo"}
    )
    gh.fake.routes[("api.github.com", expected_path)] = httpx.Response(200, json=[])

    result = github.ingest_github_org_events(gh.db, "acme")

    assert result["mode"] == mode
    assert gh.fake.paths()[-1] == ("api.github.com", expected_path)
    # Only a token holder asks who they are, and a configured username is trusted.
    asked = ("api.github.com", "/user") in gh.fake.paths()
    assert asked is bool(token and not username)
    for request in gh.fake.requests:
        assert request.headers.get("authorization") == (
            f"Bearer {TOKEN}" if token else None
        )
        assert request.headers["x-github-api-version"] == "2022-11-28"
    assert gh.cursor("github:org-events:acme:auto").meta["mode"] == mode


def test_org_events_store_new_events_once_in_order_with_provenance(gh):
    gh.db.add(
        IngestionCursor(
            name="github:org-events:acme:auto",
            last_ts=datetime(2026, 3, 2),
            meta={"org": "acme", "mode": "auto"},
        )
    )
    gh.db.commit()
    feed = [
        _event("3", "2026-03-03T09:00:00Z", repo="acme/api", actor="bob"),
        _event("2", "2026-03-02T00:00:00Z"),
        _event("1", "2026-03-01T00:00:00Z"),
        _event("0", "not-a-timestamp"),
    ]
    gh.fake.routes[("api.github.com", "/orgs/acme/events")] = httpx.Response(
        200, json=feed
    )

    first = github.ingest_github_org_events(gh.db, "acme", label="Acme")

    stored = gh.events()
    assert [e.external_id for e in stored] == ["2", "3"]
    assert first == {
        "org": "acme",
        "mode": "org",
        "created_events": 2,
        "events_seen": 4,
        "cursor_last_ts": "2026-03-03T09:00:00",
    }
    newest = stored[-1]
    assert (newest.system, newest.actor, newest.action) == (
        "acme/api",
        "bob",
        "PushEvent",
    )
    assert newest.summary.startswith("[Acme] ")
    assert newest.raw_pointer["github"] == {
        "endpoint": "org_events",
        "mode": "org",
        "org": "acme",
        "repo": "acme/api",
        "id": "3",
        "type": "PushEvent",
        "created_at": "2026-03-03T09:00:00Z",
    }
    artifact = gh.db.query(Artifact).filter_by(event_id=newest.id).one()
    assert artifact.kind == "github_event"

    # The same events seen again add nothing.
    again = github.ingest_github_org_events(gh.db, "acme", label="Acme")
    assert again["created_events"] == 0 and len(gh.events()) == 2


@pytest.mark.parametrize("status", [401, 403, 500])
def test_failed_org_event_requests_store_nothing_and_keep_the_cursor(gh, status):
    gh.db.add(
        IngestionCursor(
            name="github:org-events:acme:auto", last_ts=datetime(2026, 3, 2), meta={}
        )
    )
    gh.db.commit()
    gh.fake.routes[("api.github.com", "/orgs/acme/events")] = httpx.Response(
        status, json={"message": "nope"}
    )

    with pytest.raises(httpx.HTTPStatusError):
        github.ingest_github_org_events(gh.db, "acme")
    gh.db.rollback()

    assert gh.events() == []
    assert gh.cursor("github:org-events:acme:auto").last_ts == datetime(2026, 3, 2)


# --- Repositories in an organisation ----------------------------------------------


def test_repository_enumeration_skips_archives_forks_and_isolates_failures(gh):
    gh.fake.routes[("api.github.com", "/orgs/acme/repos")] = httpx.Response(
        200,
        json=[
            {"full_name": "acme/app"},
            {"full_name": "acme/old", "archived": True},
            {"full_name": "acme/copy", "fork": True},
            {"full_name": "not-a-full-name"},
            {"full_name": "acme/broken"},
        ],
    )
    gh.fake.routes[("api.github.com", "/repos/acme/app/events")] = httpx.Response(
        200, json=[_event("10", "2026-03-03T00:00:00Z")]
    )
    gh.fake.routes[("api.github.com", "/repos/acme/broken/events")] = httpx.Response(
        500, json={"message": "Server Error"}
    )

    result = github.ingest_github_org_repo_events(gh.db, "acme", label="Acme")

    assert result["repo_count"] == 3
    assert result["created_events"] == 1
    assert {r.get("repo") for r in result["per_repo"]} >= {"acme/broken"}
    broken = next(r for r in result["per_repo"] if r.get("repo") == "acme/broken")
    assert broken["error"] == "Collection failed: upstream returned HTTP 500"
    requested = {path for _, path in gh.fake.paths()}
    assert "/repos/acme/old/events" not in requested
    assert "/repos/acme/copy/events" not in requested
    (stored,) = gh.events()
    assert stored.raw_pointer["github"]["org"] == "acme"
    query = next(r for r in gh.fake.requests if r.url.path == "/orgs/acme/repos")
    assert query.url.params["type"] == "all"

    limited = github.ingest_github_org_repo_events(gh.db, "acme", repo_limit=1)
    assert limited["repo_count"] == 1


def test_archived_and_forked_repositories_can_be_included(gh):
    gh.fake.routes[("api.github.com", "/orgs/acme/repos")] = httpx.Response(
        200,
        json=[
            {"full_name": "acme/old", "archived": True},
            {"full_name": "acme/copy", "fork": True},
        ],
    )
    for name in ("old", "copy"):
        gh.fake.routes[("api.github.com", f"/repos/acme/{name}/events")] = (
            httpx.Response(200, json=[])
        )

    result = github.ingest_github_org_repo_events(
        gh.db, "acme", exclude_archived=False, exclude_forks=False
    )

    assert result["repo_count"] == 2


# --- Atom feeds -----------------------------------------------------------------


def _atom(*entries):
    body = "".join(
        f"""<entry><id>tag:github.com,2008:{kind}/{n}</id><title>{title}</title>
        <updated>{updated}</updated><author><name>{author}</name></author>
        <link href="https://github.com/acme/app/commit/{n}"/></entry>"""
        for n, kind, title, updated, author in entries
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom"><title>acme</title>{body}</feed>'


PRIVATE_FEED = f"https://github.com/organizations/acme/alice.private.atom?token={TOKEN}"


@pytest.mark.parametrize(
    "base,feed,allowed",
    [
        (
            "https://api.github.com",
            "https://github.com/organizations/acme/alice.atom",
            True,
        ),
        ("https://api.github.com", "https://attacker.example/acme.atom", False),
        (
            "https://api.github.com",
            "https://github.com.attacker.example/acme.atom",
            False,
        ),
        (
            "https://api.github.com",
            "http://github.com/organizations/acme/alice.atom",
            False,
        ),
        ("https://api.github.com", "https://user:pass@github.com/acme.atom", False),
        ("https://ghe.example/api/v3", "https://ghe.example/acme.atom", True),
        ("https://ghe.example/api/v3", "https://github.com/acme.atom", False),
    ],
)
def test_feed_credentials_are_sent_only_to_the_github_origin(
    gh, monkeypatch, base, feed, allowed
):
    monkeypatch.setattr(settings, "github_base_url", base)
    monkeypatch.setattr(settings, "github_token", TOKEN)
    monkeypatch.setattr(settings, "github_username", "alice")
    host = httpx.URL(feed).host
    gh.fake.routes[(host, httpx.URL(feed).path)] = httpx.Response(200, text=_atom())

    if allowed:
        github.ingest_github_atom_feed(gh.db, feed)
        (request,) = gh.fake.requests
        assert request.headers["authorization"].startswith("Basic ")
    else:
        with pytest.raises(ValueError):
            github.ingest_github_atom_feed(gh.db, feed)
        # Refused before any connection, so the credentials went nowhere.
        assert gh.fake.requests == []


def test_public_feeds_are_fetched_without_credentials(gh):
    feed = "https://feeds.example/acme.atom"
    gh.fake.routes[("feeds.example", "/acme.atom")] = httpx.Response(200, text=_atom())

    github.ingest_github_atom_feed(gh.db, feed)

    (request,) = gh.fake.requests
    assert "authorization" not in request.headers


def test_a_feed_redirect_is_not_followed(gh, monkeypatch):
    monkeypatch.setattr(settings, "github_token", TOKEN)
    monkeypatch.setattr(settings, "github_username", "alice")
    gh.fake.routes[("github.com", "/organizations/acme/alice.private.atom")] = (
        httpx.Response(302, headers={"location": "https://attacker.example/steal"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        github.ingest_github_atom_feed(gh.db, PRIVATE_FEED)

    assert [r.url.host for r in gh.fake.requests] == ["github.com"]


def test_a_private_feed_token_never_reaches_stored_evidence(gh, monkeypatch):
    monkeypatch.setattr(settings, "github_token", TOKEN)
    monkeypatch.setattr(settings, "github_username", "alice")
    gh.fake.routes[("github.com", "/organizations/acme/alice.private.atom")] = (
        httpx.Response(
            200,
            text=_atom(
                (1, "PushEvent", "alice pushed", "2099-01-01T00:00:00Z", "alice")
            ),
        )
    )

    github.ingest_github_atom_feed(gh.db, PRIVATE_FEED, label="Acme")

    (event,) = gh.events()
    assert event.action == "PushEvent" and event.system == "acme/app"
    assert event.summary == "[Acme] github feed: alice pushed"
    stored = json.dumps([event.raw_pointer, event.normalized_payload, event.summary])
    assert TOKEN not in stored
    files = [p for p in gh.root.rglob("*") if p.is_file()]
    assert files and all(TOKEN.encode() not in p.read_bytes() for p in files)
    (cursor,) = gh.db.query(IngestionCursor).all()
    assert TOKEN not in json.dumps(cursor.meta) and TOKEN not in cursor.name


def test_feed_entries_follow_the_cursor_and_are_stored_once(gh):
    feed = "https://feeds.example/acme.atom"
    entries = [
        (3, "PullRequestEvent", "newest", "2099-01-03T00:00:00Z", "carol"),
        (2, "PushEvent", "middle", "2099-01-02T00:00:00Z", "bob"),
        (1, "PushEvent", "oldest", "2099-01-01T00:00:00Z", "alice"),
    ]
    gh.fake.routes[("feeds.example", "/acme.atom")] = httpx.Response(
        200, text=_atom(*entries)
    )

    first = github.ingest_github_atom_feed(gh.db, feed)
    second = github.ingest_github_atom_feed(gh.db, feed)

    assert (first["created_events"], second["created_events"]) == (3, 0)
    assert [e.actor for e in gh.events()] == ["alice", "bob", "carol"]
    assert gh.events()[-1].action == "PullRequestEvent"
    assert first["cursor_last_ts"] == "2099-01-03T00:00:00"


def test_feed_entries_older_than_the_lookback_are_ignored(gh):
    feed = "https://feeds.example/acme.atom"
    gh.fake.routes[("feeds.example", "/acme.atom")] = httpx.Response(
        200, text=_atom((1, "PushEvent", "ancient", "2001-01-01T00:00:00Z", "alice"))
    )

    result = github.ingest_github_atom_feed(gh.db, feed, lookback_days=30)

    assert result["created_events"] == 0 and gh.events() == []
    assert result["newest_in_feed_ts"] == "2001-01-01T00:00:00"


@pytest.mark.parametrize(
    "document",
    [
        # External entity: the parser must not read local files into evidence.
        '<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&x;</title>'
        "<updated>2099-01-01T00:00:00Z</updated></entry></feed>",
        # Entity expansion: bounded rather than exhausting memory.
        '<?xml version="1.0"?><!DOCTYPE feed ['
        '<!ENTITY a "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">'
        + "".join(
            f'<!ENTITY {chr(98 + i)} "{("&" + chr(97 + i) + ";") * 20}">'
            for i in range(8)
        )
        + ']><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&i;</title>'
        "<updated>2099-01-01T00:00:00Z</updated></entry></feed>",
    ],
    ids=["external-entity", "entity-expansion"],
)
def test_hostile_feed_documents_store_nothing(gh, document):
    feed = "https://feeds.example/acme.atom"
    gh.fake.routes[("feeds.example", "/acme.atom")] = httpx.Response(200, text=document)

    with pytest.raises(SyntaxError):  # xml.etree.ElementTree.ParseError
        github.ingest_github_atom_feed(gh.db, feed)
    gh.db.rollback()

    assert gh.events() == []


# --- Orchestration --------------------------------------------------------------


def _run_all(gh, monkeypatch, config):
    monkeypatch.setattr(settings, "github_enabled", True)
    monkeypatch.setattr(github, "load_github_config", lambda path: config)
    # The undecorated collection: pause and connection fan-out are tested elsewhere.
    return github.ingest_github_all.__wrapped__.__wrapped__(gh.db)


def test_one_failing_stage_never_stops_the_others(gh, monkeypatch):
    gh.fake.routes[("api.github.com", "/orgs/acme/events")] = httpx.Response(
        500, json={"message": "Server Error"}
    )
    gh.fake.routes[("api.github.com", "/orgs/acme/repos")] = httpx.Response(
        200, json=[{"full_name": "acme/app"}]
    )
    gh.fake.routes[("api.github.com", "/repos/acme/app/events")] = httpx.Response(
        200, json=[_event("10", "2026-03-03T00:00:00Z")]
    )
    gh.fake.routes[("api.github.com", "/orgs/beta/events")] = httpx.Response(
        200, json=[_event("20", "2026-03-04T00:00:00Z", repo="beta/site")]
    )
    gh.fake.routes[("feeds.example", "/broken.atom")] = httpx.Response(
        200, text="<feed"
    )

    results = _run_all(
        gh,
        monkeypatch,
        {
            "organizations": [
                {"org": "acme", "include": "all"},
                {"name": "beta"},
                {"label": "missing an org name"},
            ],
            "feeds": [{"url": f"https://feeds.example/broken.atom?token={TOKEN}"}],
        },
    )

    assert {
        "org": "acme",
        "error": "Collection failed: upstream returned HTTP 500",
        "stage": "org_events",
    } in results
    assert sorted(e.external_id for e in gh.events()) == ["10", "20"]
    feed_error = next(r for r in results if "feed" in r)
    assert feed_error["error"].startswith("Collection failed")
    assert TOKEN not in feed_error["error"]


def test_collection_is_skipped_while_the_source_is_disabled(gh, monkeypatch):
    # The scheduled entry point declines before reading configuration or
    # contacting GitHub.
    monkeypatch.setattr(settings, "github_enabled", False)
    monkeypatch.setattr(
        github, "load_github_config", lambda path: pytest.fail("config was read")
    )

    results = github.ingest_github_all(gh.db)

    assert results == [
        {"source": "github", "skipped": True, "reason": "Source type is disabled"}
    ]
    assert gh.fake.requests == []


# --- Organisation audit log ------------------------------------------------------


class FakeAuditLog:
    """GitHub's org audit log: `created:>=` search, ascending pages, Link cursors."""

    def __init__(self, entries, *, next_base="https://api.github.com"):
        self.entries = sorted(entries, key=lambda e: e["@timestamp"])
        self.next_base = next_base
        self.fail_after = None  # fail requests whose `after` cursor is this value

    def __call__(self, request):
        params = dict(request.url.params)
        if self.fail_after is not None and params.get("after") == self.fail_after:
            return httpx.Response(500, json={"message": "Server Error"})
        phrase = params["phrase"]
        assert phrase.startswith("created:>=")
        since = datetime.fromisoformat(phrase[len("created:>=") :])
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        since_ms = int(since.timestamp() * 1000)
        matching = [e for e in self.entries if e["@timestamp"] >= since_ms]
        start = int(params.get("after", 0))
        size = int(params["per_page"])
        page = matching[start : start + size]
        headers = {}
        if start + size < len(matching):
            query = urlencode({**params, "after": str(start + size)})
            headers["link"] = (
                f'<{self.next_base}/organizations/4242/audit-log?{query}>; rel="next"'
            )
        return httpx.Response(200, json=page, headers=headers)


def _ms(moment):
    return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _audit(n, moment, *, action="org.add_member", actor="alice", user="bob"):
    return {
        "@timestamp": _ms(moment) if isinstance(moment, datetime) else moment,
        "created_at": _ms(moment) if isinstance(moment, datetime) else moment,
        "_document_id": f"doc-{n}",
        "action": action,
        "actor": actor,
        "user": user,
        "org": "acme",
    }


@pytest.fixture
def audit(gh, monkeypatch):
    monkeypatch.setattr(settings, "github_token", TOKEN)

    def serve(entries, **options):
        log = FakeAuditLog(entries, **options)
        gh.fake.routes[("api.github.com", "/orgs/acme/audit-log")] = log
        gh.fake.routes[("api.github.com", "/organizations/4242/audit-log")] = log
        return log

    gh.serve = serve
    return gh


def _recently(**offset):
    return (utc_now_naive() - timedelta(days=1)).replace(microsecond=0) + timedelta(
        **offset
    )


def test_audit_entries_are_stored_at_the_time_github_reports(audit):
    moment = _recently(milliseconds=755)
    audit.serve([_audit(1, moment)])

    result = github.ingest_github_org_auditlog(audit.db, "acme", label="Acme")

    (event,) = audit.events()
    assert event.timestamp == moment
    assert (event.actor, event.action, event.system) == (
        "alice",
        "org.add_member",
        "Acme",
    )
    assert event.summary == "[Acme] github org acme org.add_member target=bob by alice"
    assert event.raw_pointer["github"]["id"] == "doc-1"
    assert result["created_events"] == 1 and result["more"] is False
    assert audit.cursor("github:audit:acme").last_ts == moment
    (request,) = audit.fake.requests
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert (request.url.params["order"], request.url.params["include"]) == (
        "asc",
        "all",
    )
    # The first run looks back from now, to the second, in UTC.
    assert re.fullmatch(
        r"created:>=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", request.url.params["phrase"]
    )


def test_every_page_is_followed_within_one_run(audit):
    start = _recently()
    audit.serve([_audit(n, start + timedelta(seconds=n)) for n in range(250)])

    result = github.ingest_github_org_auditlog(audit.db, "acme")

    assert (result["created_events"], result["pages"], result["more"]) == (
        250,
        3,
        False,
    )
    assert len({e.external_id for e in audit.events()}) == 250
    followed = [r.url.path for r in audit.fake.requests]
    assert followed == [
        "/orgs/acme/audit-log",
        "/organizations/4242/audit-log",
        "/organizations/4242/audit-log",
    ]


def test_a_busy_day_is_collected_completely_over_several_runs(audit):
    # Five entries in each second: more than one run's page budget in a day.
    start = _recently()
    audit.serve(
        [
            _audit(n, start + timedelta(seconds=n // 5, milliseconds=n % 5))
            for n in range(250)
        ]
    )

    runs = []
    for _ in range(8):
        result = github.ingest_github_org_auditlog(audit.db, "acme", max_pages=1)
        runs.append(result["created_events"])
        if not result["more"] and result["created_events"] == 0:
            break

    assert sum(runs) == 250, runs
    assert runs[-1] == 0 and len(runs) <= 5
    assert len({e.raw_pointer["github"]["id"] for e in audit.events()}) == 250


def test_entries_sharing_the_cursor_instant_are_kept(audit):
    moment = _recently(milliseconds=500)
    audit.serve([_audit(1, moment), _audit(2, moment)])

    first = github.ingest_github_org_auditlog(audit.db, "acme", per_page=1, max_pages=1)
    second = github.ingest_github_org_auditlog(
        audit.db, "acme", per_page=1, max_pages=2
    )

    assert (first["created_events"], second["created_events"]) == (1, 1)
    assert sorted(e.raw_pointer["github"]["id"] for e in audit.events()) == [
        "doc-1",
        "doc-2",
    ]


def test_a_failed_page_keeps_the_pages_before_it(audit):
    start = _recently()
    log = audit.serve([_audit(n, start + timedelta(seconds=n)) for n in range(150)])
    log.fail_after = "100"

    with pytest.raises(httpx.HTTPStatusError):
        github.ingest_github_org_auditlog(audit.db, "acme")
    audit.db.rollback()

    assert len(audit.events()) == 100
    assert audit.cursor("github:audit:acme").last_ts == start + timedelta(seconds=99)
    log.fail_after = None
    resumed = github.ingest_github_org_auditlog(audit.db, "acme")
    assert resumed["created_events"] == 50 and len(audit.events()) == 150


@pytest.mark.parametrize(
    "next_base",
    [
        "https://attacker.example",
        "http://api.github.com",
        "https://token@api.github.com",
    ],
)
def test_pagination_links_off_the_api_are_never_followed(audit, next_base):
    start = _recently()
    audit.serve(
        [_audit(n, start + timedelta(seconds=n)) for n in range(150)],
        next_base=next_base,
    )

    with pytest.raises(ValueError, match="pagination link outside its API"):
        github.ingest_github_org_auditlog(audit.db, "acme")
    audit.db.rollback()

    assert [r.url.path for r in audit.fake.requests] == ["/orgs/acme/audit-log"]
    assert len(audit.events()) == 100


@pytest.mark.parametrize(
    "base,url,within",
    [
        (
            "https://api.github.com",
            "https://api.github.com/organizations/1/audit-log?after=x",
            True,
        ),
        (
            "https://api.github.com",
            "https://api.github.com:8443/organizations/1/audit-log",
            False,
        ),
        ("https://api.github.com", "https://api.github.com.attacker.example/x", False),
        (
            "https://ghe.example/api/v3",
            "https://ghe.example/api/v3/organizations/1/audit-log",
            True,
        ),
        (
            "https://ghe.example/api/v3",
            "https://ghe.example/api/v30/organizations/1",
            False,
        ),
        ("https://ghe.example/api/v3", "https://ghe.example/login", False),
        ("https://api.github.com", "not a url at all", False),
        ("https://api.github.com", "https://[::1/organizations/1", False),
    ],
)
def test_the_api_boundary_for_pagination_links(monkeypatch, base, url, within):
    monkeypatch.setattr(settings, "github_base_url", base)
    assert github._within_api(url) is within


@pytest.mark.parametrize(
    "value,expected",
    [
        (1635940599755, datetime(2021, 11, 3, 11, 56, 39, 755000)),
        ("2026-01-07T00:09:08Z", datetime(2026, 1, 7, 0, 9, 8)),
        ("2026-01-07T10:09:08+10:00", datetime(2026, 1, 7, 0, 9, 8)),
        (True, None),
        (None, None),
        ("yesterday", None),
        (10**30, None),
        ([1635940599755], None),
    ],
)
def test_audit_times_are_read_as_github_writes_them(value, expected):
    assert github._audit_ts({"@timestamp": value}) == expected


def test_malformed_audit_responses_store_nothing(audit):
    audit.fake.routes[("api.github.com", "/orgs/acme/audit-log")] = httpx.Response(
        200, json={"message": "not a list"}
    )
    with pytest.raises(ValueError, match="not a list"):
        github.ingest_github_org_auditlog(audit.db, "acme")
    audit.db.rollback()

    moment = _recently()
    audit.fake.routes[("api.github.com", "/orgs/acme/audit-log")] = httpx.Response(
        200, json=["not an entry", _audit(1, moment), {"action": "no.timestamp"}]
    )
    result = github.ingest_github_org_auditlog(audit.db, "acme")
    assert (result["created_events"], result["entries_seen"]) == (1, 1)


# --- Listed repositories, organisation stages and summaries ---------------------


def test_listed_repositories_are_collected_and_failures_stay_with_their_repository(
    gh, monkeypatch
):
    feed = [
        _event("30", "2026-03-05T00:00:00Z"),
        {
            "type": "WatchEvent",
            "created_at": "2026-03-05T00:00:00Z",
            "actor": {"login": "carol"},
        },
        {"id": "31", "type": "PushEvent", "payload": {}},
    ]
    gh.fake.routes[("api.github.com", "/repos/acme/app/events")] = httpx.Response(
        200, json=feed
    )
    config = {
        "repos": [
            {"owner": "acme", "repo": "app", "label": "App"},
            {"owner": "acme", "repo": "missing"},
        ]
    }

    results = _run_all(gh, monkeypatch, config)

    assert (results[0]["repo"], results[0]["created_events"]) == ("acme/app", 2)
    assert results[1] == {
        "repo": "acme/missing",
        "error": "Collection failed: upstream returned HTTP 404",
    }
    # An event without a time is skipped; one without an ID gets a content ID.
    assert [(e.system, e.summary) for e in gh.events()] == [
        ("App", "[App] GitHub WatchEvent acme/app by carol"),
        ("App", "[App] GitHub push 1 commit(s) to acme/app (main) by alice"),
    ]
    again = _run_all(gh, monkeypatch, config)
    assert again[0]["created_events"] == 0
    assert len(gh.events()) == 2


def test_repository_and_audit_stages_report_their_own_failures(gh, monkeypatch):
    monkeypatch.setattr(settings, "github_token", TOKEN)
    gh.fake.routes[("api.github.com", "/orgs/acme/repos")] = httpx.Response(
        500, json={"message": "Server Error"}
    )
    gh.fake.routes[("api.github.com", "/orgs/acme/audit-log")] = httpx.Response(
        403, json={"message": "Must have admin rights"}
    )

    results = _run_all(
        gh,
        monkeypatch,
        {
            "organizations": [{"org": "acme", "include": "repo_events audit"}],
            "feeds": [{"label": "a feed without an address"}],
        },
    )

    assert results == [
        {
            "org": "acme",
            "error": "Collection failed: upstream returned HTTP 500",
            "stage": "repo_events",
        },
        {
            "org": "acme",
            "error": "Collection failed: upstream returned HTTP 403",
            "stage": "audit_log",
        },
    ]
    # Organisation events were not asked for.
    assert ("api.github.com", "/orgs/acme/events") not in gh.fake.paths()


def test_feed_entries_without_dates_or_repository_links_are_handled(gh):
    feed = "https://feeds.example/odd.atom"
    gh.fake.routes[("feeds.example", "/odd.atom")] = httpx.Response(
        200,
        text=(
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry><id>tag:github.com,2008:push/1</id><title>undated</title></entry>"
            "<entry><id></id><updated>2099-01-02T00:00:00Z</updated>"
            '<link rel="alternate"/><link href="https://github.com/solo"/></entry>'
            "<entry><id>tag:github.com,2008:watch/3</id><title>odd link</title>"
            '<updated>2099-01-03T00:00:00Z</updated><link href="https://[::1/a/b"/>'
            "</entry></feed>"
        ),
    )

    result = github.ingest_github_atom_feed(gh.db, feed, label="Odd")

    assert (result["entries_seen"], result["created_events"]) == (3, 2)
    assert [(e.action, e.system, e.summary) for e in gh.events()] == [
        ("feed_entry", "Odd", "[Odd] github feed entry"),
        ("watch", "Odd", "[Odd] github feed: odd link"),
    ]
    assert gh.events()[0].raw_pointer["github"]["link"] == "https://github.com/solo"


@pytest.mark.parametrize(
    "event,summary",
    [
        (
            {
                "type": "PullRequestEvent",
                "repo": {"name": "acme/app"},
                "payload": {"action": "closed", "pull_request": {"title": " Fix "}},
            },
            'GitHub PR closed acme/app "Fix"',
        ),
        ({"type": "PullRequestEvent", "payload": {}}, "GitHub PR updated"),
        (
            {
                "type": "IssueCommentEvent",
                "repo": {"name": "acme/app"},
                "actor": {"login": "bob"},
                "payload": {
                    "action": "created",
                    "issue": {"number": 7, "title": "Crash"},
                },
            },
            'GitHub comment created acme/app#7 "Crash" by bob',
        ),
        (
            {"type": "IssuesEvent", "repo": {"name": "acme/app"}, "payload": {}},
            "GitHub issue updated acme/app",
        ),
        (
            {
                "type": "ReleaseEvent",
                "repo": {"name": "acme/app"},
                "payload": {"release": {"tag_name": "v1.0", "name": "v1.0"}},
            },
            "GitHub release published acme/app v1.0",
        ),
        (
            {
                "type": "ReleaseEvent",
                "actor": {"login": "eve"},
                "payload": {
                    "action": "created",
                    "release": {"tag_name": "v2", "name": "Second"},
                },
            },
            'GitHub release created v2 "Second" by eve',
        ),
        (
            {
                "type": "DeleteEvent",
                "repo": {"name": "acme/app"},
                "actor": {"login": "bob"},
                "payload": {"ref": "old", "ref_type": "branch"},
            },
            "GitHub branch deletion acme/app old by bob",
        ),
        ({"type": "DeleteEvent"}, "GitHub reference deletion"),
        ({"type": "PushEvent"}, "GitHub push"),
        ({}, "GitHub Event"),
    ],
)
def test_event_summaries_name_what_github_reports(event, summary):
    assert github._summarize_event(event) == summary


def test_load_config_reads_the_managed_document(monkeypatch):
    seen = []

    def load_document(name, path):
        seen.append((name, path))
        return {"orgs": []}

    monkeypatch.setattr(github, "load_document", load_document)
    assert github.load_github_config("fixture.yml") == {"orgs": []}
    assert seen == [("github", "fixture.yml")]


def test_a_token_whose_login_is_unusable_falls_back_to_the_org_dashboard(
    gh, monkeypatch
):
    # /user answering without a usable login leaves no username, so the run
    # reads the public organisation events.
    monkeypatch.setattr(settings, "github_token", TOKEN)
    gh.fake.routes[("api.github.com", "/user")] = httpx.Response(
        200, json={"login": "   ", "id": 1}
    )
    gh.fake.routes[("api.github.com", "/orgs/acme/events")] = httpx.Response(
        200, json=[]
    )

    result = github.ingest_github_org_events(gh.db, "acme")

    assert result["mode"] == "org"
    assert gh.fake.paths()[-1] == ("api.github.com", "/orgs/acme/events")


def test_repository_enumeration_stops_at_a_malformed_page(gh):
    # A repository listing that is no list ends enumeration; the repositories
    # already read are kept and no further page is requested.
    pages = []

    def repos(request):
        pages.append(request.url.params["page"])
        if request.url.params["page"] == "1":
            return httpx.Response(200, json=[{"full_name": "acme/app"}])
        return httpx.Response(200, json={"message": "unexpected"})

    gh.fake.routes[("api.github.com", "/orgs/acme/repos")] = repos

    with github._client() as client:
        listed = github._list_org_repos(client, "acme", per_page=1, max_pages=5)

    assert listed == [{"full_name": "acme/app"}]
    assert pages == ["1", "2"]


def test_audit_summaries_name_the_repository_an_entry_concerns(audit):
    moment = _recently()
    audit.serve([{**_audit(1, moment, user="alice"), "repo": "acme/app"}])

    github.ingest_github_org_auditlog(audit.db, "acme")

    (event,) = audit.events()
    # The actor is named once, so a target equal to the actor is left out.
    assert event.summary == "github org acme org.add_member repo=acme/app by alice"


def test_feed_entries_without_any_link_store_no_repository(gh):
    feed = "https://feeds.example/bare.atom"
    gh.fake.routes[("feeds.example", "/bare.atom")] = httpx.Response(
        200,
        text=(
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry><id>tag:github.com,2008:push/9</id><title>no link</title>"
            "<updated>2099-01-03T00:00:00Z</updated></entry></feed>"
        ),
    )

    result = github.ingest_github_atom_feed(gh.db, feed)

    assert result["created_events"] == 1
    (event,) = gh.events()
    pointer = event.raw_pointer["github"]
    assert pointer["link"] is None and pointer.get("repo") is None
    assert event.summary == "github feed: no link"
