"""Display and validation helpers shared by several API operations."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from app.core.config import settings
from app.services import presentation
from app.services.errors import DomainError

HOSTILE = '<img src=x onerror="alert(1)">'


@pytest.fixture
def rss_config(tmp_path, monkeypatch):
    monkeypatch.setitem(presentation._RSS_LABEL_CACHE, "mtime", None)
    monkeypatch.setitem(presentation._RSS_LABEL_CACHE, "labels", {})
    path = tmp_path / "rss.yml"
    monkeypatch.setattr(settings, "rss_config_path", str(path))
    return path


def test_rss_feed_labels_are_read_from_the_feed_configuration(rss_config):
    rss_config.write_text(
        "feeds:\n"
        "  - url: https://news.example/feed?token=abc123\n"
        "    label: Security news\n"
        "  - url: https://other.example/feed\n"
        "    name: Other feed\n"
        "  - url: https://unlabelled.example/feed\n"
        "  - label: No URL\n"
        "  - just a string\n"
    )
    labels = presentation.rss_labels_by_feed_url()
    # Feed URLs are matched in their redacted form, as stored on events.
    assert set(labels.values()) == {"Security news", "Other feed"}
    secret_key = next(url for url, label in labels.items() if label == "Security news")
    assert "abc123" not in secret_key
    assert (
        presentation.rss_labels_by_feed_url() is labels
    )  # cached until the file changes

    event = SimpleNamespace(
        source="rss", system="fallback", raw_pointer={"rss": {"feed_url": secret_key}}
    )
    assert presentation.event_identifier(event) == "Security news"
    event.raw_pointer = {"rss": {"feed_url": "https://unknown.example/feed"}}
    assert presentation.event_identifier(event) == "fallback"
    event.raw_pointer = "not a mapping"
    assert presentation.event_identifier(event) == "fallback"


def test_missing_or_broken_feed_configuration_gives_no_labels(rss_config, monkeypatch):
    assert presentation.rss_labels_by_feed_url() == {}  # file does not exist
    rss_config.write_text("feeds: [unclosed\n")
    assert presentation.rss_labels_by_feed_url() == {}
    monkeypatch.setattr(settings, "rss_config_path", "  ")
    assert presentation.rss_labels_by_feed_url() == {}


@pytest.mark.parametrize(
    "event,expected",
    [
        (SimpleNamespace(source="loki", system="api-logs", raw_pointer={}), "api-logs"),
        (
            SimpleNamespace(
                source="LOKI",
                system="",
                raw_pointer={"loki": {"query_name": " errors "}},
            ),
            "errors",
        ),
        (
            SimpleNamespace(
                source="loki", system=None, raw_pointer={"loki": {"query_name": ""}}
            ),
            None,
        ),
        (SimpleNamespace(source="loki", system=None, raw_pointer=None), None),
        (
            SimpleNamespace(
                source="webhook:enroll",
                system="enroll",
                normalized_payload={"new": {"host": " web-1 "}},
            ),
            "web-1",
        ),
        (
            SimpleNamespace(
                source="webhook:enroll",
                system="enroll",
                normalized_payload={"old": {"host": "web-0"}},
            ),
            "web-0",
        ),
        (
            SimpleNamespace(
                source="webhook:enroll-drift",
                system="enroll",
                normalized_payload={"payload": {"host": "db-1"}},
            ),
            "db-1",
        ),
        (
            SimpleNamespace(
                source="webhook:enroll", system="enroll", normalized_payload={}
            ),
            "enroll",
        ),
        (SimpleNamespace(source="jenkins", system="ci"), "ci"),
        (SimpleNamespace(source="jenkins", system=""), None),
    ],
)
def test_event_identifiers_prefer_source_specific_names(event, expected):
    assert presentation.event_identifier(event) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("  ", None),
        ("best practice", "Best Practice"),
        ("Result of Risk Assessment", "Result of Risk Assessment"),
        ("risk_assessment", "Result of Risk Assessment"),
        ("limited applicability", "Limited Applicability"),
    ],
)
def test_control_justifications_accept_labels_and_identifiers(raw, expected):
    assert presentation.validate_control_justification(raw) == expected


def test_unknown_control_justifications_are_rejected():
    with pytest.raises(DomainError) as error:
        presentation.validate_control_justification(HOSTILE)
    assert error.value.status_code == 400
    assert "Best Practice" in error.value.detail


def test_timezones_and_themes_are_validated():
    assert presentation.validate_timezone_name(None) is None
    assert presentation.validate_timezone_name("  ") is None
    assert (
        presentation.validate_timezone_name(" Australia/Melbourne ")
        == "Australia/Melbourne"
    )
    with pytest.raises(DomainError):
        presentation.validate_timezone_name("Mars/Olympus_Mons")
    assert presentation.validate_theme_id(None) is None
    assert presentation.validate_theme_id(" ") is None
    assert presentation.validate_theme_id("ocean-dark") == "ocean-dark"
    assert {"purple", "teal-dark"} <= presentation.theme_ids()
    with pytest.raises(DomainError):
        presentation.validate_theme_id("dark")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/events.html?q=ssh&offset=200", "/events.html?q=ssh"),
        ("events.html?q=&source=jenkins", "/events.html?source=jenkins"),
        ("?q=ssh", "/?q=ssh"),
        ("/controls.html", "/controls.html"),
    ],
)
def test_saved_search_urls_are_normalised(raw, expected):
    assert presentation.normalize_saved_search_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "javascript:alert(1)",
        "DATA:text/html,x",
        "https://evil.example/",
        "//evil.example/x",
        "/x?" + "q=1&" * 600,
    ],
)
def test_unsafe_or_oversized_saved_search_urls_are_rejected(raw):
    with pytest.raises(DomainError):
        presentation.normalize_saved_search_url(raw)


@pytest.mark.parametrize(
    "raw_pointer,payload,expected",
    [
        ({"url": "https://ci.example/job/1"}, None, "https://ci.example/job/1"),
        ({"url": "/home/user"}, None, None),
        ({"url": "javascript:alert(1)"}, None, None),
        ({"url": "//evil.example/x"}, None, None),
        ({"url": "https://" + "a" * 2050}, None, None),
        ({"url": "ftp://files.example/x"}, None, None),
        (
            {
                "rss": {
                    "entry_link": "https://news.example/item/1",
                    "feed_url": "https://news.example/feed",
                }
            },
            None,
            "https://news.example/item/1",
        ),
        (
            {},
            {"rss": {"entry": {"link": "https://news.example/item/2"}}},
            "https://news.example/item/2",
        ),
        (
            {},
            {"entry": {"href": "https://news.example/item/3"}},
            "https://news.example/item/3",
        ),
        (
            {"jenkins": {"build_url": "x", "url": "https://ci.example/job/2"}},
            None,
            "https://ci.example/job/2",
        ),
        (
            {"sourceUrl": "https://tracker.example/1", "url": "relative"},
            None,
            "https://tracker.example/1",
        ),
        (
            "not a mapping",
            {"link": "https://example.test/payload"},
            "https://example.test/payload",
        ),
        (None, None, None),
    ],
)
def test_event_source_urls_must_be_absolute_web_links(raw_pointer, payload, expected):
    assert presentation.extract_source_url(raw_pointer, payload) == expected


def test_iso_timestamps_are_parsed_leniently():
    assert presentation.parse_iso_dt(None) is None
    assert presentation.parse_iso_dt("  ") is None
    assert presentation.parse_iso_dt("yesterday") is None
    assert presentation.parse_iso_dt("2026-04-10T09:00:00Z") == datetime(
        2026, 4, 10, 9, tzinfo=timezone.utc
    )
    naive = presentation.parse_iso_dt("2026-04-10T09:00:00")
    assert naive.tzinfo == timezone.utc
    offset = presentation.parse_iso_dt("2026-04-10T09:00:00+10:00")
    assert offset.utcoffset().total_seconds() == 36000


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ({"a": 1}, {"a": 1}),
        ("  ", None),
        ('{"reviewed": true}', {"reviewed": True}),
        ("[1, 2]", {"value": [1, 2]}),
        (HOSTILE, {"notes": HOSTILE}),
        (5, {"value": 5}),
    ],
)
def test_diary_details_are_coerced_to_objects(raw, expected):
    assert presentation.normalize_diary_details(raw) == expected


def test_diary_links_allow_only_web_and_mail_links():
    links = presentation.normalize_diary_links(
        [
            {"url": " https://wiki.example/page ", "text": "x" * 300},
            {"href": "mailto:security@example.test", "label": "Security team"},
            "https://plain.example/",
            {"url": "", "text": "skipped"},
            42,
        ]
    )
    assert links == [
        {"url": "https://wiki.example/page", "text": "x" * 256},
        {"url": "mailto:security@example.test", "text": "Security team"},
        {"url": "https://plain.example/", "text": ""},
    ]
    assert presentation.normalize_diary_links(None) == []
    assert presentation.normalize_diary_links("  ") == []
    assert presentation.normalize_diary_links("https://single.example/") == [
        {"url": "https://single.example/", "text": ""}
    ]
    assert presentation.normalize_diary_links('{"url": "https://json.example/"}') == [
        {"url": "https://json.example/", "text": ""}
    ]
    assert (
        len(
            presentation.normalize_diary_links(
                [f"https://e.example/{i}" for i in range(40)]
            )
        )
        == 25
    )

    for bad, detail in (
        ("javascript:alert(1)", "Unsupported link scheme: javascript"),
        ("/relative/path", "Unsupported link scheme: none"),
        ("https:///no-host", "Link URL must include a hostname"),
        ("mailto:", "mailto: URL must include an address"),
        ("https://e.example/" + "a" * 2050, "Link URL is too long"),
        ("   ", "Link URL is blank"),
    ):
        with pytest.raises(DomainError) as error:
            presentation.validate_diary_link_url(bad)
        assert error.value.detail == detail
    with pytest.raises(DomainError):
        presentation.normalize_diary_links([{"url": "javascript:alert(1)"}])
