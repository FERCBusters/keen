"""Repository activity collection for Gitea and Gitea-compatible forges.

Forgejo and Gitea expose the same REST and RSS routes, so one collector serves
both; each product keeps its own source name, settings, cursors and evidence.

* Preferred: call the authenticated REST API endpoint
  `/api/v1/repos/{owner}/{repo}/activities/feeds` (works with PATs).
* Fallback: try to fetch the `.rss` feed URL directly and parse RSS 2.0.

Some instances require a browser session cookie for private RSS feeds and
redirect PAT-authenticated RSS requests to `/user/login`. Using the REST API
avoids that and is typically the most reliable for private repos.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from sqlalchemy.orm import Session

from app.core.datetime_utils import to_utc_naive, utc_now_naive
from app.db.models import IngestionCursor
from app.ingest.common import fingerprint, is_safe_url, store_event_with_artifact
from app.ingest.connections import load_document, namespace, settings
from app.ingest.git_activity import (
    _classify_from_title,
    _infer_owner_repo_from_feed_url,
    _is_probably_rss,
    _parse_iso_ts,
    _parse_rfc822_ts,
    _parse_rss_items,
    _response_status_error,
)
from app.ingest.http import client as ingestion_client
from app.ingest.http import require_origin
from app.security.errors import collection_error
from app.security.redaction import redact_url

_ACCOUNT_NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}")


class GiteaCompatibleCollector:
    """Collector for one forge product, named by its source key (e.g. ``gitea``)."""

    def __init__(self, name: str, label: str):
        self.name = name
        self.label = label

    def _setting(self, key: str):
        # Read through the connection-scoped settings, e.g. ``forgejo_token``.
        return getattr(settings, f"{self.name}_{key}")

    def load_config(self, path: str) -> dict[str, Any]:
        return load_document(self.name, path)

    def _auth_headers(self) -> dict[str, str]:
        """Build headers for API authentication.

        Forgejo and Gitea accept `Authorization: token <PAT>` and
        `Authorization: Bearer <PAT>` for API routes. For non-API routes (like
        RSS), instances may or may not accept these; we still try.
        """

        h: dict[str, str] = {}
        mode = (self._setting("auth_mode") or "token").lower()
        token = (self._setting("token") or "").strip()
        if mode in ("none", ""):
            return h
        if mode == "cookie" and self._setting("cookie"):
            h["Cookie"] = self._setting("cookie")
            return h
        if token:
            if mode == "bearer":
                h["Authorization"] = f"Bearer {token}"
            else:
                h["Authorization"] = f"token {token}"
        return h

    def _basic_auth(self) -> tuple[str, str] | None:
        username = (self._setting("username") or "").strip()
        token = (self._setting("token") or "").strip()
        if username and token:
            return (username, token)
        return None

    def _with_query_token(self, url: str) -> str:
        token = (self._setting("token") or "").strip()
        if not token:
            return url
        u = urlparse(url)
        qs = dict(parse_qsl(u.query, keep_blank_values=True))
        qs.setdefault("token", token)
        return urlunparse(u._replace(query=urlencode(qs)))

    def _auth_mode(self) -> str:
        return (self._setting("auth_mode") or "").lower()

    def _fetch_rss(self, feed_url: str) -> str:
        if self._auth_headers() or self._basic_auth() or self._setting("token"):
            require_origin(feed_url, self._setting("base_url"))
        if not is_safe_url(feed_url):
            raise ValueError(f"Invalid or unsafe {self.label} feed URL")

        headers = {"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.1"}
        headers.update(self._auth_headers())

        auth = self._basic_auth() if self._auth_mode() == "basic" else None
        url = (
            self._with_query_token(feed_url)
            if self._auth_mode() == "query"
            else feed_url
        )

        with ingestion_client(
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
                    f"{self.label} feed did not look like RSS/XML "
                    "(may have redirected to login)."
                )
            return r.text

    def _fetch_api_activities(
        self,
        *,
        base_url: str,
        owner: str,
        repo: str,
        max_pages: int = 1000,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch repository activity from the authenticated REST API."""

        if not is_safe_url(base_url):
            raise ValueError(f"Invalid or unsafe {self.label} base URL")

        headers = {"Accept": "application/json"}
        headers.update(self._auth_headers())
        auth = self._basic_auth() if self._auth_mode() == "basic" else None

        all_items: list[dict[str, Any]] = []
        with ingestion_client(
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
            else:
                raise ValueError(
                    "Activity pagination limit reached; cursor retained for retry"
                )

        return all_items

    def _store_api_activity_items(
        self,
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

        # The API returns newest-first; process oldest-first for cursor stability.
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
            summary_bits = [f"{self.name} {host}", f"{owner}/{repo}", op]
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
            key = f"{self.name}/api/{host}/{owner}/{repo}/{ts.date().isoformat()}/{ext_id}.json"

            res = store_event_with_artifact(
                db,
                timestamp=ts,
                source=self.name,
                system=label or f"{host}/{owner}/{repo}",
                actor=actor,
                action=op,
                outcome="info",
                severity=3,
                summary=summary,
                raw_pointer={
                    self.name: {
                        "api": True,
                        "owner": owner,
                        "repo": repo,
                        "host": host,
                        "feed": feed_url,
                    }
                },
                normalized_payload={"activity": a, "feed_url": feed_url},
                external_id=ext_id,
                artifact_kind=f"{self.name}_activity",
                artifact_bytes=payload_bytes,
                artifact_content_type="application/json",
                artifact_key=key,
                captured_by=f"keen:{self.name}",
            )
            if not res.get("deduped"):
                created += 1

            if (newest_ts is None) or (ts > newest_ts):
                newest_ts = ts

        cur.last_ts = newest_ts or cur.last_ts
        cur.updated_at = utc_now_naive()
        db.add(cur)
        db.commit()

        return {
            "feed": feed_url,
            "created_events": created,
            "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
            "mode": mode,
        }

    def ingest_repo_feed(
        self,
        db: Session,
        *,
        feed_url: str,
        label: str | None = None,
        lookback_days: int = 14,
        max_items: int = 200,
    ) -> dict[str, Any]:
        """Ingest one repository's activity, from the REST API or its RSS feed.

        Prefer the REST API for private repositories because RSS routes may
        require a browser session cookie and redirect PAT-authenticated requests
        to the login page. Fall back to RSS when the API cannot be used.
        """

        if self._auth_headers() or self._basic_auth() or self._setting("token"):
            require_origin(feed_url, self._setting("base_url"))
        u = urlparse(feed_url)
        cursor_name = namespace(f"{self.name}:feed:{u.netloc}{u.path}")
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

        now = utc_now_naive()
        since = (
            to_utc_naive(cur.last_ts)
            if cur.last_ts
            else (now - timedelta(days=lookback_days))
        )
        cursor_last = to_utc_naive(cur.last_ts) if cur.last_ts else None

        owner, repo = _infer_owner_repo_from_feed_url(feed_url)
        host = u.netloc
        base_url = self._setting("base_url").strip()
        if not base_url and u.scheme and u.netloc:
            base_url = f"{u.scheme}://{u.netloc}"

        api_error: str | None = None
        rss_error: str | None = None

        # --- Prefer the API for repo activity ---
        # Private RSS routes are web routes. Many instances ignore PAT
        # Authorization headers there and redirect to /user/login, while the REST
        # API accepts the same PAT cleanly. Try the API first whenever we can.
        if base_url and owner and repo:
            try:
                all_items = self._fetch_api_activities(
                    base_url=base_url,
                    owner=owner,
                    repo=repo,
                )
                return self._store_api_activity_items(
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
                api_error = collection_error(e)
        else:
            api_error = (
                "Could not infer base_url/owner/repo for API ingest. Provide "
                f"KEEN_{self.name.upper()}_BASE_URL or use a standard "
                "/owner/repo.rss URL."
            )

        # --- Fallback to RSS when the API is unavailable/unsupported ---
        try:
            xml = self._fetch_rss(feed_url)
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
                    f"{self.name} {host}: {title}"
                    if title
                    else f"{self.name} {host} rss item"
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
                key = f"{self.name}/feeds/{host}/{owner or 'unknown'}/{repo or 'unknown'}/{ts.date().isoformat()}/{ext_id}.json"

                res = store_event_with_artifact(
                    db,
                    timestamp=ts,
                    source=self.name,
                    system=label
                    or (f"{host}/{owner}/{repo}" if owner and repo else host),
                    actor=author,
                    action=action,
                    outcome=outcome,
                    severity=severity,
                    summary=summary,
                    raw_pointer={
                        self.name: {
                            "feed": feed_url,
                            "link": link,
                            "guid": guid,
                            "owner": owner,
                            "repo": repo,
                            "host": host,
                        }
                    },
                    normalized_payload={"rss": norm},
                    external_id=ext_id,
                    artifact_kind=f"{self.name}_rss",
                    artifact_bytes=payload_bytes,
                    artifact_content_type="application/json",
                    artifact_key=key,
                    captured_by=f"keen:{self.name}",
                )
                if not res.get("deduped"):
                    created += 1

                if (newest_ts is None) or (ts > newest_ts):
                    newest_ts = ts

            cur.last_ts = newest_ts or cur.last_ts
            cur.updated_at = utc_now_naive()
            db.add(cur)
            db.commit()
            return {
                "feed": feed_url,
                "created_events": created,
                "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
                "mode": "rss_fallback",
                # The fallback runs only after the API attempt has failed.
                "api_error": api_error,
            }
        except Exception as e:
            db.rollback()
            rss_error = collection_error(e)

        return {
            "feed": feed_url,
            "created_events": 0,
            "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
            "mode": "failed",
            "api_error": api_error,
            "rss_error": rss_error,
            "error": f"Could not ingest {self.label} activity via API or RSS.",
        }

    def _discover_repositories(
        self, base_url: str, section: str, name: str
    ) -> list[dict]:
        """Enumerate accessible repositories owned by one account, without following URLs from responses."""
        if (
            section not in ("organizations", "users")
            or not _ACCOUNT_NAME.fullmatch(name)
            or name in (".", "..")
        ):
            raise ValueError(f"Invalid {self.label} account")
        if not is_safe_url(base_url):
            raise ValueError(
                f"Set KEEN_{self.name.upper()}_BASE_URL to a safe HTTPS instance URL"
            )
        route = "orgs" if section == "organizations" else "users"
        url = f"{base_url.rstrip('/')}/api/v1/{route}/{quote(name, safe='')}/repos"
        headers = {"Accept": "application/json", **self._auth_headers()}
        auth = self._basic_auth() if self._auth_mode() == "basic" else None
        repos, seen = [], set()
        with ingestion_client(
            timeout=30.0,
            follow_redirects=False,
            verify=True,
            headers=headers,
            auth=auth,
        ) as client:
            for page in range(1, 1001):
                response = client.get(url, params={"page": page, "limit": 50})
                error = _response_status_error(response)
                if error:
                    raise RuntimeError(error)
                rows = response.json()
                if not isinstance(rows, list):
                    raise ValueError(f"{self.label} repository response must be a list")
                if not rows:
                    return repos
                added = 0
                for repo in rows:
                    if not isinstance(repo, dict):
                        raise ValueError(f"Invalid {self.label} repository response")
                    owner = (repo.get("owner") or {}).get("login")
                    repo_name = repo.get("name")
                    if not isinstance(owner, str) or owner.lower() != name.lower():
                        raise ValueError(
                            f"{self.label} returned a repository outside the "
                            "requested owner"
                        )
                    if (
                        not isinstance(repo_name, str)
                        or not _ACCOUNT_NAME.fullmatch(repo_name)
                        or repo_name in (".", "..")
                    ):
                        raise ValueError(f"Invalid {self.label} repository name")
                    key = (owner, repo_name)
                    if key not in seen:
                        seen.add(key)
                        repos.append(repo)
                        added += 1
                if not added:
                    raise ValueError(
                        f"{self.label} repeated a repository page; discovery stopped"
                    )
                # Continue to an empty page even if the server caps page size below 50.
        raise ValueError(f"{self.label} repository discovery exceeded 1000 pages")

    def ingest_all(self, db: Session) -> list[dict[str, Any]]:
        cfg = self.load_config(self._setting("config_path"))
        out: list[dict[str, Any]] = []
        feeds = list(cfg.get("feeds", []) or [])
        base = self._setting("base_url").rstrip("/")
        for section, field in (("organizations", "org"), ("users", "user")):
            for entry in cfg.get(section, []) or []:
                name = str(entry.get(field) or "").strip()
                try:
                    repos = self._discover_repositories(base, section, name)
                    out.append(
                        {
                            "scope": section,
                            "account": name,
                            "discovered_repositories": len(repos),
                        }
                    )
                    for repo in repos:
                        owner = repo["owner"]["login"]
                        feeds.append(
                            {
                                "url": f"{base}/{owner}/{repo['name']}.rss",
                                "label": entry.get("label") or None,
                            }
                        )
                except Exception as exc:
                    out.append(
                        {
                            "scope": section,
                            "account": name,
                            "error": collection_error(exc),
                        }
                    )
        seen_feeds = set()
        for f in feeds:
            url = f.get("url") or f.get("feed")
            if not url or url in seen_feeds:
                continue
            seen_feeds.add(url)
            try:
                out.append(
                    self.ingest_repo_feed(db, feed_url=url, label=f.get("label"))
                )
            except Exception as e:
                db.rollback()
                out.append({"feed": url, "error": collection_error(e)})
        return out or [
            {
                "skipped": True,
                "reason": f"No {self.label} inputs configured. Open Sources & evidence mapping, select this source and choose Add input. Choose User, Organisation or Feed, enter the account name or repository feed URL, and save.",
            }
        ]
