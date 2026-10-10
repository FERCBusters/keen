"""Shared, stateless Forgejo/Gitea feed parsing and response diagnostics.

Authentication, fetching, cursors and event provenance belong to each collector.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from app.security.redaction import redact_url


def _to_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_iso_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return _to_utc_naive(dt)
    except Exception:
        return None


def _parse_rfc822_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value.strip())
        return _to_utc_naive(dt)
    except Exception:
        return None


def _redacted_url(url: Any) -> str:
    return redact_url(str(url))


def _response_status_error(resp: httpx.Response) -> str | None:
    """Return a redacted status/redirect error for non-success responses."""

    if 200 <= resp.status_code < 300:
        return None

    status = f"{resp.status_code} {resp.reason_phrase}".strip()
    msg = f"HTTP {status} for url '{_redacted_url(resp.url)}'"
    location = (resp.headers.get("location") or "").strip()
    if location:
        msg += f"; redirect location: '{redact_url(location)}'"
    return msg


def _infer_owner_repo_from_feed_url(feed_url: str) -> tuple[str | None, str | None]:
    """Infer owner/repo from a Forgejo repo RSS URL.

    Example: https://git.example.com/Org/repo.rss
    """
    try:
        u = urlparse(feed_url)
        parts = [p for p in u.path.split("/") if p]
        if len(parts) >= 2:
            owner = parts[0]
            repo = parts[1]
            if repo.endswith(".rss"):
                repo = repo[: -len(".rss")]
            if repo.endswith(".atom"):
                repo = repo[: -len(".atom")]
            return owner, repo
        return None, None
    except Exception:
        return None, None


def _is_probably_rss(resp: httpx.Response) -> bool:
    ct = (resp.headers.get("content-type") or "").lower()
    if "xml" in ct or "rss" in ct or "atom" in ct:
        return True
    txt = (resp.text or "").lstrip()[:200].lower()
    return "<rss" in txt or "<channel" in txt


def _parse_rss_items(xml: str, max_items: int = 200) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    channel = root.find("channel")
    if channel is None:
        channel = root.find("./{*}channel")
    if channel is None:
        return []

    items = channel.findall("item")
    if not items:
        items = channel.findall("./{*}item")

    out: list[dict[str, Any]] = []
    content_ns = "{http://purl.org/rss/1.0/modules/content/}encoded"
    for it in items[:max_items]:
        title = (it.findtext("title") or it.findtext("{*}title") or "").strip()
        link = (it.findtext("link") or it.findtext("{*}link") or "").strip()
        description = (
            it.findtext("description") or it.findtext("{*}description") or ""
        ).strip()
        encoded = (it.findtext(content_ns) or "").strip()
        author = (
            it.findtext("author") or it.findtext("{*}author") or ""
        ).strip() or None
        guid = (it.findtext("guid") or it.findtext("{*}guid") or "").strip()
        pub = (it.findtext("pubDate") or it.findtext("{*}pubDate") or "").strip()
        out.append(
            {
                "title": title,
                "link": link,
                "description": description,
                "content": encoded,
                "author": author,
                "guid": guid,
                "pubDate": pub,
            }
        )
    return out


def _classify_from_title(title: str) -> tuple[str, str, int]:
    t = (title or "").lower()
    action = "activity"
    outcome = "info"
    severity = 3
    if "merged pull request" in t or (" merged " in f" {t} "):
        action = "pull_merged"
        outcome = "success"
        severity = 2
    elif "opened" in t and "pull request" in t:
        action = "pull_opened"
    elif "commented" in t:
        action = "comment"
    elif "approved" in t:
        action = "review_approved"
        outcome = "success"
        severity = 2
    elif "pushed" in t:
        action = "push"
        outcome = "success"
        severity = 2
    return action, outcome, severity
