"""BookStack poller contracts against simulated BookStack REST payloads.

The payloads mirror real BookStack API shapes (list items, full page details,
books and tags) while keeping every response offline and bounded.
"""

import contextlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.ingest import bookstack as bs
from app.security.regex import compile_pattern

PAGE_LIST_ITEM = {
    "id": 11,
    "name": "Access Control Policy",
    "slug": "access-control-policy",
    "book_id": 7,
    "chapter_id": None,
    "draft": False,
    "template": False,
    "revision_count": 3,
    "editor": "wysiwyg",
    "created_at": "2026-09-01T09:00:00.000000Z",
    "updated_at": "2026-10-09T12:30:00.000000Z",
}

PAGE_FULL = dict(
    PAGE_LIST_ITEM,
    book={"id": 7, "name": "ISMS", "slug": "isms"},
    created_by={"id": 1, "name": "Founder"},
    updated_by={"id": 2, "name": "Ops User"},
    html="<h1>Access Control Policy</h1>\n<script>alert('no')</script>"
    "<p>Users must &amp; shall be approved.</p>",
    markdown=None,
    tags=[{"name": "ism", "value": "yes"}],
)

PAGE_FULL_MARKDOWN = dict(PAGE_FULL, markdown="# Mk heading\n\nplain line", html=None)

# Theoretical BookStack /api/system metadata payload.
SYSTEM_INFO = {"app_name": "Docs.example", "base_url": "https://docs.example.com"}


@pytest.fixture
def db():
    session = MagicMock()
    session.query.return_value.filter.return_value.one_or_none.return_value = None
    return session


class BookStackApi:
    """Simulated upstream combining catalog, listing and detail endpoints."""

    def __init__(self, *, list_items, full_pages, books=None, system=None):
        self.list_items = list_items
        self.full_pages = full_pages
        self.books = books or [{"id": 7, "name": "ISMS", "slug": "isms"}]
        self.system = system if system is not None else SYSTEM_INFO
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/system":
            return httpx.Response(200, json=self.system)
        if path == "/api/books":
            self.requests.append(request)
            slug = request.url.params.get("filter[slug]")
            matching = (
                [b for b in self.books if b["slug"].lower() == slug.lower()]
                if slug is not None
                else self.books
            )
            return httpx.Response(200, json={"data": matching})
        if path == "/api/pages":
            self.requests.append(request)
            slug = request.url.params.get("filter[slug]")
            name = request.url.params.get("filter[name]")
            offset = int(request.url.params.get("offset", 0))
            count = int(request.url.params.get("count", "100"))
            window = [p for p in self.list_items if p.get("_in_window", True)]
            if slug is not None:
                window = [p for p in window if p["slug"] == slug]
            if name is not None:
                window = [p for p in window if p["name"] == name]
            data = window[offset : offset + count]
            return httpx.Response(200, json={"data": data, "total": len(window)})
        if path.startswith("/api/pages/"):
            self.requests.append(request)
            page_id = int(path.rsplit("/", 1)[-1])
            full = self.full_pages.get(page_id)
            if full is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json=full)
        return httpx.Response(404, json={"error": "unknown"})


@pytest.fixture
def env():
    def build(*, list_items, full_pages, books=None, system=None):
        api = BookStackApi(
            list_items=list_items, full_pages=full_pages, books=books, system=system
        )

        def fake_client(**kwargs):
            return contextlib.nullcontext(
                httpx.Client(
                    transport=httpx.MockTransport(api.handler),
                    base_url="https://docs.example.com",
                    headers=kwargs.get("headers") or {},
                    follow_redirects=False,
                )
            )

        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(bs, "ingestion_client", fake_client))
        stack.enter_context(patch.object(bs, "is_safe_url", return_value=True))
        stack.enter_context(patch.object(bs, "connection_cache", return_value={}))
        stack.enter_context(
            patch.object(bs.settings, "bookstack_base_url", "https://docs.example.com")
        )
        stack.enter_context(
            patch.object(bs.settings, "bookstack_token_id", "fixture-id")
        )
        stack.enter_context(
            patch.object(bs.settings, "bookstack_token_secret", "fixture-secret")
        )
        return stack, api

    return build


def test_timestamp_parsing_and_formatting():
    assert bs._parse_bookstack_ts("2026-10-09T12:30:00.000000Z") == datetime(
        2026, 10, 9, 12, 30
    )
    assert bs._parse_bookstack_ts("2026-10-09T12:30:00") == datetime(
        2026, 10, 9, 12, 30
    )
    for value in (None, "", "   ", 99, "not a date"):
        assert bs._parse_bookstack_ts(value) is None
    assert bs._to_iso_z(datetime(2026, 10, 9, 12, 30)) == "2026-10-09T12:30:00Z"
    assert bs._to_iso_z(datetime(2026, 10, 9, 23)) == "2026-10-09T23:00:00Z"
    east = datetime(2026, 10, 9, 15, tzinfo=timezone(timedelta(hours=3)))
    assert bs._to_iso_z(east) == "2026-10-09T12:00:00Z"


def test_client_configuration(env):
    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return "client"

    with (
        patch.object(bs.settings, "bookstack_base_url", "https://docs.example.com"),
        patch.object(bs.settings, "bookstack_token_id", "fixture-id"),
        patch.object(bs.settings, "bookstack_token_secret", "fixture-secret"),
        patch.object(bs, "ingestion_client", fake_client),
        patch.object(bs, "is_safe_url", return_value=True),
    ):
        assert bs._client() == "client"
    assert captured["base_url"] == "https://docs.example.com"
    assert captured["headers"]["Authorization"] == "Token fixture-id:fixture-secret"
    assert captured["verify"] is True

    with pytest.raises(ValueError):
        with (
            patch.object(bs.settings, "bookstack_base_url", ""),
            patch.object(bs.settings, "bookstack_token_id", "tid"),
            patch.object(bs.settings, "bookstack_token_secret", "s"),
        ):
            bs._client()
    with pytest.raises(ValueError):
        with (
            patch.object(bs.settings, "bookstack_base_url", "https://docs.example.com"),
            patch.object(bs.settings, "bookstack_token_id", "tid"),
            patch.object(bs.settings, "bookstack_token_secret", "  "),
            patch.object(bs, "is_safe_url", return_value=True),
        ):
            bs._client()
    with pytest.raises(ValueError):
        with (
            patch.object(bs.settings, "bookstack_base_url", "https://docs.example.com"),
            patch.object(bs, "is_safe_url", return_value=False),
            patch.object(bs.settings, "bookstack_token_id", "tid"),
            patch.object(bs.settings, "bookstack_token_secret", "s"),
        ):
            bs._client()


def test_load_bookstack_config(tmp_path):
    with (
        patch.object(bs.settings, "bookstack_config_path", "missing.yml"),
        patch.object(bs, "load_document", side_effect=FileNotFoundError("nope")),
    ):
        assert bs.load_bookstack_config("missing.yml") == {}
    with (
        patch.object(bs.settings, "bookstack_config_path", "other.yml"),
        patch.object(bs, "load_document", return_value=["not", "a", "dict"]),
    ):
        assert bs.load_bookstack_config("other.yml") == {}
    with (
        patch.object(bs.settings, "bookstack_config_path", "doc.yml"),
        patch.object(bs, "load_document", return_value={"label": "ISMS"}),
    ):
        assert bs.load_bookstack_config("doc.yml") == {"label": "ISMS"}


def test_slug_scoping_helpers():
    assert bs._extract_book_slug({"book_slug": " isms "}) == "isms"
    assert bs._extract_book_slug({"book": {"slug": "hr-docs"}}) == "hr-docs"
    assert bs._extract_book_slug({"book": {"book_slug": "other"}}) == "other"
    assert bs._extract_book_slug({"book": "not-a-dict"}, {"book_slug": 3}) is None
    assert bs._extract_book_slug("junk", None) is None
    page = bs._stamp_book_scope({"id": 1}, book_id=7, book_slug="isms")
    assert page == {"id": 1, "book_id": 7, "book_slug": "isms"}
    preserved = bs._stamp_book_scope(
        {"id": 1, "book_slug": "hr", "book_id": None}, book_id=7, book_slug="isms"
    )
    assert preserved["book_slug"] == "hr"
    assert preserved["book_id"] == 7
    assert bs._stamp_book_scope("junk", book_id=7, book_slug="isms") == "junk"
    assert bs._nested_dict({"a": {"b": 1}}, "a") == {"b": 1}
    assert bs._nested_dict({"a": "x"}, "a") == {}
    assert bs._nested_dict("junk", "a") == {}
    assert bs._book_lookup_candidates({"data": [{"id": 1}, "junk"]}) == [{"id": 1}]
    assert bs._book_lookup_candidates([{"id": 2}]) == [{"id": 2}]
    assert bs._book_lookup_candidates({"data": "junk"}) == []


def test_url_builders():
    assert (
        bs._page_url("https://docs.example.com/", "isms", "page-slug")
        == "https://docs.example.com/books/isms/page/page-slug"
    )
    assert bs._page_url("", "isms", "page-slug") is None
    assert bs._page_url("https://docs.example.com", " ", "page-slug") is None
    assert bs._page_direct_url("https://docs.example.com", 11) == (
        "https://docs.example.com/link/11"
    )
    assert bs._page_direct_url("https://docs.example.com", 0) is None
    assert bs._page_direct_url("", 11) is None
    assert bs._page_direct_url("https://docs.example.com", "x") is None
    assert bs._page_direct_url("https://docs.example.com", -1) is None


def test_html_stripping_and_previews():
    text = bs._strip_html_to_text(PAGE_FULL["html"])
    assert "alert" not in text
    assert "Users must & shall be approved." in text
    assert bs._strip_html_to_text("") == ""
    long_tag = "<p>" + "x" * 70000 + "</p>"
    stripped = bs._strip_html_to_text(long_tag)
    assert 0 < len(stripped) <= 70000

    text, source = bs._make_preview(PAGE_FULL_MARKDOWN, 500)
    assert source == "markdown"
    assert text.startswith("# Mk heading")
    text, source = bs._make_preview(PAGE_FULL, 500)
    assert source == "html"
    assert "shall be approved" in text
    text, _ = bs._make_preview(PAGE_FULL, 20)
    assert len(text) == 20
    text, _ = bs._make_preview({"raw_html": "<p>t&nbsp;</p>"}, 40)
    assert text == "t"
    text, _ = bs._make_preview({"html": "<p>t</p>"}, "not-a-number")
    assert text == "t"
    assert bs._make_preview({}, None) == ("", "html")


def test_a_stored_page_event_gives_the_fields_page_mappings_match():
    ev = MagicMock()
    ev.normalized_payload = {
        "bookstack": {
            "page_id": 11,
            "page_slug": " access-control-policy ",
            "page_title": "Access Control Policy",
            "book_slug": "isms",
        }
    }
    ev.raw_pointer = {"bookstack": {"page_id": 11, "book_id": 4}}

    payload, copy = bs._event_bookstack_page_payloads(ev)

    assert payload == {
        "id": 11,
        "book_id": 4,
        "slug": "access-control-policy",
        "name": "Access Control Policy",
        "book_slug": "isms",
    }
    assert copy == payload and copy is not payload
    # Remapping honours a mapping scoped by book ID, slug, title or page ID.
    mappings = bs._parse_page_mappings(
        {
            "page_mappings": [
                {
                    "match": {"book_id": 4, "slug": "access-control-policy"},
                    "map_to": ["A"],
                },
                {
                    "match": {"book_slug": "isms", "title": "access control policy"},
                    "map_to": ["B"],
                },
                {"match": {"id": 11}, "map_to": ["C"]},
                {
                    "match": {"book_id": 5, "slug": "access-control-policy"},
                    "map_to": ["D"],
                },
                {"match": {"slug": "isms-access-control-policy"}, "map_to": ["E"]},
            ]
        }
    )
    assert [m.map_to for m in mappings if m.matches(payload, copy)] == [
        ["A"],
        ["B"],
        ["C"],
        ["E"],
    ]


@pytest.mark.parametrize(
    "normalized,pointer",
    [
        (
            {
                "bookstack": {
                    "page_id": "11",
                    "page_slug": " ",
                    "page_title": None,
                    "book_slug": 7,
                }
            },
            {"bookstack": {"book_id": True}},
        ),
        ({"bookstack": "page"}, {"bookstack": ["book"]}),
        (None, "pointer"),
    ],
    ids=["other-types", "other-shapes", "absent"],
)
def test_page_fields_an_event_lacks_stay_out(normalized, pointer):
    ev = MagicMock()
    ev.normalized_payload, ev.raw_pointer = normalized, pointer

    assert bs._event_bookstack_page_payloads(ev) == ({}, {})


def test_resolve_book_id_uses_filtered_lookup_then_caches(env):
    stack, api = env(list_items=[], full_pages={})
    with stack:
        client = httpx.Client(
            transport=httpx.MockTransport(api.handler),
            base_url="https://docs.example.com",
        )
        assert bs._resolve_book_id(client, None) is None
        assert bs._resolve_book_id(client, " Isms ") == 7
        with patch.object(
            bs, "connection_cache", return_value={"bookstack_book_ids": {"isms": 7}}
        ):
            cached_client = MagicMock()
            assert bs._resolve_book_id(cached_client, "ISMS") == 7
            cached_client.get.assert_not_called()

    def resolves_without_filter(request):
        if request.url.params.get("filter[slug]"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(
            200,
            json={"data": [{"id": 42, "slug": "HR-docs"}, {"id": 43, "slug": "no"}]},
        )

    with (
        patch.object(bs, "is_safe_url", return_value=True),
        patch.object(
            bs,
            "ingestion_client",
            lambda **kw: contextlib.nullcontext(
                httpx.Client(transport=httpx.MockTransport(resolves_without_filter))
            ),
        ),
        patch.object(bs, "connection_cache", return_value={"bookstack_book_ids": {}}),
    ):
        scoped = httpx.Client(
            transport=httpx.MockTransport(resolves_without_filter),
            base_url="https://docs.example.com",
        )
        assert bs._resolve_book_id(scoped, "hr-docs") == 42

    def broken_id(request):
        return httpx.Response(200, json={"data": [{"id": "not-int", "slug": "isms"}]})

    with (
        patch.object(bs, "is_safe_url", return_value=True),
        patch.object(
            bs,
            "ingestion_client",
            lambda **kw: contextlib.nullcontext(
                httpx.Client(transport=httpx.MockTransport(broken_id))
            ),
        ),
        patch.object(bs, "connection_cache", return_value={}),
    ):
        cache = {"bookstack_book_ids": {}}
        with patch.object(bs, "connection_cache", return_value=cache):
            scoped = httpx.Client(
                transport=httpx.MockTransport(broken_id),
                base_url="https://docs.example.com",
            )
            assert bs._resolve_book_id(scoped, "isms") is None
            assert cache["bookstack_book_ids"]["isms"] is None

    def failing_api(request):
        return httpx.Response(503)

    with (
        patch.object(bs, "is_safe_url", return_value=True),
        patch.object(
            bs,
            "ingestion_client",
            lambda **kw: contextlib.nullcontext(
                httpx.Client(transport=httpx.MockTransport(failing_api))
            ),
        ),
    ):
        cache = {}
        with patch.object(bs, "connection_cache", return_value=cache):
            assert bs._resolve_book_id(httpx.Client(), "isms") is None
            assert cache["bookstack_book_ids"]["isms"] is None


def test_parse_page_mappings_variants():
    cfg = {
        "page_mappings": [
            {
                "match": {"book_slug": "ISMS", "slug": "access"},
                "map_to": "A.9, , B.1",
                "confidence": "bad",
                "rationale": "policy map",
            },
            {
                "match": {"book": {"id": "9", "slug": "hr"}, "page_slug": "onboarding"},
                "controls": ["A.5"],
            },
            {"map_to": ["A.1"]},  # missing match criteria entirely: still parses
            {"match": {"slug_regex": "("}, "map_to": ["A.2"]},  # invalid regex
            {"match": {"title_regex": "("}, "map_to": ["A.2"]},  # invalid regex
            "junk",
            {"match": {"id": "41"}, "map_to": ["A.6"]},
            {
                "match": {"title": "Login Policy", "name_regex": "^bad("},
                "map_to": ["A.3"],
            },
        ]
    }
    mappings = bs._parse_page_mappings(cfg)
    assert len(mappings) == 4
    first = mappings[0]
    assert first.match_book_slug == "ISMS"
    assert first.match_slug == "access"
    assert first.map_to == ["A.9", "B.1"]
    assert first.confidence == 0.95
    assert first.rationale == "policy map"
    second = mappings[1]
    assert second.match_book_id == 9
    assert second.match_book_slug == "hr"
    assert second.match_slug == "onboarding"
    assert second.map_to == ["A.5"]
    third = mappings[2]
    # An entry without match criteria matches every page.
    assert third.map_to == ["A.1"] and third.match_id is None
    fourth = mappings[3]
    assert fourth.match_id == 41 and fourth.map_to == ["A.6"]

    assert bs._parse_page_mappings({"page_mappings": "junk"}) == []
    assert bs._parse_page_mappings({}) == []

    named = bs._parse_page_mappings(
        {"pages": [{"match": {"slug": "s"}, "map_to": ["A.1"], "confidence": 1.2}]}
    )
    assert named[0].confidence == 1.2
    titled_regex = bs._parse_page_mappings(
        {
            "pages": [
                {"match": {"name_regex": "policy$"}, "map_to": ["A.4"]},
            ]
        }
    )
    assert titled_regex[0].match_title_regex is compile_pattern("policy$")
    book_names = bs._parse_page_mappings(
        {"pages": [{"match": {"book": "hr-docs"}, "map_to": ["A.5"]}]}
    )
    assert book_names[0].match_book_slug == "hr-docs"


def test_page_mapping_matches():
    mapping = bs._parse_page_mappings(
        {
            "page_mappings": [
                {
                    "match": {"book_slug": "isms", "slug": "access-control-policy"},
                    "map_to": ["A.9"],
                }
            ]
        }
    )[0]
    assert mapping.matches(PAGE_LIST_ITEM, PAGE_FULL) is True
    combined = mapping.with_confidence if False else None
    # A different book scope fails the match.
    other = bs._parse_page_mappings(
        {"page_mappings": [{"match": {"book_id": 8}, "map_to": ["A.1"]}]}
    )[0]
    assert other.matches(PAGE_LIST_ITEM, PAGE_FULL) is False
    title_mapping = bs._parse_page_mappings(
        {
            "page_mappings": [
                {"match": {"title": "ACCESS CONTROL POLICY"}, "map_to": ["A.1"]}
            ]
        }
    )[0]
    assert title_mapping.matches(PAGE_LIST_ITEM, PAGE_FULL) is True
    regex_mapping = bs._parse_page_mappings(
        {"page_mappings": [{"match": {"slug_regex": "^hr-docs-"}, "map_to": ["A.1"]}]}
    )[0]
    hr_page = dict(PAGE_LIST_ITEM, slug="policies", book_slug="hr-docs")
    # Combined slug hr-docs-policies matches the composer prefix rule.
    assert regex_mapping.matches(hr_page, {"book_slug": "hr-docs"}) is True
    exact = bs._parse_page_mappings(
        {"page_mappings": [{"match": {"id": 11}, "map_to": ["A.1"]}]}
    )[0]
    assert exact.matches(PAGE_LIST_ITEM, PAGE_FULL) is True
    missing = bs._parse_page_mappings(
        {"page_mappings": [{"match": {"id": 12}, "map_to": ["A.1"]}]}
    )[0]
    assert missing.matches(PAGE_LIST_ITEM, PAGE_FULL) is False
    prefix = bs._parse_page_mappings(
        {"page_mappings": [{"match": {"slug": "hr-docs-policies"}, "map_to": ["A.1"]}]}
    )[0]
    assert prefix.matches(hr_page, {}) is True
    # Missing page criteria combine as an all-pass mapping.
    catch_all = bs._parse_page_mappings({"page_mappings": [{"map_to": ["A.1"]}]})[0]
    assert catch_all.matches({}, {}) is True


def test_ensure_and_add_mappings(db):
    existing = MagicMock()
    existing.ref = "A.9"
    existing.id = "ci-1"
    query_control = MagicMock()
    query_control.filter.return_value.all.return_value = [existing]
    query_map = MagicMock()
    query_map.filter.return_value.scalar.return_value = None  # not yet mapped
    another_map = MagicMock()
    another_map.filter.return_value.scalar.return_value = None
    db.query.side_effect = [query_control, query_map, another_map]
    created = bs._ensure_and_add_mappings(
        db,
        event_id="ev-1",
        refs=[" A.9 ", "", "B.2", "A.9"],
        confidence=0.7,
        method="config",
        rationale="test rationale",
    )
    assert created == 2
    db.commit.assert_called_once()
    added_kinds = [type(call.args[0]).__name__ for call in db.add.call_args_list]
    assert added_kinds.count("Mapping") == 2
    assert added_kinds.count("ControlItem") == 1

    # Duplicate mappings are skipped and no commit happens.
    query_three = MagicMock()
    query_three.filter.return_value.scalar.return_value = 1
    db.query.side_effect = [query_control, query_three]
    db.add.reset_mock()
    db.commit.reset_mock()
    assert (
        bs._ensure_and_add_mappings(
            db,
            event_id="ev-1",
            refs=["A.9"],
            confidence=0.7,
            method="config",
            rationale="r",
        )
        == 0
    )
    db.commit.assert_not_called()

    assert (
        bs._ensure_and_add_mappings(
            db,
            event_id="ev-1",
            refs=["  ", ""],
            confidence=0.7,
            method="config",
            rationale="r",
        )
        == 0
    )


def test_apply_bookstack_config_mappings(db):
    ev = MagicMock()
    ev.source = "not-bookstack"
    with patch.object(bs, "_parse_page_mappings", return_value=[]):
        assert bs.apply_bookstack_config_mappings(db, ev) == 0

    ev.source = "BookStack "
    ev.normalized_payload = {
        "bookstack": {"page_slug": "access-control-policy", "book_slug": "isms"}
    }
    ev.raw_pointer = {}
    ev.system = ""
    ev.external_id = "page:11:z"
    ev.summary = ""
    mapping = MagicMock()
    mapping.matches.return_value = True
    mapping.map_to = ["A.9"]
    mapping.confidence = 0.8
    mapping.rationale = "policy"
    with (
        patch.object(bs, "load_bookstack_config", return_value={}),
        patch.object(bs, "_parse_page_mappings", return_value=[]),
    ):
        assert bs.apply_bookstack_config_mappings(db, ev) == 0

    db.query.side_effect = None
    db.query.reset_mock()
    q1 = MagicMock()
    q1.filter.return_value.all.return_value = []
    q2 = MagicMock()
    q2.filter.return_value.scalar.return_value = None
    db.query.side_effect = [q1, q2]
    with (
        patch.object(bs, "load_bookstack_config", return_value={}),
        patch.object(bs, "_parse_page_mappings", return_value=[mapping]),
    ):
        assert bs.apply_bookstack_config_mappings(db, ev) == 1
    mapping.matches.assert_called_once()
    db.commit.assert_called_once()


def connections_env():
    from app.ingest.connections import Connection

    connection = Connection(
        "env:bookstack",
        "bookstack",
        "Environment default",
        {},
        {},
        None,
        True,
        "environment",
    )

    @contextlib.contextmanager
    def fake_run_lock(db, connection_id):
        yield True

    return patch(
        "app.ingest.connections.connections", return_value=[connection]
    ), patch("app.ingest.connections.run_lock", fake_run_lock)


MD_LIST_ITEM = {
    "id": 44,
    "name": "Employment Contract",
    "slug": "employment-contract",
    "book_id": 7,
    "chapter_id": None,
    "draft": False,
    "template": False,
    "revision_count": 1,
    "editor": "markdown",
    "created_at": "2026-06-01T08:00:00.000000Z",
    "updated_at": "2026-10-08T10:00:00.000000Z",
}


def test_ingest_all_stores_page_events_and_config_mappings(db, env):
    stack, api = env(
        list_items=[PAGE_LIST_ITEM, MD_LIST_ITEM],
        full_pages={11: PAGE_FULL, 44: PAGE_FULL_MARKDOWN},
    )
    cfg = {
        "label": "ISMS-docs",
        "poll": {"lookback_days": 5, "count": 50},
        "book": {"slug": "isms"},
        "capture": {"preview_chars": 60},
        "page_mappings": [
            {
                "match": {"book_slug": "isms", "slug": "access-control-policy"},
                "map_to": ["A.9.1"],
            }
        ],
        "selected_pages": [{"id": 99, "book_id": 8}],
    }
    stored_calls = []

    def store(db, **kwargs):
        stored_calls.append(kwargs)
        return {"deduped": False, "event_id": f"ev-{len(stored_calls)}"}

    with contextlib.ExitStack() as total:
        total.enter_context(stack)
        connections, run_lock = connections_env()
        total.enter_context(connections)
        total.enter_context(run_lock)
        total.enter_context(patch.object(bs, "load_bookstack_config", return_value=cfg))
        total.enter_context(patch.object(bs.settings, "bookstack_enabled", True))
        total.enter_context(patch.object(bs, "store_event_with_artifact", store))
        total.enter_context(
            patch.object(bs, "_ensure_and_add_mappings", return_value=2)
        )
        results = bs.ingest_bookstack_all(db)

    summary_row = results[0]
    assert summary_row["ok"] is True
    assert summary_row["source"] == "bookstack"
    assert summary_row["instance"] == "Docs.example"
    assert summary_row["created"] == 2
    assert summary_row["config_mappings_created"] == 2
    assert summary_row["deduped"] == 0
    # The selected page has no full detail upstream; collection stays tolerant.
    assert summary_row["errors"] == [
        "page_id=99: Collection failed: upstream returned HTTP 404"
    ]
    assert summary_row["cursor_set_to"]
    # Store results are forwarded verbatim, merged with connection identity.
    assert [r["event_id"] for r in results[1:]] == ["ev-1", "ev-2"]
    assert all(r["connection_id"] == "env:bookstack" for r in results[1:])

    # Listing respected the resolved book scope and the poll window filters.
    listing = [
        r
        for r in api.requests
        if isinstance(r, httpx.Request)
        and r.url.path == "/api/pages"
        and "filter[updated_at:gt]" in r.url.params
    ]
    first_list_params = listing[0].url.params
    assert first_list_params["filter[book_id]"] == "7"
    assert first_list_params["filter[draft]"] == "false"
    assert first_list_params["filter[template]"] == "false"
    assert "filter[updated_at:gt]" in first_list_params
    assert "filter[updated_at:lte]" in first_list_params

    # Pages are processed in id order: 11 (listed) then 44 (also unchanged window).
    assert len(stored_calls) == 2
    by_pid = {
        call["raw_pointer"]["bookstack"]["page_id"]: call for call in stored_calls
    }

    policy_page = by_pid[11]
    assert policy_page["source"] == "bookstack"
    assert policy_page["system"] == "ISMS-docs:isms:access-control-policy"
    assert policy_page["summary"] == (
        "bookstack: updated page 'Access Control Policy' (isms-access-control-policy)"
    )
    assert policy_page["action"] == "updated"
    assert policy_page["actor"] == "Ops User"
    assert policy_page["external_id"] == "page:11:2026-10-09T12:30:00.000000Z"
    assert policy_page["timestamp"] == datetime(2026, 10, 9, 12, 30)
    assert policy_page["captured_by"] == "keen:bookstack"
    assert policy_page["raw_pointer"]["bookstack"]["url_direct"] == (
        "https://docs.example.com/link/11"
    )
    assert policy_page["normalized_payload"]["bookstack"]["instance"] == "Docs.example"
    assert len(policy_page["normalized_payload"]["bookstack"]["preview"]) <= 60
    assert policy_page["normalized_payload"]["bookstack"]["preview_source"] == "html"

    contract_page = by_pid[44]
    assert contract_page["action"] == "updated"
    assert (
        contract_page["normalized_payload"]["bookstack"]["preview_source"] == "markdown"
    )
    assert "Mk heading" in contract_page["normalized_payload"]["bookstack"]["preview"]

    snapshot = json.loads(policy_page["artifact_bytes"].decode())
    assert snapshot["instance"] == {
        "app_name": "Docs.example",
        "base_url": "https://docs.example.com",
    }
    assert snapshot["page"]["tags"] == [{"name": "ism", "value": "yes"}]
    assert snapshot["page"]["url_slug"].endswith(
        "/books/isms/page/access-control-policy"
    )
    assert policy_page["artifact_key"].startswith("bookstack/ISMS-docs/")
    assert policy_page["artifact_key"].endswith("11.json")
    assert snapshot["page"]["url_slug"].endswith(
        "/books/isms/page/access-control-policy"
    )
    assert snapshot["page"]["preview_chars"] == 60
    # Preview-only capture by default: page bodies stay out of the artifact.
    assert "password" not in policy_page["artifact_bytes"].decode()
    db.commit.assert_called()


def test_ingest_all_capture_full_content_mode(db, env):
    stack, api = env(
        list_items=[MD_LIST_ITEM],
        full_pages={44: PAGE_FULL_MARKDOWN},
    )
    cfg = {
        "label": "full-docs",
        "book": {"slug": "isms"},
        "capture": {"mode": "full"},
    }
    stored_calls = []

    def store(db, **kwargs):
        stored_calls.append(kwargs)
        return {"deduped": False, "event_id": "ev-1"}

    with contextlib.ExitStack() as total:
        total.enter_context(stack)
        connections, run_lock = connections_env()
        total.enter_context(connections)
        total.enter_context(run_lock)
        total.enter_context(patch.object(bs, "load_bookstack_config", return_value=cfg))
        total.enter_context(patch.object(bs.settings, "bookstack_enabled", True))
        total.enter_context(patch.object(bs, "store_event_with_artifact", store))
        results = bs.ingest_bookstack_all(db)
    assert results[0]["created"] == 1
    payload = json.loads(stored_calls[0]["artifact_bytes"].decode())
    # Full capture keeps every body representation in the stored snapshot.
    assert payload["page"]["markdown"].startswith("# Mk heading")
    assert payload["page"]["html"] is None  # detail omitted html for this page
    assert stored_calls[0]["artifact_kind"] == "bookstack_page"


def test_ingest_all_books_listing_and_optional_slug_filter(db, env):
    stack, api = env(list_items=[PAGE_LIST_ITEM], full_pages={11: PAGE_FULL})
    cfg = {
        "label": "books-mode",
        "books": ["isms", {"slug": "unknown-book"}],
        "poll": {"count": 10},
    }

    def store(db, **kwargs):
        return {"deduped": False, "event_id": "ev-1"}

    with contextlib.ExitStack() as total:
        total.enter_context(stack)
        connections, run_lock = connections_env()
        total.enter_context(connections)
        total.enter_context(run_lock)
        total.enter_context(patch.object(bs, "load_bookstack_config", return_value=cfg))
        total.enter_context(patch.object(bs.settings, "bookstack_enabled", True))
        total.enter_context(patch.object(bs, "store_event_with_artifact", store))
        results = bs.ingest_bookstack_all(db)
    assert results[0]["created"] == 1
    book_requests = [
        r
        for r in api.requests
        if r.url.path == "/api/books" and "filter[slug]" in r.url.params
    ]
    slugs = {r.url.params["filter[slug]"] for r in book_requests}
    assert slugs == {"isms", "unknown-book"}
    listing = [
        r
        for r in api.requests
        if isinstance(r, httpx.Request) and r.url.path == "/api/pages"
    ]
    # The unresolved slug falls back to the slug filter instead of a book id.
    assert any(r.url.params.get("filter[book_slug]") == "unknown-book" for r in listing)


def test_ingest_all_disabled(db):
    with patch.object(bs.settings, "bookstack_enabled", False):
        connections, run_lock = connections_env()
        with connections, run_lock:
            results = bs.ingest_bookstack_all(db)
    assert results[0]["skipped"] is True
    db.commit.assert_not_called()


def test_ingest_all_dedup_advances_cursor_without_storing(db, env):
    stack, api = env(list_items=[PAGE_LIST_ITEM], full_pages={11: PAGE_FULL})
    cfg = {"label": "dedupe-docs", "book": {"slug": "isms"}}

    with contextlib.ExitStack() as total:
        total.enter_context(stack)
        connections, run_lock = connections_env()
        total.enter_context(connections)
        total.enter_context(run_lock)
        total.enter_context(patch.object(bs, "load_bookstack_config", return_value=cfg))
        total.enter_context(patch.object(bs.settings, "bookstack_enabled", True))
        with patch.object(
            bs, "store_event_with_artifact", return_value={"deduped": True}
        ):
            results = bs.ingest_bookstack_all(db)
    assert results[0]["deduped"] == 1
    assert results[0]["created"] == 0
