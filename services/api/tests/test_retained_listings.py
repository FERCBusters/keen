"""Collectors keep no more upstream data across pages than one response allows.

A collector that keeps a listing's pages until it has them all holds every
page parsed at once, and a parsed page costs many times its size
(tests/test_parse_memory_budget.py measures the costliest shapes). Each such
listing therefore reads its pages inside ``app.ingest.http.retained_listing``,
whose allowance is one response's limit, or reduces each page to a few fields
before reading the next.

``test_every_page_keeping_loop_is_bounded`` finds each loop in the collectors
that fetches pages and keeps what they hold, and fails when one is neither
bounded by an allowance nor listed below with the reason it needs none.
"""

import ast
import json
import pathlib
import socket
import subprocess
import sys
from datetime import datetime, timedelta

import httpx
import pytest
from app.ingest import http

from tests.test_parse_memory_budget import BUDGET_MIB

API = pathlib.Path(__file__).resolve().parents[1]
APP = API / "app"

# Page-keeping loops outside an allowance, and why their memory stays bounded.
CLOUD = "the provider's own API, bounded by its page limit (SECURITY.md)"
UNBOUNDED_BY_DESIGN = {
    ("ingest/bookstack.py", "_list_page_items"): "runs inside _list_pages' allowance",
    ("ingest/elasticsearch.py", "_window"): "stores each page before the next",
    ("ingest/gitea_compatible.py", "_discover_repositories"): "keeps names only",
    ("ingest/github.py", "_list_org_repos"): "keeps names only",
    ("ingest/github.py", "ingest_github_org_auditlog"): "stores each page first",
    ("ingest/loki.py", "ingest_loki_once"): "stores each page before the next",
    ("ingest/cloud/azure.py", "subscriptions"): "keeps a few fields of each",
    ("ingest/cloud/azure.py", "listing"): CLOUD,
    ("ingest/cloud/digitalocean.py", "activity"): CLOUD,
    ("ingest/cloud/digitalocean.py", "listing"): CLOUD,
    ("ingest/cloud/linode.py", "listing"): CLOUD,
    ("ingest/cloud/scaleway.py", "_events"): CLOUD,
    ("ingest/cloud/scaleway.py", "pages"): CLOUD,
}


def _fetches(node) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr == "json" and not node.args and not node.keywords:
        return True
    return node.func.attr in {"get", "post", "send", "request"} and any(
        k.arg == "params" for k in node.keywords
    )


def _keeps(node) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"extend", "append"}
    )


def _opens_allowance(function) -> bool:
    return any(
        isinstance(n, ast.Call)
        and (getattr(n.func, "attr", None) or getattr(n.func, "id", None))
        == "retained_listing"
        for n in ast.walk(function)
    )


def page_keeping_loops(source: str, filename: str):
    """(file, function, bounded) for each function whose loop fetches and keeps."""
    found = set()
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(function)):
            continue  # a generator hands each page on before reading the next
        for loop in ast.walk(function):
            if isinstance(loop, (ast.For, ast.While)):
                nodes = list(ast.walk(loop))
                if any(map(_fetches, nodes)) and any(map(_keeps, nodes)):
                    found.add((filename, function.name, _opens_allowance(function)))
                    break
    return found


def test_every_page_keeping_loop_is_bounded():
    found = set()
    for sub in ("ingest", "integrations"):
        for path in sorted((APP / sub).rglob("*.py")):
            found |= page_keeping_loops(
                path.read_text(), path.relative_to(APP).as_posix()
            )
    open_ended = sorted(
        (file, name)
        for file, name, bounded in found
        if not bounded and (file, name) not in UNBOUNDED_BY_DESIGN
    )
    assert not open_ended, (
        "These collectors keep pages across a listing without an allowance; "
        "read them inside app.ingest.http.retained_listing, or reduce each "
        f"page before the next and list the reason here: {open_ended}"
    )
    names = {(file, name) for file, name, _ in found}
    assert not set(UNBOUNDED_BY_DESIGN) - names, "remove entries no longer found"


def test_the_inventory_detects_the_accumulating_forge_listing():
    source = (
        "def _fetch_pages(c, path):\n"
        "    items = []\n"
        "    for page in range(1, 4):\n"
        "        items.extend(c.get(path, params={'page': page}).json())\n"
        "    return items\n"
    )
    assert page_keeping_loops(source, "ingest/x.py") == {
        ("ingest/x.py", "_fetch_pages", False)
    }


def _used():
    return [used for used, _ in http._listings.get()]


def test_nested_allowances_count_together_and_release_on_exit():
    with http.retained_listing(100):
        http._retain(30)
        with http.retained_listing():
            http._retain(50)
            assert _used() == [80, 50]
        assert _used() == [30]  # the inner listing was released
        # An inner listing that overflows the outer one is refused, and every
        # byte it counted is released again, whichever allowance refused it.
        with pytest.raises(
            http.ListingTooLarge,
            match=r"^Listing exceeds the upstream data one collection holds \(0 MiB\); narrow the input$",
        ):
            with http.retained_listing():
                http._retain(40)
                http._retain(40)
        assert _used() == [30]
        with pytest.raises(
            http.ListingTooLarge,
            match=r"^Listing exceeds the upstream data one collection holds \(0 MiB\); narrow the input$",
        ):
            with http.retained_listing(10):
                http._retain(11)
        assert _used() == [30]
    assert http._listings.get() == ()


# --- Through the real transport ----------------------------------------------

PAGE = 256 * 1024


@pytest.fixture
def upstream(monkeypatch):
    """The real IngestionTransport, with DNS and sockets replaced in-process."""
    served = []

    def handler(request):
        served.append(request)
        # A streamed body, as a real upstream sends, passes the transport's limits.
        body = served_page(request, len(served))
        return httpx.Response(200, stream=httpx.ByteStream(body))

    def served_page(request, number):
        return upstream.page(request, number)

    upstream.page = lambda request, number: b"[]"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port))
        ],
    )
    monkeypatch.setattr(
        http.httpx, "HTTPTransport", lambda **kw: httpx.MockTransport(handler)
    )
    monkeypatch.setattr(http, "MAX_RETAINED_BYTES", 4 * PAGE)
    upstream.served = served
    return upstream


def _full_page(count, number=0, size=PAGE):
    """``count`` items filling about ``size`` bytes, distinct for each page."""
    pad = "x" * max(1, size // count - 40)
    return json.dumps(
        [{"id": number * count + i, "pad": pad} for i in range(count)]
    ).encode()


def test_github_events_stop_at_the_allowance(upstream, monkeypatch):
    from app.ingest import github

    monkeypatch.setattr(http, "MAX_RETAINED_BYTES", 2 * PAGE)
    upstream.page = lambda request, number: _full_page(100, number)
    with http.client(base_url="https://ghe.example.com/api/v3") as c:
        with pytest.raises(
            http.ListingTooLarge,
            match=r"^Listing exceeds the upstream data one collection holds \(0 MiB\); narrow the input$",
        ):
            github._fetch_pages(c, "/repos/o/r/events", per_page=100, max_pages=3)
    assert len(upstream.served) == 3


def test_github_repository_discovery_keeps_names_only(upstream):
    from app.ingest import github

    def page(request, number):
        rows = [
            {"full_name": f"org/r{number}-{i}", "blob": "x" * 6000, "fork": False}
            for i in range(100 if number < 6 else 10)
        ]
        return json.dumps(rows).encode()

    upstream.page = page
    with http.client(base_url="https://ghe.example.com/api/v3") as c:
        repos = github._list_org_repos(c, "org")
    # More than an allowance was read, a page at a time, and only the names
    # were kept, so a large organisation is still listed.
    assert len(repos) == 510
    assert all(set(repo) == {"full_name"} for repo in repos)


def test_a_page_holding_more_items_than_requested_is_refused(upstream):
    from app.ingest import github

    upstream.page = lambda request, number: json.dumps([{}] * 101).encode()
    with http.client(base_url="https://ghe.example.com/api/v3") as c:
        with pytest.raises(ValueError, match="more items than a page holds"):
            github._fetch_pages(c, "/repos/o/r/events", per_page=100, max_pages=3)


def _gitea():
    from app.ingest.gitea_compatible import GiteaCompatibleCollector

    collector = GiteaCompatibleCollector("gitea", "Gitea")
    collector._auth_headers = lambda: {}
    collector._basic_auth = lambda: None
    collector._auth_mode = lambda: ""
    return collector


def test_gitea_activity_stops_at_the_allowance(upstream):
    upstream.page = lambda request, number: _full_page(50, number)
    with pytest.raises(
        http.ListingTooLarge,
        match=r"^Listing exceeds the upstream data one collection holds \(1 MiB\); narrow the input$",
    ):
        _gitea()._fetch_api_activities(
            base_url="https://git.example.com", owner="o", repo="r", max_pages=50
        )
    assert len(upstream.served) == 5


def test_gitea_activity_paging_stops_at_the_cursor(upstream):
    start = datetime(2026, 1, 1)

    def page(request, number):
        # Newest first: page n holds the 50 activities before page n-1's.
        rows = [
            {
                "id": number * 100 + i,
                "created": (start - timedelta(hours=number * 50 + i)).isoformat() + "Z",
            }
            for i in range(50)
        ]
        return json.dumps(rows).encode()

    upstream.page = page
    items = _gitea()._fetch_api_activities(
        base_url="https://git.example.com",
        owner="o",
        repo="r",
        stop_at=start - timedelta(hours=120),
    )
    # Page 1 is newer than the cursor; page 2 passes it, so paging stops.
    assert len(upstream.served) == 2
    assert len(items) == 100


def test_gitlab_events_are_stored_and_committed_a_page_at_a_time(upstream, monkeypatch):
    from unittest.mock import MagicMock

    from app.ingest import gitlab

    def page(request, number):
        if number > 3:
            return b"[]"
        rows = [
            {
                "id": number * 100 + i,
                "created_at": f"2026-10-0{number}T00:00:{i:02d}Z",
                "action_name": "pushed",
            }
            for i in range(50)
        ]
        return json.dumps(rows).encode()

    upstream.page = page
    held = []
    monkeypatch.setattr(
        gitlab,
        "store_event_with_artifact",
        lambda db, **kw: held.append(len(upstream.served)) or {"deduped": False},
    )
    monkeypatch.setattr(gitlab, "utc_now_naive", lambda: datetime(2026, 10, 7))
    db = MagicMock()
    db.query.return_value.filter_by.return_value.one_or_none.return_value = None
    commits = []
    db.commit.side_effect = lambda: commits.append(len(upstream.served))
    with http.client() as c:
        result = gitlab._project_events(
            db, c, "https://gitlab.example.com", "groups", "g", {"id": 9}, None
        )
    assert result["created_events"] == 150
    # Each page's events are stored and committed before the next is read.
    assert held == [1] * 50 + [2] * 50 + [3] * 50
    assert commits == [1, 2, 3]


def test_gitlab_project_discovery_keeps_references_only(upstream):
    from app.ingest import gitlab

    def page(request, number):
        if number > 8:
            return b"[]"
        rows = [
            {"id": number * 100 + i, "path_with_namespace": f"g/p{i}", "x": "y" * 3000}
            for i in range(100)
        ]
        return json.dumps(rows).encode()

    upstream.page = page
    with http.client() as c:
        projects = gitlab._pages(
            c,
            "https://gitlab.example.com",
            "groups/g/projects",
            keep=gitlab._project_reference,
        )
    # More than the allowance was read, a page at a time, and only the
    # references were kept.
    assert len(upstream.served) == 9 and len(projects) == 800
    assert all(set(p) == {"id", "path_with_namespace"} for p in projects)


def test_bookstack_page_listings_stop_at_the_allowance(upstream):
    from app.ingest import bookstack

    upstream.page = lambda request, number: json.dumps(
        {"data": json.loads(_full_page(100, number))}
    ).encode()
    with http.client(base_url="https://books.example.com") as c:
        with pytest.raises(
            http.ListingTooLarge,
            match=r"^Listing exceeds the upstream data one collection holds \(1 MiB\); narrow the input$",
        ):
            bookstack._list_pages(
                c,
                since_ts=None,
                until_ts=None,
                book_id=1,
                book_slug=None,
                count=100,
                include_drafts=False,
                include_templates=False,
                max_pages=100000,
            )
    assert len(upstream.served) == 5


_LISTING = """
import json, resource, socket, sys
sys.path.insert(0, {api!r})
import httpx
from app.ingest import http
SIZE = http.MAX_RESPONSE_BYTES // 4 - 64
unit = b"{{}},"
body = b"[" + unit * ((SIZE - 32) // len(unit)) + b"{{}}]"
socket.getaddrinfo = lambda host, port, *a, **k: [
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port))
]
http.httpx.HTTPTransport = lambda **kw: httpx.MockTransport(
    lambda request: httpx.Response(200, stream=httpx.ByteStream(body))
)
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
pages = []
refused = False
try:
    with http.client(base_url="https://ghe.example.com/api/v3") as c, http.retained_listing():
        while True:
            response = c.get("/repos/o/r/events")
            pages.append(response.json())
except http.ListingTooLarge:
    refused = True
after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({{"pages": len(pages), "refused": refused,
                   "growth_mib": (after - before) / 1024}}))
"""


def test_the_costliest_listing_the_allowance_admits_fits_the_worker():
    # Quarter-size pages of the costliest JSON shape, kept until the listing
    # allowance refuses one: everything kept parses within the worker budget.
    result = subprocess.run(
        [sys.executable, "-I", "-c", _LISTING.format(api=str(API))],
        capture_output=True,
        text=True,
        timeout=300,
        env={"KEEN_PUBLIC_BASE_URL": "https://keen.example", "PATH": "/usr/bin"},
    )
    assert result.returncode == 0, result.stderr[-2000:]
    measured = json.loads(result.stdout)
    assert measured["refused"] and measured["pages"] == 4, measured
    assert measured["growth_mib"] < BUDGET_MIB, measured
