"""BookStack poller contracts against simulated BookStack REST payloads.

The payloads mirror real BookStack API shapes (list items, full page details,
books and tags) while keeping every response offline and bounded.
"""

import contextlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
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
            book_id = request.url.params.get("filter[book_id]")
            if book_id is not None:
                window = [p for p in window if str(p.get("book_id")) == book_id]
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


def test_ingest_all_lists_only_books_it_resolves(db, env):
    stack, api = env(list_items=[PAGE_LIST_ITEM], full_pages={11: PAGE_FULL})
    cfg = {
        "label": "books-mode",
        "books": ["isms", {"slug": "unknown-book"}],
        "poll": {"count": 10},
    }
    stored = []

    def store(db, **kwargs):
        stored.append(kwargs)
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

    summary = results[0]
    assert summary["created"] == 1
    book_requests = [
        r
        for r in api.requests
        if r.url.path == "/api/books" and "filter[slug]" in r.url.params
    ]
    slugs = {r.url.params["filter[slug]"] for r in book_requests}
    assert slugs == {"isms", "unknown-book"}
    # BookStack ignores page filters it does not support, so a book slug that
    # resolves to no book is never listed: the listing would hold every book.
    listing = [r for r in api.requests if r.url.path == "/api/pages"]
    assert [r.url.params.get("filter[book_id]") for r in listing] == ["7"]
    assert [call["system"] for call in stored] == [
        "books-mode:isms:access-control-policy"
    ]
    assert summary["errors"] == [
        "book_slug=unknown-book: No readable BookStack book has this slug"
    ]
    # The unread book keeps the cursor in place for the next run.
    assert summary["cursor_set_to"] is None


def test_a_book_whose_listing_fails_is_reported_and_read_again(db, env):
    stack, api = env(list_items=[PAGE_LIST_ITEM], full_pages={11: PAGE_FULL})
    upstream = api.handler

    def handler(request):
        if request.url.params.get("filter[book_id]") == "8":
            return httpx.Response(500, json={"error": "unavailable"})
        return upstream(request)

    api.handler = handler
    cfg = {"label": "books-mode", "books": [{"id": 8}, {"id": 7, "slug": "isms"}]}
    stored = []

    def store(db, **kwargs):
        stored.append(kwargs)
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

    # The other book is still collected, and the failure holds the cursor.
    assert [call["system"] for call in stored] == [
        "books-mode:isms:access-control-policy"
    ]
    assert results[0]["errors"] == [
        "book=8: Collection failed: upstream returned HTTP 500"
    ]
    assert results[0]["cursor_set_to"] is None


def test_a_cursor_moves_to_the_run_start_when_every_book_is_read(db, env):
    stack, api = env(list_items=[PAGE_LIST_ITEM], full_pages={11: PAGE_FULL})
    cfg = {"label": "books-mode", "books": ["isms"]}

    with contextlib.ExitStack() as total:
        total.enter_context(stack)
        connections, run_lock = connections_env()
        total.enter_context(connections)
        total.enter_context(run_lock)
        total.enter_context(patch.object(bs, "load_bookstack_config", return_value=cfg))
        total.enter_context(patch.object(bs.settings, "bookstack_enabled", True))
        total.enter_context(
            patch.object(
                bs, "store_event_with_artifact", return_value={"deduped": True}
            )
        )
        before = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
        results = bs.ingest_bookstack_all(db)

    assert results[0]["errors"] == []
    assert datetime.fromisoformat(results[0]["cursor_set_to"]) >= before


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


def listed(page_id, slug, name, book_id):
    return dict(PAGE_LIST_ITEM, id=page_id, slug=slug, name=name, book_id=book_id)


def detail(item):
    return dict(item, html=f"<p>{item['name']}</p>", markdown=None, tags=[])


def seeded_run(db, env, cfg, pages, window=(), failing=None, details=None):
    """Run a poll whose window lists only ``window``; mapped pages are seeded."""
    full = {p["id"]: detail(p) for p in pages}
    full.update(details or {})
    stack, api = env(list_items=pages, full_pages=full)
    upstream = api.handler

    def handler(request):
        if "filter[updated_at:gt]" in request.url.params:
            api.requests.append(request)
            data = [p for p in pages if p["id"] in window]
            offset = int(request.url.params.get("offset", 0))
            count = int(request.url.params.get("count", 100))
            page = data[offset : offset + count]
            return httpx.Response(200, json={"data": page, "total": len(data)})
        if failing and failing(request):
            return httpx.Response(500, json={"error": "unavailable"})
        return upstream(request)

    api.handler = handler
    stored, mapped = [], []

    def store(db, **kwargs):
        stored.append(kwargs)
        return {
            "deduped": False,
            "event_id": f"ev-{kwargs['raw_pointer']['bookstack']['page_id']}",
        }

    def add_mappings(db, *, event_id, refs, confidence, method, rationale):
        mapped.append((event_id, refs))
        return len(refs)

    with contextlib.ExitStack() as total:
        total.enter_context(stack)
        connections, run_lock = connections_env()
        total.enter_context(connections)
        total.enter_context(run_lock)
        total.enter_context(patch.object(bs, "load_bookstack_config", return_value=cfg))
        total.enter_context(patch.object(bs.settings, "bookstack_enabled", True))
        total.enter_context(patch.object(bs, "store_event_with_artifact", store))
        total.enter_context(patch.object(bs, "_ensure_and_add_mappings", add_mappings))
        results = bs.ingest_bookstack_all(db)
    assert results[0].get("ok") is True, results
    return SimpleNamespace(
        summary=results[0],
        pages=[c["raw_pointer"]["bookstack"]["page_id"] for c in stored],
        mapped=mapped,
        requests=api.requests,
        stored=stored,
    )


SEEDED_PAGES = [
    listed(11, "access-control-policy", "Access Control Policy", 7),
    listed(44, "employment-contract", "Employment Contract", 7),
    listed(51, "supplier-security", "Supplier Security", 7),
    listed(52, "supplier-onboarding", "Supplier Onboarding", 8),
    listed(53, "hr-handbook", "HR Handbook", 8),
    listed(54, "hr-handbook", "Old HR Handbook", 7),
    listed(61, "asset-register", "Asset Register", 9),
    listed(70, "board-minutes", "Board minutes", 7),
    listed(80, "release-notes", "Release notes", 7),
]


def test_mapped_pages_are_captured_within_their_book_scope(db, env):
    cfg = {
        "label": "seeded",
        "book": {"slug": "isms"},
        "page_mappings": [
            {"match": {"slug": "access-control-policy"}, "map_to": ["A.5.15"]},
            {"match": {"title": "Employment Contract"}, "map_to": ["A.6.2"]},
            {"match": {"slug_regex": "^supplier-"}, "map_to": ["A.5.19"]},
            {"match": {"book": {"id": 8}, "slug": "hr-handbook"}, "map_to": ["A.6.3"]},
            {"match": {"id": 61}, "map_to": ["A.5.9"]},
            # Invalid patterns are skipped, so they match nothing.
            {"match": {"slug_regex": "("}, "map_to": ["A.1"]},
            {"match": {"title_regex": "["}, "map_to": ["A.2"]},
        ],
        "capture_pages": [{"title": "Board minutes"}],
    }

    run = seeded_run(db, env, cfg, SEEDED_PAGES, window=[80])

    # Page 80 changed in the window; the rest are captured because a mapping
    # or capture selector names them, each looked up in its book scope.
    assert run.pages == [11, 44, 51, 53, 61, 70, 80]
    assert run.mapped == [
        ("ev-11", ["A.5.15"]),
        ("ev-44", ["A.6.2"]),
        ("ev-51", ["A.5.19"]),
        ("ev-53", ["A.6.3"]),
        ("ev-61", ["A.5.9"]),
    ]
    assert run.summary["errors"] == [] and run.summary["config_mappings_created"] == 5
    lookups = [
        dict(r.url.params)
        for r in run.requests
        if r.url.path == "/api/pages"
        and ("filter[name]" in r.url.params or "filter[slug]" in r.url.params)
    ]
    assert {
        (q.get("filter[slug]") or q["filter[name]"], q["filter[book_id]"])
        for q in lookups
    } == {
        ("access-control-policy", "7"),
        ("Employment Contract", "7"),
        ("Board minutes", "7"),
        ("hr-handbook", "8"),
    }


def test_mapped_pages_in_an_unknown_book_are_never_looked_up(db, env):
    cfg = {
        "label": "seeded",
        "book": {"slug": "retired-book"},
        "page_mappings": [
            {"match": {"slug": "access-control-policy"}, "map_to": ["A.5.15"]},
            {"match": {"title": "Employment Contract"}, "map_to": ["A.6.2"]},
            {"match": {"slug_regex": "^supplier-"}, "map_to": ["A.5.19"]},
        ],
    }

    run = seeded_run(db, env, cfg, SEEDED_PAGES)

    assert (run.pages, run.mapped) == ([], [])
    assert [r for r in run.requests if r.url.path == "/api/pages"] == []
    assert run.summary["errors"] == [
        "book_slug=retired-book: No readable BookStack book has this slug"
    ]
    assert run.summary["cursor_set_to"] is None


def test_without_a_book_scope_mapped_pages_are_found_instance_wide(db, env):
    cfg = {
        "label": "seeded",
        "page_mappings": [
            {"match": {"slug": "asset-register"}, "map_to": ["A.5.9"]},
            {"match": {"slug_regex": "^supplier-"}, "map_to": ["A.5.19"]},
        ],
        "seed_mapped_pages": True,
    }

    run = seeded_run(db, env, cfg, SEEDED_PAGES)

    assert run.pages == [51, 52, 61]
    assert sorted(run.mapped) == [
        ("ev-51", ["A.5.19"]),
        ("ev-52", ["A.5.19"]),
        ("ev-61", ["A.5.9"]),
    ]


def test_mapped_pages_wait_for_changes_when_seeding_is_off(db, env):
    cfg = {
        "label": "seeded",
        "seed_mapped_pages": False,
        "page_mappings": [{"match": {"slug": "release-notes"}, "map_to": ["A.8.32"]}],
    }

    run = seeded_run(db, env, cfg, SEEDED_PAGES, window=[44])

    # Only the changed page is read; the mapping still applies to what is read.
    assert (run.pages, run.mapped) == ([44], [])
    run = seeded_run(db, env, cfg, SEEDED_PAGES, window=[80])
    assert (run.pages, run.mapped) == ([80], [("ev-80", ["A.8.32"])])


def test_mapped_pages_are_found_in_each_configured_book(db, env):
    cfg = {
        "label": "multi",
        # Entries without a usable identity are ignored.
        "books": [{"id": 7}, {"id": 8}, {"id": "seven"}, {"slug": ""}, 5],
        "page_mappings": [
            {"match": {"slug": "hr-handbook"}, "map_to": ["A.6.3"]},
            {"match": {"slug_regex": "^supplier-"}, "map_to": ["A.5.19"]},
            {"match": {"title": "Board minutes"}, "map_to": ["A.5.4"]},
        ],
    }

    run = seeded_run(db, env, cfg, SEEDED_PAGES)

    # The first book with the slug wins; patterns are matched in every book.
    assert run.pages == [51, 52, 54, 70]
    listings = {
        r.url.params.get("filter[book_id]")
        for r in run.requests
        if r.url.path == "/api/pages" and "filter[slug]" not in r.url.params
    }
    assert listings == {"7", "8"}


def test_a_failed_lookup_in_one_book_falls_through_to_the_next(db, env):
    cfg = {
        "label": "multi",
        "books": [{"id": 7}, {"id": 8}],
        "page_mappings": [
            {"match": {"slug": "hr-handbook"}, "map_to": ["A.6.3"]},
            {"match": {"title": "Supplier Onboarding"}, "map_to": ["A.5.19"]},
            {"match": {"slug_regex": "^asset-"}, "map_to": ["A.5.9"]},
        ],
    }

    def book_seven(request):
        return request.url.params.get("filter[book_id]") == "7"

    run = seeded_run(db, env, cfg, SEEDED_PAGES, failing=book_seven)

    assert run.pages == [52, 53]
    assert run.summary["errors"] == []


def test_window_listings_follow_every_page(db, env):
    cfg = {"label": "paged", "poll": {"count": 2}}

    run = seeded_run(db, env, cfg, SEEDED_PAGES, window=[11, 44, 51, 52, 53])

    assert run.pages == [11, 44, 51, 52, 53]
    offsets = [
        r.url.params["offset"]
        for r in run.requests
        if r.url.path == "/api/pages" and "filter[updated_at:gt]" in r.url.params
    ]
    assert offsets == ["0", "2", "4"]


def test_unusable_settings_fall_back_and_actors_come_from_the_creator(db, env):
    created_only = dict(
        detail(SEEDED_PAGES[1]),
        updated_by=None,
        created_by={"id": 1, "name": "Founder"},
    )
    cfg = {
        "label": "fallbacks",
        "book": {"id": "seven"},
        "capture": {"preview_chars": "lots"},
    }

    run = seeded_run(
        db, env, cfg, SEEDED_PAGES, window=[44], details={44: created_only}
    )

    (stored,) = run.stored
    assert stored["actor"] == "Founder"
    payload = stored["normalized_payload"]["bookstack"]
    assert payload["preview_chars"] == 500
    # An unusable book id leaves the listing unscoped.
    listing = next(
        r
        for r in run.requests
        if r.url.path == "/api/pages" and "filter[updated_at:gt]" in r.url.params
    )
    assert "filter[book_id]" not in listing.url.params


def test_a_mapping_scoped_to_another_book_slug_never_matches():
    # A book slug on the mapping is a hard scope: pages from any other book,
    # even with the same page slug, stay unmapped.
    mapping = bs._parse_page_mappings(
        {
            "page_mappings": [
                {
                    "match": {"book_slug": "hr-docs", "slug": "access-control-policy"},
                    "map_to": ["A.9"],
                }
            ]
        }
    )[0]
    assert mapping.matches(PAGE_LIST_ITEM, PAGE_FULL) is False
    assert mapping.matches(dict(PAGE_LIST_ITEM, book_slug="HR-Docs"), {}) is True


def test_mappings_without_usable_targets_or_book_ids_are_tolerated():
    # Targets that are neither text nor a list are skipped, and a book id that
    # is no number leaves the mapping unscoped by book id.
    mappings = bs._parse_page_mappings(
        {
            "page_mappings": [
                {"match": {"slug": "a"}, "map_to": 7},
                {"match": {"slug": "b"}, "map_to": {"ref": "A.1"}},
                {"match": {"slug": "c", "book_id": "seven"}, "map_to": ["A.2"]},
            ]
        }
    )
    assert [m.match_slug for m in mappings] == ["c"]
    assert mappings[0].match_book_id is None
    assert mappings[0].matches({"slug": "c", "book_id": 99}, {}) is True


def test_cursor_is_reused_when_present_and_created_once_when_absent(db):
    existing = SimpleNamespace(name="bookstack:pages", last_ts=None, meta={})
    db.query.return_value.filter.return_value.one_or_none.return_value = existing
    assert bs._get_cursor(db) is existing
    db.add.assert_not_called()

    db.query.return_value.filter.return_value.one_or_none.return_value = None
    created = bs._get_cursor(db)
    assert isinstance(created, bs.IngestionCursor)
    assert created.last_ts is None and created.meta == {}
    db.add.assert_called_once_with(created)
    db.flush.assert_called_once()


def test_system_info_is_empty_when_the_endpoint_fails_or_is_malformed():
    # Instance metadata is optional: an upstream failure, an invalid body or a
    # list payload all leave the collector with no system information.
    def failing(request):
        return httpx.Response(503, text="maintenance")

    def not_json(request):
        return httpx.Response(200, text="<html>login</html>")

    def listed_body(request):
        return httpx.Response(200, json=["Docs", "https://docs.example.com"])

    for handler in (failing, not_json, listed_body):
        client = httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://docs.example.com"
        )
        assert bs._get_system_info(client) == {}


def test_page_listing_stops_at_the_configured_page_budget():
    # max_pages bounds how far the listing walks even while the upstream keeps
    # returning full pages.
    items = [listed(i, f"page-{i}", f"Page {i}", 7) for i in range(1, 7)]
    api = BookStackApi(list_items=items, full_pages={})
    client = httpx.Client(
        transport=httpx.MockTransport(api.handler), base_url="https://docs.example.com"
    )
    out = bs._list_pages(
        client,
        since_ts=None,
        until_ts=None,
        book_id=7,
        book_slug=None,
        count=2,
        include_drafts=False,
        include_templates=False,
        max_pages=4,
    )
    assert [p["id"] for p in out] == [1, 2, 3, 4]
    assert [r.url.params["offset"] for r in api.requests] == ["0", "2"]


def test_single_page_lookups_need_a_selector_and_a_result():
    # Blank selectors never reach the API, and an empty listing is no page.
    api = BookStackApi(list_items=[PAGE_LIST_ITEM], full_pages={})
    client = httpx.Client(
        transport=httpx.MockTransport(api.handler), base_url="https://docs.example.com"
    )
    assert bs._find_page_by_slug(client, "  ", book_id=7, book_slug=None) is None
    assert bs._find_page_by_title(client, "", book_id=7, book_slug=None) is None
    assert api.requests == []

    assert bs._find_page_by_slug(client, "missing", book_id=7, book_slug=None) is None
    assert bs._find_page_by_title(client, "Missing", book_id=7, book_slug=None) is None
    assert len(api.requests) == 2

    found = bs._find_page_by_title(
        client, "Access Control Policy", book_id=None, book_slug="isms"
    )
    assert found["id"] == 11 and found["book_slug"] == "isms"


def test_an_ambiguous_reference_attaches_no_mapping(db):
    # Two controls sharing a reference make the target ambiguous; evidence is
    # never attached to a guess, so nothing is created or committed.
    first, second = MagicMock(), MagicMock()
    for item in (first, second):
        item.ref = "A.9"
        item.type = "annex_control"
    query_control = MagicMock()
    query_control.filter.return_value.all.return_value = [first, second]
    db.query.side_effect = [query_control]
    created = bs._ensure_and_add_mappings(
        db,
        event_id="ev-1",
        refs=["A.9"],
        confidence=0.7,
        method="config",
        rationale="r",
    )
    assert created == 0
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_config_mappings_skip_events_without_page_details_or_matches(db):
    ev = MagicMock()
    ev.source = "bookstack"
    ev.normalized_payload = {}
    ev.raw_pointer = {}
    mapping = MagicMock()
    mapping.matches.return_value = False
    with (
        patch.object(bs, "load_bookstack_config", return_value={}),
        patch.object(bs, "_parse_page_mappings", return_value=[mapping]),
        patch.object(bs, "_ensure_and_add_mappings") as add,
    ):
        # No page fields on the event: nothing to match against.
        assert bs.apply_bookstack_config_mappings(db, ev) == 0
        mapping.matches.assert_not_called()

        ev.normalized_payload = {"bookstack": {"page_slug": "other"}}
        assert bs.apply_bookstack_config_mappings(db, ev) == 0
        mapping.matches.assert_called_once()
        add.assert_not_called()


def test_pattern_and_title_mappings_honour_their_own_book_scope(db, env):
    # A mapping naming its own book is looked up there, ahead of the top-level
    # book scope, so a page in another book is captured and mapped.
    cfg = {
        "label": "scoped",
        "book": {"id": 7},
        "page_mappings": [
            {"match": {"book_id": 8, "slug_regex": "^supplier-"}, "map_to": ["A.5.19"]},
            {"match": {"book": {"id": 8}, "title": "HR Handbook"}, "map_to": ["A.6.3"]},
        ],
    }

    run = seeded_run(db, env, cfg, SEEDED_PAGES)

    assert run.pages == [52, 53]
    assert run.mapped == [("ev-52", ["A.5.19"]), ("ev-53", ["A.6.3"])]
    lookups = {
        (r.url.params.get("filter[name]"), r.url.params.get("filter[book_id]"))
        for r in run.requests
        if r.url.path == "/api/pages" and "filter[name]" in r.url.params
    }
    assert lookups == {("HR Handbook", "8")}
    pattern_listings = {
        r.url.params.get("filter[book_id]")
        for r in run.requests
        if r.url.path == "/api/pages"
        and "filter[name]" not in r.url.params
        and "filter[updated_at:gt]" not in r.url.params
    }
    assert pattern_listings == {"8"}


def test_title_mappings_without_any_book_scope_search_the_instance(db, env):
    cfg = {
        "label": "instance",
        "page_mappings": [{"match": {"title": "Asset Register"}, "map_to": ["A.5.9"]}],
    }

    run = seeded_run(db, env, cfg, SEEDED_PAGES)

    assert (run.pages, run.mapped) == ([61], [("ev-61", ["A.5.9"])])
    (lookup,) = [
        r
        for r in run.requests
        if r.url.path == "/api/pages" and "filter[name]" in r.url.params
    ]
    assert "filter[book_id]" not in lookup.url.params


def test_a_title_pattern_that_misses_rejects_the_page():
    mapping = bs._parse_page_mappings(
        {"page_mappings": [{"match": {"title_regex": "^Backup"}, "map_to": ["A.1"]}]}
    )[0]
    assert mapping.matches(PAGE_LIST_ITEM, PAGE_FULL) is False
    assert mapping.matches(dict(PAGE_LIST_ITEM, name="Backup Policy"), {}) is True


def test_preview_text_is_empty_when_tag_stripping_times_out():
    # Hostile HTML that exhausts the regex budget yields no preview, so the
    # collector stores the snapshot without blocking on the pattern.
    slow = MagicMock()
    slow.sub.side_effect = TimeoutError()
    with patch.object(bs, "_TAG_RE", slow):
        assert bs._strip_html_to_text("<p>text</p>") == ""
    slow.sub.assert_called_once()
