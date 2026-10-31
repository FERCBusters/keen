"""Feed and timestamp helpers shared by the Gitea-compatible collectors."""

from datetime import datetime

import httpx
import pytest
from app.core.datetime_utils import to_utc_naive
from app.ingest import git_activity as collector


def test_feed_namespaces_content_and_item_limit():
    xml = """<rss xmlns="urn:rss" xmlns:content="http://purl.org/rss/1.0/modules/content/">
      <channel><item><title> alice pushed </title><link>https://git.example/a/b</link>
      <guid>item-1</guid><author>alice</author><description>preview</description>
      <content:encoded>full content</content:encoded>
      <pubDate>Fri, 09 Oct 2026 12:00:00 +1100</pubDate></item>
      <item><title>second</title></item></channel></rss>"""
    items = collector._parse_rss_items(xml, max_items=1)
    assert items == [
        {
            "title": "alice pushed",
            "link": "https://git.example/a/b",
            "guid": "item-1",
            "author": "alice",
            "description": "preview",
            "content": "full content",
            "pubDate": "Fri, 09 Oct 2026 12:00:00 +1100",
        }
    ]
    assert collector._parse_rfc822_ts(items[0]["pubDate"]) == datetime(2026, 10, 9, 1)
    assert collector._classify_from_title(items[0]["title"]) == ("push", "success", 2)


def test_timestamp_normalization_and_invalid_input():
    assert collector._parse_iso_ts("2026-10-09T12:00:00+11:00") == datetime(
        2026, 10, 9, 1
    )
    assert collector._parse_iso_ts("2026-10-09T01:00:00Z") == datetime(2026, 10, 9, 1)
    for value in (None, "", "invalid"):
        assert collector._parse_iso_ts(value) is None
        assert collector._parse_rfc822_ts(value) is None


def test_redirect_diagnostics_redact_both_urls():
    response = httpx.Response(
        302,
        request=httpx.Request(
            "GET", "https://git.example/team/repo.rss?token=private-token"
        ),
        headers={"location": "https://git.example/login?access_token=private-redirect"},
    )
    error = collector._response_status_error(response)
    assert error == "Collection failed: upstream returned HTTP 302"
    assert "private-token" not in error and "private-redirect" not in error
    assert collector._response_status_error(httpx.Response(200)) is None


def test_repository_identity_and_rss_detection():
    assert collector._infer_owner_repo_from_feed_url(
        "https://git.example/team/repo.rss?token=x"
    ) == ("team", "repo")
    assert collector._infer_owner_repo_from_feed_url(
        "https://git.example/team/repo.atom"
    ) == ("team", "repo")
    assert collector._infer_owner_repo_from_feed_url("https://git.example/") == (
        None,
        None,
    )
    assert collector._is_probably_rss(
        httpx.Response(200, text="  <rss><channel/></rss>")
    )
    assert not collector._is_probably_rss(
        httpx.Response(200, text="<html>Login</html>")
    )


@pytest.mark.parametrize(
    "title,expected",
    [
        ("alice merged pull request #4", ("pull_merged", "success", 2)),
        ("Branch feature was merged by bob", ("pull_merged", "success", 2)),
        ("alice opened pull request #5", ("pull_opened", "info", 3)),
        ("bob commented on issue #9", ("comment", "info", 3)),
        ("carol approved pull request #5", ("review_approved", "success", 2)),
        ("dave pushed to main", ("push", "success", 2)),
        ("eve created repository", ("activity", "info", 3)),
        ("", ("activity", "info", 3)),
    ],
)
def test_feed_titles_classify_into_actions_and_outcomes(title, expected):
    # Review and merge titles carry success at a lower severity; anything the
    # collector cannot recognise stays an informational activity.
    assert collector._classify_from_title(title) == expected


def test_feeds_without_a_channel_or_a_parseable_url_yield_nothing():
    assert collector._parse_rss_items("<rss><title>bare</title></rss>") == []
    assert (
        collector._parse_rss_items("<rss><channel><title>t</title></channel></rss>")
        == []
    )
    assert collector._infer_owner_repo_from_feed_url("https://[bad/owner/repo.rss") == (
        None,
        None,
    )


def test_naive_timestamps_and_xml_content_types_are_accepted():
    naive = datetime(2026, 10, 9, 1)
    assert to_utc_naive(naive) is naive
    assert collector._is_probably_rss(
        httpx.Response(
            200, headers={"content-type": "application/rss+xml"}, text="<html/>"
        )
    )
    namespaced = """<rss xmlns="urn:rss"><channel><item><title>x pushed</title>
      </item></channel></rss>"""
    assert collector._parse_rss_items(namespaced)[0]["title"] == "x pushed"
    plain = "<rss><channel><item><title>y pushed</title></item></channel></rss>"
    assert collector._parse_rss_items(plain)[0]["title"] == "y pushed"
