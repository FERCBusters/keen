from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo

import yaml

from app.core.config import settings
from app.core.references import CONTROL_JUSTIFICATION_IDS, CONTROL_JUSTIFICATIONS
from app.security.redaction import redact_url
from app.services.errors import DomainError

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_RSS_LABEL_CACHE: dict[str, Any] = {"mtime": None, "labels": {}}


def rss_labels_by_feed_url() -> dict[str, str]:
    """Map redacted feed URL -> configured label.

    Events store RSS feed URLs in raw_pointer after redaction. To show a
    human-friendly identifier in the UI, we look up the configured label.
    """

    path = (settings.rss_config_path or "").strip()
    if not path:
        return {}

    try:
        st = os.stat(path)
        mtime = int(st.st_mtime)
    except Exception:
        return {}

    if _RSS_LABEL_CACHE.get("mtime") == mtime and isinstance(
        _RSS_LABEL_CACHE.get("labels"), dict
    ):
        return _RSS_LABEL_CACHE["labels"]

    labels: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        feeds = cfg.get("feeds") or []
        if isinstance(feeds, list):
            for feed in feeds:
                if not isinstance(feed, dict):
                    continue
                url = str(feed.get("url") or "").strip()
                if not url:
                    continue
                label = str(feed.get("label") or feed.get("name") or "").strip()
                if not label:
                    continue
                labels[redact_url(url)] = label
    except Exception:
        labels = {}

    _RSS_LABEL_CACHE["mtime"] = mtime
    _RSS_LABEL_CACHE["labels"] = labels
    return labels


def event_identifier(event: Any) -> str | None:
    """Best-effort identifier for the Events list column."""

    def _get_dotted(obj: Any, path: str) -> Any:
        cur: Any = obj
        for part in (path or "").split("."):
            if not part:
                continue
            if isinstance(cur, dict) and part in cur:
                cur = cur.get(part)
            else:
                return None
        return cur

    src = (getattr(event, "source", "") or "").lower()

    if src == "rss":
        feed_url = None
        try:
            rss_ptr = (getattr(event, "raw_pointer", None) or {}).get("rss") or {}
            feed_url = rss_ptr.get("feed_url")
        except Exception:
            feed_url = None

        if isinstance(feed_url, str) and feed_url.strip():
            lbl = rss_labels_by_feed_url().get(feed_url.strip())
            if lbl:
                return lbl
        return getattr(event, "system", None) or None

    if src == "loki":
        if getattr(event, "system", None):
            return getattr(event, "system", None)
        try:
            loki_ptr = (getattr(event, "raw_pointer", None) or {}).get("loki") or {}
            qn = loki_ptr.get("query_name")
            if isinstance(qn, str) and qn.strip():
                return qn.strip()
        except Exception:
            pass
        return None

    if src.startswith("webhook:enroll"):
        payload = getattr(event, "normalized_payload", None) or {}
        for path in ("new.host", "old.host", "host"):
            v = _get_dotted(payload, path)
            if isinstance(v, str) and v.strip():
                return v.strip()

        wrapped = payload.get("payload") if isinstance(payload, dict) else None
        if isinstance(wrapped, dict):
            for path in ("new.host", "old.host", "host"):
                v = _get_dotted(wrapped, path)
                if isinstance(v, str) and v.strip():
                    return v.strip()

    return getattr(event, "system", None) or None


# ---------------------------------------------------------------------------
# Statement of Applicability / controls
# ---------------------------------------------------------------------------


def validate_control_justification(raw: str | None) -> str | None:
    """Validate an editable control justification value."""
    value = str(raw or "").strip()
    if not value:
        return None
    for label in CONTROL_JUSTIFICATIONS:
        if value.lower() == label.lower():
            return label
    mapped = CONTROL_JUSTIFICATION_IDS.get(value.lower().replace(" ", "_"))
    if mapped:
        return mapped
    raise DomainError(
        status_code=400,
        detail="justification must be one of: " + ", ".join(CONTROL_JUSTIFICATIONS),
    )


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

# Keep this list in sync with the UI theme dropdown.
ALLOWED_THEMES: list[tuple[str, str]] = [
    ("purple", "Purple"),
    ("ocean", "Ocean"),
    ("forest", "Forest"),
    ("sunset", "Sunset"),
    ("rose", "Rose"),
    ("slate", "Slate"),
    ("teal", "Teal"),
]

ALLOWED_THEMES += [
    (f"{theme_id}-dark", f"{name} (dark)") for theme_id, name in ALLOWED_THEMES.copy()
]


def theme_ids() -> set[str]:
    return {t[0] for t in ALLOWED_THEMES}


def validate_timezone_name(tz: str | None) -> str | None:
    """Validate an IANA timezone name. Returns normalized string or None."""
    if tz is None:
        return None
    tz = (tz or "").strip()
    if not tz:
        return None
    try:
        ZoneInfo(tz)
    except Exception:
        raise DomainError(
            status_code=400,
            detail="Invalid timezone. Use an IANA name like 'Australia/Melbourne'.",
        )
    return tz


def validate_theme_id(theme: str | None) -> str | None:
    if theme is None:
        return None
    theme = (theme or "").strip()
    if not theme:
        return None
    if theme not in theme_ids():
        raise DomainError(status_code=400, detail="Invalid theme")
    return theme


def normalize_saved_search_url(url: str | None) -> str:
    """Normalize and validate a saved-search URL.

    Only allow *relative* paths within this UI (e.g. /events.html?q=ssh).
    Drop pagination params like 'offset' so saved searches reopen at the top.
    """

    raw = str(url or "").strip()
    if not raw:
        raise DomainError(status_code=400, detail="url is required")

    if re.match(r"^(?:javascript|data):", raw, flags=re.IGNORECASE):
        raise DomainError(status_code=400, detail="Invalid saved search URL")
    # Browsers read a backslash as "/" and drop tabs and newlines, so such a
    # path can resolve off-site when the navbar search jumps to it.
    if "\\" in raw or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise DomainError(status_code=400, detail="Invalid saved search URL")

    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        raise DomainError(
            status_code=400,
            detail="Saved search URL must be a relative path (e.g. /events.html?q=...)",
        )

    path = (parts.path or "").strip() or "/"
    if not path.startswith("/"):
        path = "/" + path

    pairs = []
    for k, v in parse_qsl(parts.query or "", keep_blank_values=False):
        if not k:
            continue
        if k == "offset":
            continue
        if v is None:
            continue
        vv = str(v)
        if vv == "":
            continue
        pairs.append((k, vv))

    query = urlencode(pairs, doseq=True)
    out = path + (("?" + query) if query else "")
    if len(out) > 2048:
        raise DomainError(status_code=400, detail="Saved search URL is too long")
    return out


def _validate_event_source_url(url: str) -> Optional[str]:
    """Return a safe clickable upstream/source URL, or ``None``.

    Evidence payloads often contain fields named ``url`` that are not web links
    to an upstream system.  OSSEC/Wazuh alerts, for example, may put Unix paths
    such as ``/home/miguel`` in ``json.url``.  Rendering those values as links
    makes the browser resolve them against the Keen origin, producing misleading
    buttons like ``https://keen.example/home/miguel``.

    Source links shown in evidence tables therefore need to be full web URLs.
    Manual diary links use ``validate_diary_link_url`` below and remain separate.
    """

    s = (url or "").strip()
    if not s or len(s) > 2048:
        return None

    # Block dangerous schemes and protocol-relative URLs.
    if re.match(r"^(?:javascript|data|vbscript):", s, flags=re.IGNORECASE):
        return None
    if re.match(r"^//", s):
        return None

    parts = urlsplit(s)
    scheme = (parts.scheme or "").lower()
    if scheme in ("http", "https") and parts.netloc:
        return s
    return None


def extract_source_url(
    raw_pointer: Any, normalized_payload: Any = None
) -> Optional[str]:
    """Best-effort extract of an event upstream/source URL.

    Most providers put clickable upstream links in ``raw_pointer``, and an RSS
    event's normalised payload also holds its entry's link, so both are read.

    Only absolute ``http://`` and ``https://`` URLs are returned.  Relative paths
    from generic payload ``url`` fields are commonly filesystem/application paths,
    not upstream event links.
    """

    def _path_value(obj: Any, path: tuple[str, ...]) -> Optional[str]:
        cur = obj
        for key in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
        if isinstance(cur, str):
            return _validate_event_source_url(cur)
        return None

    candidates = [raw_pointer, normalized_payload]

    # Prefer known per-entry URLs over more generic URLs.  This matters for RSS:
    # normalized payloads also contain the feed URL, but the table should jump to
    # the specific upstream item when the feed entry supplied a link.
    preferred_paths: tuple[tuple[str, ...], ...] = (
        ("rss", "entry_link"),
        ("rss", "entry", "link"),
        ("rss", "entry", "href"),
        ("entry", "link"),
        ("entry", "href"),
    )
    for candidate in candidates:
        for path in preferred_paths:
            ok = _path_value(candidate, path)
            if ok:
                return ok

    # Common event-pointer keys used by providers.  RSS events store the
    # original feed entry link as ``raw_pointer["rss"]["entry_link"]`` so the
    # Events table can offer the same upstream jump as Jenkins/Taiga links.
    source_url_keys = (
        "url",
        "link",
        "href",
        "source_url",
        "sourceUrl",
        "upstream_url",
        "upstreamUrl",
        "entry_link",
        "entryLink",
    )

    def _from_mapping(mapping: Any) -> Optional[str]:
        if not isinstance(mapping, dict):
            return None
        for k in source_url_keys:
            v = mapping.get(k)
            if isinstance(v, str):
                ok = _validate_event_source_url(v)
                if ok:
                    return ok
        for v in mapping.values():
            if isinstance(v, dict):
                for k in source_url_keys:
                    vv = v.get(k)
                    if isinstance(vv, str):
                        ok = _validate_event_source_url(vv)
                        if ok:
                            return ok
        return None

    for candidate in candidates:
        ok = _from_mapping(candidate)
        if ok:
            return ok
    return None


def parse_iso_dt(s: str | None) -> datetime | None:
    """Parse a best-effort ISO8601 timestamp into a timezone-aware datetime."""
    if not s:
        return None
    txt = str(s).strip()
    if not txt:
        return None
    txt = txt.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(txt)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def normalize_diary_details(details: Any) -> dict | None:
    """Coerce diary details from JSON, dict, or plain text."""
    if details is None:
        return None
    if isinstance(details, dict):
        return details
    if isinstance(details, str):
        s = details.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            return {"notes": s}
    return {"value": details}


def validate_diary_link_url(url: str) -> str:
    """Validate and normalize a diary link URL.

    We intentionally only allow safe schemes to avoid XSS via javascript: URLs.
    """

    u = (url or "").strip()
    if not u:
        raise DomainError(status_code=400, detail="Link URL is blank")
    if len(u) > 2048:
        raise DomainError(status_code=400, detail="Link URL is too long")

    parts = urlsplit(u)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https", "mailto"):
        raise DomainError(
            status_code=400,
            detail=f"Unsupported link scheme: {scheme or 'none'}",
        )

    if scheme in ("http", "https") and not parts.netloc:
        raise DomainError(status_code=400, detail="Link URL must include a hostname")
    if scheme == "mailto" and not parts.path:
        raise DomainError(status_code=400, detail="mailto: URL must include an address")

    return u


def normalize_diary_links(raw: Any) -> list[dict[str, str]]:
    """Coerce diary links into a list of {url, text} dicts."""
    if raw is None:
        return []

    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            raw = json.loads(s)
        except Exception:
            raw = [{"url": s, "text": ""}]

    items: list[Any]
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = [raw]
    else:
        items = [raw]

    out: list[dict[str, str]] = []
    for it in items:
        url = ""
        text = ""
        if isinstance(it, str):
            url = it
        elif isinstance(it, dict):
            url = str(it.get("url") or it.get("href") or "").strip()
            text = str(it.get("text") or it.get("label") or "").strip()
        else:
            continue

        if not url:
            continue

        url = validate_diary_link_url(url)
        if len(text) > 256:
            text = text[:256]

        out.append({"url": url, "text": text})
        if len(out) >= 25:
            break

    return out


# ---------------------------------------------------------------------------
# Control links
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------
