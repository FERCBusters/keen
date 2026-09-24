from __future__ import annotations

"""Generic RSS/Atom feed ingester.

This ingester is intentionally generic:
  - Supports RSS 2.0 and Atom 1.0 with minimal assumptions.
  - Stores a JSON artifact per entry (suitable as an evidence blob).
  - Uses per-feed cursors (last_ts + conditional GET headers) and global de-dupe.

See config/rss.yml for an example configuration.
"""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx
from dateutil import parser as dtparser
from sqlalchemy.orm import Session

from app.core.managed_configuration import load_document
from app.core.config import settings
from app.db.models import IngestionCursor
from app.ingest.common import store_event_with_artifact, is_safe_url
from app.security.redaction import redact_url


def load_rss_config(path: str) -> dict[str, Any]:
    return load_document("rss", path)


def _to_utc_naive(dt: datetime) -> datetime:
    """Normalize to UTC naive (DB stores TIMESTAMP WITHOUT TIME ZONE)."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    try:
        dt = dtparser.parse(v)
        # If the parsed datetime is naive, assume UTC.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return _to_utc_naive(dt)
    except Exception:
        return None


def _local(tag: str) -> str:
    # "{ns}name" -> "name"
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _child_text(parent: ET.Element, name: str) -> str:
    for ch in list(parent):
        if _local(ch.tag) == name:
            return (ch.text or "").strip()
    return ""


def _first_link_rss(item: ET.Element) -> str:
    link = _child_text(item, "link")
    return link.strip()


def _first_link_atom(entry: ET.Element) -> str:
    best = ""
    for ch in list(entry):
        if _local(ch.tag) != "link":
            continue
        href = (ch.attrib.get("href") or "").strip()
        if not href:
            continue
        rel = (ch.attrib.get("rel") or "alternate").strip().lower()
        if rel == "alternate":
            return href
        if not best:
            best = href
    return best


def _author_atom(entry: ET.Element) -> str:
    for ch in list(entry):
        if _local(ch.tag) != "author":
            continue
        name = _child_text(ch, "name")
        if name:
            return name
    return ""


def _author_rss(item: ET.Element) -> str:
    # RSS often uses <author>email (Name)</author> or <dc:creator>
    a = _child_text(item, "author")
    if a:
        return a
    # Try any <creator> regardless of namespace
    for ch in list(item):
        if _local(ch.tag) == "creator":
            return (ch.text or "").strip()
    return ""


def _sanitize_title(s: str) -> str:
    s2 = " ".join((s or "").split())
    return s2


def _truncate(s: str, limit: int = 180) -> str:
    s2 = _sanitize_title(s)
    if len(s2) <= limit:
        return s2
    return s2[: max(0, limit - 1)] + "…"


def _feed_cursor_name(url: str, name_hint: str | None = None) -> str:
    # Keep <= 128 chars and stable.
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    hint = (name_hint or "").strip().lower()
    hint = re.sub(r"[^a-z0-9._-]+", "-", hint)[:40].strip("-")
    if hint:
        return f"rss:{hint}:{h}"
    return f"rss:{h}"


def _external_id(url: str, entry_id: str) -> str:
    # Stable per feed+entry.
    payload = f"{url}|{entry_id}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:32]


def _parse_feed(xml_bytes: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (feed_meta, entries)."""
    root = ET.fromstring(xml_bytes)
    root_name = _local(root.tag).lower()

    # RSS 2.0
    if root_name == "rss":
        channel = None
        for ch in list(root):
            if _local(ch.tag).lower() == "channel":
                channel = ch
                break
        if channel is None:
            return ({"type": "rss"}, [])

        feed_title = _child_text(channel, "title")
        feed_link = _child_text(channel, "link")
        feed_desc = _child_text(channel, "description")
        meta = {
            "type": "rss",
            "title": feed_title,
            "link": feed_link,
            "description": feed_desc,
        }

        entries: list[dict[str, Any]] = []
        for item in list(channel):
            if _local(item.tag).lower() != "item":
                continue
            title = _child_text(item, "title")
            link = _first_link_rss(item)
            guid = _child_text(item, "guid")
            pub = _child_text(item, "pubDate")
            updated = _child_text(item, "updated")
            desc = _child_text(item, "description")
            author = _author_rss(item)

            entry_id = (guid or link or title or "").strip()
            if not entry_id:
                continue

            ts = _parse_dt(pub) or _parse_dt(updated)
            entries.append(
                {
                    "id": entry_id,
                    "title": title,
                    "link": link,
                    "author": author,
                    "published": pub,
                    "updated": updated,
                    "timestamp": ts.isoformat() if ts else None,
                    "summary": desc,
                }
            )

        return meta, entries

    # Atom 1.0
    if root_name == "feed":
        meta = {
            "type": "atom",
            "title": _child_text(root, "title"),
            "link": "",
            "description": _child_text(root, "subtitle"),
        }
        # feed link
        for ch in list(root):
            if _local(ch.tag) != "link":
                continue
            href = (ch.attrib.get("href") or "").strip()
            rel = (ch.attrib.get("rel") or "alternate").strip().lower()
            if href and rel == "alternate":
                meta["link"] = href
                break
            if href and not meta["link"]:
                meta["link"] = href

        entries: list[dict[str, Any]] = []
        for entry in list(root):
            if _local(entry.tag).lower() != "entry":
                continue
            title = _child_text(entry, "title")
            link = _first_link_atom(entry)
            eid = _child_text(entry, "id")
            published = _child_text(entry, "published")
            updated = _child_text(entry, "updated")
            summary = _child_text(entry, "summary") or _child_text(entry, "content")
            author = _author_atom(entry)

            entry_id = (eid or link or title or "").strip()
            if not entry_id:
                continue

            ts = _parse_dt(published) or _parse_dt(updated)
            entries.append(
                {
                    "id": entry_id,
                    "title": title,
                    "link": link,
                    "author": author,
                    "published": published,
                    "updated": updated,
                    "timestamp": ts.isoformat() if ts else None,
                    "summary": summary,
                }
            )

        return meta, entries

    # Unknown
    return ({"type": root_name or "unknown"}, [])


def _safe_label_from_url(url: str) -> str:
    try:
        u = urlparse(url)
        host = (u.hostname or "rss").lower()
        path = (u.path or "").strip("/")
        bits = [host] + ([path.split("/")[0]] if path else [])
        s = "-".join([b for b in bits if b])
        s = re.sub(r"[^a-z0-9._-]+", "-", s)
        return (s or "rss")[:60]
    except Exception:
        return "rss"


def ingest_rss_feed(db: Session, feed_cfg: dict[str, Any]) -> dict[str, Any]:
    url = (feed_cfg.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "missing url"}

    if not is_safe_url(url):
        return {"ok": False, "error": "invalid or unsafe feed URL"}

    label = (feed_cfg.get("label") or feed_cfg.get("name") or "").strip()
    system = (feed_cfg.get("system") or label or "").strip() or _safe_label_from_url(
        url
    )
    max_items = int(feed_cfg.get("max_items") or 50)
    overlap_minutes = int(
        feed_cfg.get("overlap_minutes") or 60 * 24 * 2
    )  # 2 days default

    cursor_name = _feed_cursor_name(url, name_hint=label or system)
    cur = (
        db.query(IngestionCursor)
        .filter(IngestionCursor.name == cursor_name)
        .one_or_none()
    )
    if cur is None:
        cur = IngestionCursor(
            name=cursor_name,
            last_ts=None,
            meta={"url": redact_url(url), "label": label, "system": system},
        )
        db.add(cur)
        db.flush()

    headers: dict[str, str] = {}
    headers["User-Agent"] = settings.rss_user_agent
    for k, v in (feed_cfg.get("headers") or {}).items():
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
            headers[k.strip()] = v.strip()

    etag = (cur.meta or {}).get("etag")
    last_mod = (cur.meta or {}).get("last_modified")
    if isinstance(etag, str) and etag:
        headers["If-None-Match"] = etag
    if isinstance(last_mod, str) and last_mod:
        headers["If-Modified-Since"] = last_mod

    auth: tuple[str, str] | None = None
    auth_cfg = feed_cfg.get("auth")
    if isinstance(auth_cfg, dict):
        u = (auth_cfg.get("username") or "").strip()
        p = (auth_cfg.get("password") or "").strip()
        if u and p:
            auth = (u, p)

    verify_tls = True

    # Fetch without following redirects - redirects are not allowed
    with httpx.Client(
        timeout=30.0,
        follow_redirects=False,
        verify=verify_tls,
        headers=headers,
        auth=auth,
    ) as c:
        response = c.get(url)

    if response.status_code == 304:
        return {
            "ok": True,
            "url": redact_url(url),
            "created_events": 0,
            "not_modified": True,
        }
    response.raise_for_status()
    xml_bytes = response.content

    # Update conditional cache headers for next run.
    meta = dict(cur.meta or {})
    if response.headers.get("etag"):
        meta["etag"] = response.headers.get("etag")
    if response.headers.get("last-modified"):
        meta["last_modified"] = response.headers.get("last-modified")
        cur.meta = meta

    feed_meta, entries = _parse_feed(xml_bytes)

    # Process oldest-first for cursor stability
    def _entry_ts(e: dict[str, Any]) -> datetime:
        dt = _parse_dt(e.get("timestamp") or e.get("published") or e.get("updated"))
        return dt or _to_utc_naive(datetime.now(timezone.utc))

    entries_sorted = sorted(entries, key=_entry_ts)
    if max_items and len(entries_sorted) > max_items:
        entries_sorted = entries_sorted[-max_items:]

    cursor_last = cur.last_ts
    if cursor_last and cursor_last.tzinfo is not None:
        cursor_last = cursor_last.replace(tzinfo=None)

    # Overlap window to avoid missing slightly-out-of-order entries
    floor_ts: datetime | None = None
    if cursor_last:
        floor_ts = cursor_last - timedelta(minutes=overlap_minutes)

    created = 0
    newest_ts: datetime | None = cursor_last

    for e in entries_sorted:
        entry_id = (e.get("id") or "").strip()
        if not entry_id:
            continue

        ts = _entry_ts(e)
        if floor_ts and ts < floor_ts:
            continue

        title = (e.get("title") or "").strip()
        if not title:
            # Fallback: derive a compact title from link/id
            title = (e.get("link") or entry_id).strip()

        actor = (e.get("author") or "").strip() or None
        action = "published" if (e.get("published") or "").strip() else "updated"
        link = (e.get("link") or "").strip()

        summary = _truncate(title, 180)
        if label:
            summary = f"[{label}] {summary}"

        ext_id = _external_id(url, entry_id)

        payload = {
            "feed": {"url": redact_url(url), **feed_meta},
            "entry": {
                **{k: v for k, v in e.items() if k != "timestamp"},
                "timestamp": ts.isoformat(),
            },
            "fetched_at": _to_utc_naive(datetime.now(timezone.utc)).isoformat(),
        }
        payload_bytes = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

        key = f"rss/{_safe_label_from_url(url)}/{ts.date().isoformat()}/{ext_id}.json"

        res = store_event_with_artifact(
            db,
            timestamp=ts,
            source="rss",
            system=system,
            actor=actor,
            action=action,
            outcome="info",
            severity=3,
            summary=summary,
            raw_pointer={
                "rss": {
                    "feed_url": redact_url(url),
                    "entry_id": entry_id,
                    "entry_link": redact_url(link) if link else "",
                }
            },
            normalized_payload={"rss": payload},
            external_id=ext_id,
            artifact_kind="rss_entry",
            artifact_bytes=payload_bytes,
            artifact_content_type="application/json",
            artifact_key=key,
            captured_by="keen:rss",
        )
        if not res.get("deduped"):
            created += 1

        if newest_ts is None or ts > newest_ts:
            newest_ts = ts

    # advance cursor
    if newest_ts:
        cur.last_ts = newest_ts
    cur.updated_at = datetime.utcnow()
    db.add(cur)
    db.commit()

    return {
        "ok": True,
        "url": redact_url(url),
        "label": label,
        "system": system,
        "created_events": created,
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
        "feed_type": feed_meta.get("type"),
    }


def ingest_rss_all(db: Session) -> list[dict[str, Any]]:
    if not settings.rss_enabled:
        return [{"skipped": True, "reason": "rss_enabled=false"}]

    cfg = load_rss_config(settings.rss_config_path)
    feeds = cfg.get("feeds") or []
    out: list[dict[str, Any]] = []
    if not isinstance(feeds, list) or not feeds:
        return [{"ok": True, "created_events": 0, "note": "no feeds configured"}]

    for f in feeds:
        if not isinstance(f, dict):
            continue
        if f.get("enabled") is False:
            out.append(
                {
                    "skipped": True,
                    "reason": "feed disabled",
                    "url": redact_url(str(f.get("url") or "")),
                }
            )
            continue
        try:
            out.append(ingest_rss_feed(db, f))
        except Exception as e:
            db.rollback()
            out.append(
                {
                    "ok": False,
                    "url": redact_url(str(f.get("url") or "")),
                    "error": str(e),
                }
            )
    return out
