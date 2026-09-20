from __future__ import annotations

"""Forgejo (Gitea-compatible) ingestion.

This ingestor is intentionally pragmatic:

* Preferred: call the authenticated REST API endpoint
  `/api/v1/repos/{owner}/{repo}/activities/feeds` (works with PATs).
* Fallback: try to fetch the `.rss` feed URL directly and parse RSS 2.0.

Why both?
Some Gitea/Forgejo instances require a browser session cookie for private RSS
feeds and redirect PAT-authenticated RSS requests to `/user/login`. Using the
REST API avoids that and is typically the most reliable for private repos.
"""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
import json
from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl
from xml.etree import ElementTree as ET

import httpx
import yaml
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import IngestionCursor
from app.ingest.common import fingerprint, store_event_with_artifact, is_safe_url
from app.security.redaction import redact_url


def load_forgejo_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


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


def _auth_headers() -> dict[str, str]:
    """Build headers for Forgejo auth.

    Forgejo documents support for `Authorization: token <PAT>` and
    `Authorization: Bearer <PAT>` for API routes. For non-API routes (like RSS),
    instances may or may not accept these; we still try.
    """

    h: dict[str, str] = {}
    mode = (settings.forgejo_auth_mode or "token").lower()
    token = (settings.forgejo_token or "").strip()
    if mode in ("none", ""):
        return h
    if mode == "cookie" and settings.forgejo_cookie:
        h["Cookie"] = settings.forgejo_cookie
        return h
    if token:
        if mode == "bearer":
            h["Authorization"] = f"Bearer {token}"
        else:
            h["Authorization"] = f"token {token}"
    return h


def _basic_auth() -> tuple[str, str] | None:
    if (settings.forgejo_username or "").strip() and (
        settings.forgejo_token or ""
    ).strip():
        return (settings.forgejo_username.strip(), settings.forgejo_token.strip())
    return None


def _with_query_token(url: str) -> str:
    token = (settings.forgejo_token or "").strip()
    if not token:
        return url
    u = urlparse(url)
    qs = dict(parse_qsl(u.query, keep_blank_values=True))
    qs.setdefault("token", token)
    return urlunparse(u._replace(query=urlencode(qs)))


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


def _fetch_rss(feed_url: str) -> str:
    if not is_safe_url(feed_url):
        raise ValueError("Invalid or unsafe Forgejo feed URL")

    headers = {"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.1"}
    headers.update(_auth_headers())

    auth = (
        _basic_auth() if (settings.forgejo_auth_mode or "").lower() == "basic" else None
    )
    url = (
        _with_query_token(feed_url)
        if (settings.forgejo_auth_mode or "").lower() == "query"
        else feed_url
    )

    with httpx.Client(
        timeout=30.0,
        follow_redirects=False,
        verify=True,
        headers=headers,
        auth=auth,
    ) as c:
        r = c.get(url)
        status_error = _response_status_error(r)
        if status_error:
            raise RuntimeError(status_error)
        if not _is_probably_rss(r):
            raise RuntimeError(
                "Forgejo feed did not look like RSS/XML (may have redirected to login)."
            )
        return r.text


def _fetch_api_activities(
    *,
    base_url: str,
    owner: str,
    repo: str,
    max_pages: int = 10,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch Forgejo repository activity from the authenticated REST API."""

    if not is_safe_url(base_url):
        raise ValueError("Invalid or unsafe Forgejo base URL")

    headers = {"Accept": "application/json"}
    headers.update(_auth_headers())
    auth = (
        _basic_auth() if (settings.forgejo_auth_mode or "").lower() == "basic" else None
    )

    all_items: list[dict[str, Any]] = []
    with httpx.Client(
        timeout=30.0,
        follow_redirects=False,
        verify=True,
        headers=headers,
        auth=auth,
    ) as c:
        page = 1
        while page <= max_pages:
            r = c.get(
                f"{base_url.rstrip('/')}/api/v1/repos/{owner}/{repo}/activities/feeds",
                params={"page": str(page), "limit": str(limit)},
            )
            status_error = _response_status_error(r)
            if status_error:
                raise RuntimeError(status_error)
            data = r.json() or []
            if not isinstance(data, list) or not data:
                break
            all_items.extend(data)
            if len(data) < limit:
                break
            page += 1

    return all_items


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


def _store_api_activity_items(
    db: Session,
    cur: IngestionCursor,
    *,
    all_items: list[dict[str, Any]],
    cursor_name: str,
    feed_url: str,
    host: str,
    owner: str,
    repo: str,
    label: str | None,
    since: datetime,
    cursor_last: datetime | None,
    mode: str = "api",
) -> dict[str, Any]:
    created = 0
    newest_ts = cursor_last

    # Forgejo returns newest-first; process oldest-first for cursor stability.
    for a in reversed(all_items):
        ts = (
            _parse_iso_ts(a.get("created"))
            or _parse_iso_ts(a.get("created_at"))
            or _parse_iso_ts(a.get("updated"))
            or _parse_iso_ts(a.get("updated_at"))
        )
        if not ts:
            continue
        if ts < since:
            continue
        if cursor_last and ts <= cursor_last:
            continue

        act_user = a.get("act_user") or a.get("user") or {}
        actor = None
        if isinstance(act_user, dict):
            actor = (
                act_user.get("login")
                or act_user.get("username")
                or act_user.get("name")
            )
        actor = actor or a.get("actor")

        op = str(a.get("op_type") or a.get("type") or "activity")
        content = a.get("content")
        title = a.get("title")
        summary_bits = [f"forgejo {host}", f"{owner}/{repo}", op]
        if actor:
            summary_bits.append(f"by {actor}")
        if title:
            summary_bits.append(str(title))
        elif content and isinstance(content, str):
            summary_bits.append(content[:120])
        summary = " ".join([b for b in summary_bits if b])
        if label:
            summary = f"[{label}] {summary}"

        payload_bytes = json.dumps(a, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        a_id = str(a.get("id") or a.get("_id") or "")
        ext_id = fingerprint([cursor_name, a_id, op], payload_bytes)
        key = f"forgejo/api/{host}/{owner}/{repo}/{ts.date().isoformat()}/{ext_id}.json"

        res = store_event_with_artifact(
            db,
            timestamp=ts,
            source="forgejo",
            system=label or f"{host}/{owner}/{repo}",
            actor=actor,
            action=op,
            outcome="info",
            severity=3,
            summary=summary,
            raw_pointer={
                "forgejo": {"api": True, "owner": owner, "repo": repo, "host": host}
            },
            normalized_payload={"activity": a, "feed_url": feed_url},
            external_id=ext_id,
            artifact_kind="forgejo_activity",
            artifact_bytes=payload_bytes,
            artifact_content_type="application/json",
            artifact_key=key,
            captured_by="keen:forgejo",
        )
        if not res.get("deduped"):
            created += 1

        if (newest_ts is None) or (ts > newest_ts):
            newest_ts = ts

    cur.last_ts = newest_ts or cur.last_ts
    cur.updated_at = datetime.utcnow()
    db.add(cur)
    db.commit()

    return {
        "feed": feed_url,
        "created_events": created,
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
        "mode": mode,
    }


def ingest_forgejo_repo_feed(
    db: Session,
    *,
    feed_url: str,
    label: str | None = None,
    lookback_days: int = 14,
    max_items: int = 200,
) -> dict[str, Any]:
    """Ingest a Forgejo repo RSS feed.

    Prefer the REST API for private repositories because Forgejo RSS routes may
    require a browser session cookie and redirect PAT-authenticated requests to
    the login page. Fall back to RSS when the API cannot be used.
    """

    u = urlparse(feed_url)
    cursor_name = f"forgejo:feed:{u.netloc}{u.path}"
    cur = (
        db.query(IngestionCursor)
        .filter(IngestionCursor.name == cursor_name)
        .one_or_none()
    )
    if cur is None:
        # Store a redacted URL so query tokens are never persisted in DB.
        cur = IngestionCursor(
            name=cursor_name, last_ts=None, meta={"url": redact_url(feed_url)}
        )
        db.add(cur)
        db.flush()

    now = datetime.utcnow()
    since = (
        _to_utc_naive(cur.last_ts)
        if cur.last_ts
        else (now - timedelta(days=lookback_days))
    )
    cursor_last = _to_utc_naive(cur.last_ts) if cur.last_ts else None

    owner, repo = _infer_owner_repo_from_feed_url(feed_url)
    host = u.netloc
    base_url = settings.forgejo_base_url.strip()
    if not base_url and u.scheme and u.netloc:
        base_url = f"{u.scheme}://{u.netloc}"

    api_error: str | None = None
    rss_error: str | None = None

    # --- Prefer the API for Forgejo repo activity ---
    # Private Forgejo/Gitea RSS routes are web routes. Many instances ignore PAT
    # Authorization headers there and redirect to /user/login, while the REST API
    # accepts the same PAT cleanly. Try the API first whenever we can infer it.
    if base_url and owner and repo:
        try:
            all_items = _fetch_api_activities(
                base_url=base_url,
                owner=owner,
                repo=repo,
            )
            return _store_api_activity_items(
                db,
                cur,
                all_items=all_items,
                cursor_name=cursor_name,
                feed_url=feed_url,
                host=host,
                owner=owner,
                repo=repo,
                label=label,
                since=since,
                cursor_last=cursor_last,
                mode="api",
            )
        except Exception as e:
            db.rollback()
            api_error = str(e)
    else:
        api_error = (
            "Could not infer base_url/owner/repo for API ingest. Provide "
            "KEEN_FORGEJO_BASE_URL or use a standard /owner/repo.rss URL."
        )

    # --- Fallback to RSS when the API is unavailable/unsupported ---
    try:
        xml = _fetch_rss(feed_url)
        items = _parse_rss_items(xml, max_items=max_items)

        created = 0
        newest_ts = cursor_last

        # RSS is usually newest-first
        for it in reversed(items):
            ts = _parse_rfc822_ts(it.get("pubDate"))
            if not ts:
                continue
            if ts < since:
                continue
            if cursor_last and ts <= cursor_last:
                continue

            title = it.get("title") or ""
            action, outcome, severity = _classify_from_title(title)
            author = it.get("author")
            link = it.get("link")
            guid = it.get("guid") or link or title

            summary = (
                f"forgejo {host}: {title}" if title else f"forgejo {host} rss item"
            )
            if label:
                summary = f"[{label}] {summary}"

            norm = {
                "feed_url": feed_url,
                "host": host,
                "owner": owner,
                "repo": repo,
                "item": it,
            }
            payload_bytes = json.dumps(
                norm, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            ext_id = fingerprint([cursor_name, str(guid)], payload_bytes)
            key = f"forgejo/feeds/{host}/{owner or 'unknown'}/{repo or 'unknown'}/{ts.date().isoformat()}/{ext_id}.json"

            res = store_event_with_artifact(
                db,
                timestamp=ts,
                source="forgejo",
                system=label or (f"{host}/{owner}/{repo}" if owner and repo else host),
                actor=author,
                action=action,
                outcome=outcome,
                severity=severity,
                summary=summary,
                raw_pointer={"forgejo": {"feed": feed_url, "link": link, "guid": guid}},
                normalized_payload={"rss": norm},
                external_id=ext_id,
                artifact_kind="forgejo_rss",
                artifact_bytes=payload_bytes,
                artifact_content_type="application/json",
                artifact_key=key,
                captured_by="keen:forgejo",
            )
            if not res.get("deduped"):
                created += 1

            if (newest_ts is None) or (ts > newest_ts):
                newest_ts = ts

        cur.last_ts = newest_ts or cur.last_ts
        cur.updated_at = datetime.utcnow()
        db.add(cur)
        db.commit()
        result = {
            "feed": feed_url,
            "created_events": created,
            "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
            "mode": "rss_fallback",
        }
        if api_error:
            result["api_error"] = api_error
        return result
    except Exception as e:
        db.rollback()
        rss_error = str(e)

    return {
        "feed": feed_url,
        "created_events": 0,
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
        "mode": "failed",
        "api_error": api_error,
        "rss_error": rss_error,
        "error": "Could not ingest Forgejo activity via API or RSS.",
    }


def ingest_forgejo_all(db: Session) -> list[dict[str, Any]]:
    if not settings.forgejo_enabled:
        return [{"skipped": True, "reason": "KEEN_FORGEJO_ENABLED=false"}]
    cfg = load_forgejo_config(settings.forgejo_config_path)
    out: list[dict[str, Any]] = []
    for f in cfg.get("feeds", []) or []:
        url = f.get("url") or f.get("feed")
        if not url:
            continue
        try:
            out.append(ingest_forgejo_repo_feed(db, feed_url=url, label=f.get("label")))
        except Exception as e:
            db.rollback()
            out.append({"feed": url, "error": str(e)})
    return out
