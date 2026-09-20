from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import json
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx
import yaml
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import IngestionCursor
from app.ingest.common import fingerprint, store_event_with_artifact
from app.security.redaction import redact_url


def load_github_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _client() -> httpx.Client:
    # GitHub recommends specifying an explicit API version header.
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return httpx.Client(
        base_url=settings.github_base_url.rstrip("/"),
        headers=headers,
        timeout=30.0,
        verify=True,
    )


def _basic_auth() -> tuple[str, str] | None:
    """Return (username, token) for Basic Auth when configured.

    GitHub returns some legacy resources (notably private Atom feeds) only via Basic Auth.
    """

    if settings.github_username and settings.github_token:
        return (settings.github_username, settings.github_token)
    return None


def _to_utc_naive(dt: datetime) -> datetime:
    """Convert a datetime to *UTC naive*.

    Our DB columns are TIMESTAMP WITHOUT TIME ZONE. We therefore normalize
    ingester comparisons and cursor storage to naive UTC to avoid
    offset-aware vs offset-naive comparison errors.
    """

    if dt.tzinfo is None:
        # Treat naive values as UTC.
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_iso_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    # Common GitHub forms: 2026-01-07T00:09:08Z or with offset
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return _to_utc_naive(dt)
    except Exception:
        return None


def _fetch_pages(
    c: httpx.Client, path: str, *, per_page: int = 100, max_pages: int = 3
) -> list[dict[str, Any]]:
    """Fetch up to `max_pages` pages from a GitHub REST endpoint."""

    items: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        r = c.get(path, params={"per_page": str(per_page), "page": str(page)})
        r.raise_for_status()
        batch = r.json() or []
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        # GitHub returns up to 100 per page; stop early if we received less.
        if len(batch) < per_page:
            break
    return items


def _event_external_id(
    cursor_name: str, e: dict[str, Any], payload_bytes: bytes
) -> str:
    # Prefer GitHub's native event id so the same event de-dupes across
    # /orgs/{org}/events and /repos/{owner}/{repo}/events.
    eid = e.get("id")
    if isinstance(eid, str) and eid.strip():
        return eid.strip()
    return fingerprint([cursor_name, str(e.get("type") or "")], payload_bytes)


def _summarize_event(e: dict[str, Any], *, fallback_repo: str | None = None) -> str:
    etype = (e.get("type") or "").strip() or "Event"
    actor = ((e.get("actor") or {}).get("login") or "").strip()
    repo = ((e.get("repo") or {}).get("name") or fallback_repo or "").strip()
    payload = e.get("payload") or {}

    if etype == "PushEvent":
        size = payload.get("size")
        ref = payload.get("ref") or ""
        branch = (ref.split("/")[-1] if ref else "").strip()
        bits = ["GitHub push"]
        if size is not None:
            bits.append(f"{size} commit(s)")
        if repo:
            bits.append(f"to {repo}")
        if branch:
            bits.append(f"({branch})")
        if actor:
            bits.append(f"by {actor}")
        return " ".join(bits)

    if etype == "PullRequestEvent":
        action = payload.get("action") or "updated"
        pr = payload.get("pull_request") or {}
        num = payload.get("number") or pr.get("number")
        title = (pr.get("title") or "").strip()
        bits = [f"GitHub PR {action}"]
        if repo and num is not None:
            bits.append(f"{repo}#{num}")
        elif repo:
            bits.append(repo)
        if title:
            bits.append(f'"{title}"')
        if actor:
            bits.append(f"by {actor}")
        return " ".join(bits)

    if etype in ("IssuesEvent", "IssueCommentEvent"):
        action = payload.get("action") or "updated"
        issue = payload.get("issue") or {}
        num = issue.get("number")
        title = (issue.get("title") or "").strip()
        if etype == "IssueCommentEvent":
            bits = [f"GitHub comment {action}"]
        else:
            bits = [f"GitHub issue {action}"]
        if repo and num is not None:
            bits.append(f"{repo}#{num}")
        elif repo:
            bits.append(repo)
        if title:
            bits.append(f'"{title}"')
        if actor:
            bits.append(f"by {actor}")
        return " ".join(bits)

    if etype == "ReleaseEvent":
        action = payload.get("action") or "published"
        rel = payload.get("release") or {}
        tag = (rel.get("tag_name") or "").strip()
        name = (rel.get("name") or "").strip()
        bits = [f"GitHub release {action}"]
        if repo:
            bits.append(repo)
        if tag:
            bits.append(tag)
        if name and name != tag:
            bits.append(f'"{name}"')
        if actor:
            bits.append(f"by {actor}")
        return " ".join(bits)

    if etype == "DeleteEvent":
        action = payload.get("ref_type") or ""
        ref = payload.get("ref") or ""
        name = (rel.get("name") or "").strip()
        ref_type = payload.get("ref_type") or ""
        bits = [f"GitHub {ref_type} deletion"]
        if repo:
            bits.append(repo)
        if tag:
            bits.append(tag)
        if name and name != tag:
            bits.append(f'"{name}"')
        if actor:
            bits.append(f"by {actor}")
        return " ".join(bits)

    # Generic fallback
    bits = [f"GitHub {etype}"]
    if repo:
        bits.append(repo)
    if actor:
        bits.append(f"by {actor}")
    return " ".join(bits)


def ingest_github_repo(
    db: Session, owner: str, repo: str, label: str | None = None
) -> dict[str, Any]:
    cursor_name = f"github:{owner}/{repo}"
    cur = (
        db.query(IngestionCursor)
        .filter(IngestionCursor.name == cursor_name)
        .one_or_none()
    )
    if cur is None:
        cur = IngestionCursor(name=cursor_name, last_ts=None, meta={})
        db.add(cur)
        db.flush()

    with _client() as c:
        events = _fetch_pages(
            c, f"/repos/{owner}/{repo}/events", per_page=100, max_pages=3
        )

    created = 0
    cursor_last = _to_utc_naive(cur.last_ts) if cur.last_ts else None
    newest_ts = cursor_last

    # GitHub returns newest-first; process oldest-first for cursor stability
    for e in reversed(events):
        # created_at is ISO string
        created_at = e.get("created_at")
        if not created_at:
            continue
        ts = _parse_iso_ts(created_at)
        if not ts:
            continue
        # Use < (not <=) to avoid edge misses when multiple events share the
        # same timestamp; de-dupe handles repeats safely.
        if cursor_last and ts < cursor_last:
            continue

        etype = e.get("type") or None
        actor = (e.get("actor") or {}).get("login")
        action = etype
        outcome = "info"
        summary = _summarize_event(e, fallback_repo=f"{owner}/{repo}")
        if label:
            summary = f"[{label}] {summary}"

        payload_bytes = json.dumps(e, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        ext_id = _event_external_id(cursor_name, e, payload_bytes)

        key = f"github/repo-events/{owner}/{repo}/{ts.date().isoformat()}/{ext_id}.json"
        res = store_event_with_artifact(
            db,
            timestamp=ts,
            source="github",
            system=label or f"{owner}/{repo}",
            actor=actor,
            action=action,
            outcome=outcome,
            severity=3,
            summary=summary,
            raw_pointer={
                "github": {
                    "endpoint": "repo_events",
                    "owner": owner,
                    "repo": repo,
                    "id": e.get("id"),
                    "type": etype,
                    "created_at": e.get("created_at"),
                }
            },
            normalized_payload={"github_event": e},
            external_id=ext_id,
            artifact_kind="github_event",
            artifact_bytes=payload_bytes,
            artifact_content_type="application/json",
            artifact_key=key,
            captured_by="keen:github",
        )
        if not res.get("deduped"):
            created += 1

        if (newest_ts is None) or (ts > newest_ts):
            newest_ts = ts

    # advance cursor
    cur.last_ts = newest_ts or cur.last_ts
    cur.updated_at = datetime.utcnow()
    db.add(cur)
    db.commit()

    return {
        "repo": f"{owner}/{repo}",
        "created_events": created,
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
    }


def _detect_username(c: httpx.Client) -> str | None:
    """Best-effort detect the authenticated user login."""
    try:
        r = c.get("/user")
        r.raise_for_status()
        login = (r.json() or {}).get("login")
        if isinstance(login, str) and login.strip():
            return login.strip()
    except Exception:
        return None
    return None


def ingest_github_org_events(
    db: Session,
    org: str,
    *,
    label: str | None = None,
    mode: str = "auto",
    per_page: int = 100,
    max_pages: int = 3,
) -> dict[str, Any]:
    """Ingest GitHub org-scoped events.

    Supports two endpoints:
      - /orgs/{org}/events (public org events)
      - /users/{username}/events/orgs/{org} (authenticated user's org dashboard)
    """

    cursor_name = f"github:org-events:{org}:{mode}"
    cur = (
        db.query(IngestionCursor)
        .filter(IngestionCursor.name == cursor_name)
        .one_or_none()
    )
    if cur is None:
        cur = IngestionCursor(
            name=cursor_name, last_ts=None, meta={"org": org, "mode": mode}
        )
        db.add(cur)
        db.flush()

    with _client() as c:
        endpoint_path: str
        chosen_mode = mode

        if mode == "user" or (
            mode == "auto" and (settings.github_username or settings.github_token)
        ):
            username = settings.github_username or _detect_username(c)
            if username:
                endpoint_path = f"/users/{username}/events/orgs/{org}"
                chosen_mode = "user"
            else:
                endpoint_path = f"/orgs/{org}/events"
                chosen_mode = "org"
        else:
            endpoint_path = f"/orgs/{org}/events"
            chosen_mode = "org"

        events = _fetch_pages(c, endpoint_path, per_page=per_page, max_pages=max_pages)

    created = 0
    cursor_last = _to_utc_naive(cur.last_ts) if cur.last_ts else None
    newest_ts = cursor_last

    for e in reversed(events):
        ts = _parse_iso_ts(e.get("created_at"))
        if not ts:
            continue
        if cursor_last and ts < cursor_last:
            continue

        repo = ((e.get("repo") or {}).get("name") or "").strip() or None
        actor = (e.get("actor") or {}).get("login")
        etype = e.get("type") or None

        summary = _summarize_event(e, fallback_repo=repo)
        if label:
            summary = f"[{label}] {summary}"

        payload_bytes = json.dumps(e, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        ext_id = _event_external_id(cursor_name, e, payload_bytes)
        key = f"github/org-events/{org}/{ts.date().isoformat()}/{ext_id}.json"

        res = store_event_with_artifact(
            db,
            timestamp=ts,
            source="github",
            system=repo or (label or f"org:{org}"),
            actor=actor,
            action=etype,
            outcome="info",
            severity=3,
            summary=summary,
            raw_pointer={
                "github": {
                    "endpoint": "org_events",
                    "mode": chosen_mode,
                    "org": org,
                    "repo": repo,
                    "id": e.get("id"),
                    "type": etype,
                    "created_at": e.get("created_at"),
                }
            },
            normalized_payload={"github_event": e},
            external_id=ext_id,
            artifact_kind="github_event",
            artifact_bytes=payload_bytes,
            artifact_content_type="application/json",
            artifact_key=key,
            captured_by="keen:github",
        )
        if not res.get("deduped"):
            created += 1

        if (newest_ts is None) or (ts > newest_ts):
            newest_ts = ts

    cur.last_ts = newest_ts or cur.last_ts
    cur.meta = {**(cur.meta or {}), "mode": chosen_mode}
    cur.updated_at = datetime.utcnow()
    db.add(cur)
    db.commit()

    return {
        "org": org,
        "mode": chosen_mode,
        "created_events": created,
        "events_seen": len(events),
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
    }


def _list_org_repos(
    c: httpx.Client,
    org: str,
    *,
    per_page: int = 100,
    max_pages: int = 20,
    exclude_archived: bool = True,
    exclude_forks: bool = True,
) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        r = c.get(
            f"/orgs/{org}/repos",
            params={
                "per_page": str(per_page),
                "page": str(page),
                "type": "all",
                "sort": "updated",
                "direction": "desc",
            },
        )
        r.raise_for_status()
        batch = r.json() or []
        if not isinstance(batch, list) or not batch:
            break
        for repo in batch:
            if exclude_archived and repo.get("archived") is True:
                continue
            if exclude_forks and repo.get("fork") is True:
                continue
            repos.append(repo)
        if len(batch) < per_page:
            break
    return repos


def ingest_github_org_repo_events(
    db: Session,
    org: str,
    *,
    label: str | None = None,
    repo_limit: int | None = None,
    exclude_archived: bool = True,
    exclude_forks: bool = True,
) -> dict[str, Any]:
    """Enumerate org repos then ingest /repos/{owner}/{repo}/events for each."""

    with _client() as c:
        repos = _list_org_repos(
            c,
            org,
            exclude_archived=exclude_archived,
            exclude_forks=exclude_forks,
        )

    if repo_limit and repo_limit > 0:
        repos = repos[:repo_limit]

    created_total = 0
    per_repo: list[dict[str, Any]] = []
    for r in repos:
        full = (r.get("full_name") or "").strip()  # owner/repo
        if not full or "/" not in full:
            continue
        owner, repo = full.split("/", 1)
        try:
            res = ingest_github_repo(db, owner, repo, label=label)
            created_total += int(res.get("created_events") or 0)
            per_repo.append(res)
        except Exception as e:
            db.rollback()
            per_repo.append({"repo": full, "error": str(e)})

    return {
        "org": org,
        "repo_count": len(repos),
        "created_events": created_total,
        "per_repo": per_repo,
    }


def ingest_github_org_auditlog(
    db: Session,
    org: str,
    label: str | None = None,
    lookback_days: int = 7,
    per_page: int = 100,
) -> dict[str, Any]:
    """Ingest GitHub *organization* audit log entries.

    This avoids enumerating every repo. It requires an org owner token with the `read:audit_log` scope.
    """

    cursor_name = f"github:audit:{org}"
    cur = (
        db.query(IngestionCursor)
        .filter(IngestionCursor.name == cursor_name)
        .one_or_none()
    )
    if cur is None:
        cur = IngestionCursor(name=cursor_name, last_ts=None, meta={})
        db.add(cur)
        db.flush()

    now = datetime.utcnow()
    since = (
        _to_utc_naive(cur.last_ts)
        if cur.last_ts
        else (now - timedelta(days=lookback_days))
    )

    # GitHub's audit log endpoint supports a search phrase with created timestamps.
    # Use a date-level filter to keep requests simple; cursor will do the precise cutoff.
    phrase = f"created:>={since.date().isoformat()}"

    with _client() as c:
        r = c.get(
            f"/orgs/{org}/audit-log",
            params={
                "per_page": str(per_page),
                "include": "all",
                "order": "asc",
                "phrase": phrase,
            },
        )
        r.raise_for_status()
        entries = r.json() or []

    created = 0
    cursor_last = _to_utc_naive(cur.last_ts) if cur.last_ts else None
    newest_ts = cursor_last

    for e in entries:
        ts = _parse_iso_ts(e.get("@timestamp") or e.get("created_at"))
        if not ts:
            continue
        if cursor_last and ts <= cursor_last:
            continue

        actor = e.get("actor") or e.get("user")
        action = e.get("action") or "audit"
        # Try to give the summary a bit of meaning without being overly specific.
        repo = e.get("repo") or e.get("repository")
        target = e.get("target") or e.get("user") or e.get("actor")
        parts = [f"github org {org}", action]
        if repo:
            parts.append(f"repo={repo}")
        if target and target != actor:
            parts.append(f"target={target}")
        if actor:
            parts.append(f"by {actor}")
        summary = " ".join(parts)
        if label:
            summary = f"[{label}] {summary}"

        payload_bytes = json.dumps(e, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        ext_id = fingerprint(
            [cursor_name, str(e.get("_id") or e.get("id") or ""), str(action)],
            payload_bytes,
        )
        key = f"github/audit/{org}/{ts.date().isoformat()}/{ext_id}.json"

        res = store_event_with_artifact(
            db,
            timestamp=ts,
            source="github",
            system=label or f"org:{org}",
            actor=actor,
            action=str(action),
            outcome="info",
            severity=3,
            summary=summary,
            raw_pointer={
                "github": {
                    "org": org,
                    "audit": True,
                    "action": action,
                    "id": e.get("_id") or e.get("id"),
                }
            },
            normalized_payload={"audit": e},
            external_id=ext_id,
            artifact_kind="github_audit",
            artifact_bytes=payload_bytes,
            artifact_content_type="application/json",
            artifact_key=key,
            captured_by="keen:github",
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
        "org": org,
        "created_events": created,
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
    }


def ingest_github_atom_feed(
    db: Session,
    feed_url: str,
    label: str | None = None,
    lookback_days: int = 30,
    max_entries: int = 200,
) -> dict[str, Any]:
    """Ingest an Atom feed (e.g. org dashboard feed).

    Note: private GitHub feeds require Basic Auth.
    """

    # Normalize the cursor name based on the URL path.
    u = urlparse(feed_url)
    cursor_name = f"github:feed:{u.netloc}{u.path}"
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

    auth = _basic_auth()
    with httpx.Client(
        timeout=30.0, follow_redirects=False, verify=True, auth=auth
    ) as c:
        r = c.get(feed_url, headers={"Accept": "application/atom+xml"})
        r.raise_for_status()
        xml = r.text

    root = ET.fromstring(xml)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns)[:max_entries]

    created = 0
    cursor_last = _to_utc_naive(cur.last_ts) if cur.last_ts else None
    newest_ts = cursor_last
    newest_in_feed: datetime | None = None

    def _etype_from_eid(eid: str | None) -> str | None:
        """Extract a stable event type from a GitHub Atom entry id.

        Examples:
          tag:github.com,2008:push/7265684130      -> push
          tag:github.com,2008:pr_created/5606866010 -> pr_created
        """
        if not eid:
            return None
        try:
            tail = eid.split(":")[-1]
            et = (tail.split("/")[0] or "").strip()
            return et or None
        except Exception:
            return None

    def _repo_from_link(href: str | None) -> str | None:
        if not href:
            return None
        try:
            u2 = urlparse(href)
            parts = (u2.path or "").strip("/").split("/")
            if len(parts) >= 2:
                owner, repo = parts[0], parts[1]
                if owner and repo:
                    return f"{owner}/{repo}"
        except Exception:
            return None
        return None

    # Atom feeds are usually newest-first.
    for entry in reversed(entries):
        updated = entry.findtext("a:updated", default=None, namespaces=ns)
        ts = _parse_iso_ts(updated)
        if not ts:
            continue

        if (newest_in_feed is None) or (ts > newest_in_feed):
            newest_in_feed = ts

        if ts < since:
            continue
        if cursor_last and ts <= cursor_last:
            continue

        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        link = None
        for l in entry.findall("a:link", ns):
            href = l.attrib.get("href")
            if href:
                link = href
                break
        author = (
            entry.findtext("a:author/a:name", default=None, namespaces=ns) or ""
        ).strip() or None
        eid = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()

        etype = _etype_from_eid(eid)
        repo = _repo_from_link(link)

        summary = f"github feed: {title}" if title else "github feed entry"
        if label:
            summary = f"[{label}] {summary}"

        norm = {
            "id": eid,
            "title": title,
            "updated": updated,
            "author": author,
            "link": link,
            "event_type": etype,
            "repo": repo,
            "feed_url": feed_url,
        }
        payload_bytes = json.dumps(norm, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        ext_id = fingerprint([cursor_name, eid or title], payload_bytes)
        key = f"github/feeds/{ts.date().isoformat()}/{ext_id}.json"

        res = store_event_with_artifact(
            db,
            timestamp=ts,
            source="github",
            system=repo or (label or "feed"),
            actor=author,
            action=etype or "feed_entry",
            outcome="info",
            severity=2,
            summary=summary,
            raw_pointer={
                "github": {
                    "feed": feed_url,
                    "label": label,
                    "id": eid,
                    "link": link,
                    "event_type": etype,
                    "repo": repo,
                }
            },
            normalized_payload={"feed": norm},
            external_id=ext_id,
            artifact_kind="github_feed",
            artifact_bytes=payload_bytes,
            artifact_content_type="application/json",
            artifact_key=key,
            captured_by="keen:github",
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
        "entries_seen": len(entries),
        "newest_in_feed_ts": newest_in_feed.isoformat() if newest_in_feed else None,
    }


def ingest_github_all(db: Session) -> list[dict[str, Any]]:
    if not settings.github_enabled:
        return [{"skipped": True, "reason": "KEEN_GITHUB_ENABLED=false"}]
    cfg = load_github_config(settings.github_config_path)
    out = []

    # --- Org-level ingestion ---
    # Supports:
    #   - org events API
    #   - per-repo events API (enumerate repos in org)
    #   - (optional) org audit log (requires `read:audit_log` scope)
    for o in cfg.get("organizations", []) or cfg.get("orgs", []) or []:
        org = o.get("org") or o.get("name") or o.get("owner")
        if not org:
            continue

        include_raw = (o.get("include") or "events").lower()
        # split on commas / pluses / spaces
        include_parts = {
            p.strip()
            for p in include_raw.replace("+", ",").replace(" ", ",").split(",")
            if p.strip()
        }
        if "all" in include_parts:
            include_parts |= {"events", "repo_events"}

        label = o.get("label")
        mode = (o.get("org_events_mode") or o.get("mode") or "auto").lower()
        repo_limit = o.get("repo_limit")
        exclude_archived = bool(o.get("exclude_archived", True))
        exclude_forks = bool(o.get("exclude_forks", True))

        # 1) Org events
        if include_parts & {"events", "org_events", "org-events"}:
            try:
                out.append(
                    ingest_github_org_events(db, org=org, label=label, mode=mode)
                )
            except Exception as e:
                db.rollback()
                out.append({"org": org, "error": str(e), "stage": "org_events"})

        # 2) Per-repo events (enumerate repos in org)
        if include_parts & {"repo_events", "repos", "repo-events", "repo"}:
            try:
                out.append(
                    ingest_github_org_repo_events(
                        db,
                        org=org,
                        label=label,
                        repo_limit=repo_limit,
                        exclude_archived=exclude_archived,
                        exclude_forks=exclude_forks,
                    )
                )
            except Exception as e:
                db.rollback()
                out.append({"org": org, "error": str(e), "stage": "repo_events"})

        # 3) Optional: audit log (Enterprise / org owner token with read:audit_log)
        if include_parts & {"audit", "audit_log", "audit-log"}:
            try:
                out.append(ingest_github_org_auditlog(db, org=org, label=label))
            except Exception as e:
                db.rollback()
                out.append({"org": org, "error": str(e), "stage": "audit_log"})

    # --- Optional: Atom feeds ---
    for f in cfg.get("feeds", []) or []:
        url = f.get("url")
        if not url:
            continue
        try:
            out.append(ingest_github_atom_feed(db, feed_url=url, label=f.get("label")))
        except Exception as e:
            db.rollback()
            out.append({"feed": url, "error": str(e)})

    # --- Repo-level ingestion (legacy / small orgs) ---
    for r in cfg.get("repos", []) or []:
        try:
            out.append(ingest_github_repo(db, r["owner"], r["repo"], r.get("label")))
        except Exception as e:
            db.rollback()
            out.append({"repo": f"{r.get('owner')}/{r.get('repo')}", "error": str(e)})
    return out
