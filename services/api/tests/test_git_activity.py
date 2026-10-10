"""Contracts shared by both Gitea-compatible collectors."""

from datetime import datetime

import httpx
import pytest
from app.ingest import forgejo, gitea


@pytest.mark.parametrize("collector", [forgejo, gitea], ids=["forgejo", "gitea"])
def test_feed_namespaces_content_and_item_limit(collector):
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


@pytest.mark.parametrize("collector", [forgejo, gitea], ids=["forgejo", "gitea"])
def test_timestamp_normalization_and_invalid_input(collector):
    assert collector._parse_iso_ts("2026-10-09T12:00:00+11:00") == datetime(
        2026, 10, 9, 1
    )
    assert collector._parse_iso_ts("2026-10-09T01:00:00Z") == datetime(2026, 10, 9, 1)
    for value in (None, "", "invalid"):
        assert collector._parse_iso_ts(value) is None
        assert collector._parse_rfc822_ts(value) is None


@pytest.mark.parametrize("collector", [forgejo, gitea], ids=["forgejo", "gitea"])
def test_redirect_diagnostics_redact_both_urls(collector):
    response = httpx.Response(
        302,
        request=httpx.Request(
            "GET", "https://git.example/team/repo.rss?token=private-token"
        ),
        headers={"location": "https://git.example/login?access_token=private-redirect"},
    )
    error = collector._response_status_error(response)
    assert "302" in error and "redirect location" in error
    assert "private-token" not in error and "private-redirect" not in error
    assert collector._response_status_error(httpx.Response(200)) is None


@pytest.mark.parametrize("collector", [forgejo, gitea], ids=["forgejo", "gitea"])
def test_repository_identity_and_rss_detection(collector):
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
