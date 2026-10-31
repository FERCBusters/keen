"""Helpers shared by the raw log collectors, such as Loki and CloudWatch Logs."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Return a timezone-aware UTC datetime.

    Some deployments store cursor timestamps as TIMESTAMP WITHOUT TIME ZONE.
    SQLAlchemy then returns offset-naive datetimes, which cannot be compared to
    timezone-aware values (e.g. datetime.now(timezone.utc)).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def make_fingerprint(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8", errors="replace"))
        h.update(b"|")
    return h.hexdigest()[:32]


def try_parse_json_line(line: str) -> Any | None:
    s = (line or "").strip()
    if not s:
        return None
    if s[0] not in "{[":
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def truncate(s: str, limit: int = 220) -> str:
    s2 = " ".join((s or "").split())
    if len(s2) <= limit:
        return s2
    return s2[: limit - 1] + "…"


def brief_http_request(log_line: str) -> str:
    """Extract a compact HTTP request from common access-log formats.

    Example:
      "... \"GET /path?query=... HTTP/1.1\" ..." -> "GET /path"
    """
    s = str(log_line or "")
    m = re.search(r'"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+([^\s\"]+)', s)
    if not m:
        return ""
    return f"{m.group(1)} {truncate(m.group(2).split('?', 1)[0], 120)}"
