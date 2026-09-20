from __future__ import annotations

import json
import mimetypes
import os
import re
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from zoneinfo import ZoneInfo

import yaml
from fastapi import HTTPException

from app.core.config import settings
from app.security.redaction import redact_url

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

CONTROL_JUSTIFICATIONS: tuple[str, ...] = (
    "Best Practice",
    "Result of Risk Assessment",
    "Applicable but not Implemented",
    "Limited Applicability",
)
CONTROL_JUSTIFICATION_IDS: dict[str, str] = {
    "best_practice": "Best Practice",
    "risk_assessment": "Result of Risk Assessment",
    "result_of_risk_assessment": "Result of Risk Assessment",
    "applicable_not_implemented": "Applicable but not Implemented",
    "applicable_but_not_implemented": "Applicable but not Implemented",
    "limited_applicability": "Limited Applicability",
}


def control_justification(control: Any) -> str | None:
    """Return a validated display label for a control justification, if present."""
    meta = {}
    try:
        meta = getattr(control, "meta", None) or {}
    except Exception:
        meta = {}
    if isinstance(control, dict):
        meta = control.get("metadata") or control.get("meta") or meta
    if not isinstance(meta, dict):
        return None

    raw = (
        meta.get("justification")
        or meta.get("soa_justification")
        or meta.get("control_justification")
        or meta.get("applicability_justification")
    )
    value = str(raw or "").strip()
    if not value:
        return None
    for label in CONTROL_JUSTIFICATIONS:
        if value.lower() == label.lower():
            return label
    mapped = CONTROL_JUSTIFICATION_IDS.get(value.lower().replace(" ", "_"))
    return mapped


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
    raise HTTPException(
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
        raise HTTPException(
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
        raise HTTPException(status_code=400, detail="Invalid theme")
    return theme


def normalize_saved_search_url(url: str | None) -> str:
    """Normalize and validate a saved-search URL.

    Only allow *relative* paths within this UI (e.g. /events.html?q=ssh).
    Drop pagination params like 'offset' so saved searches reopen at the top.
    """

    raw = str(url or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="url is required")

    if re.match(r"^(?:javascript|data):", raw, flags=re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Invalid saved search URL")

    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        raise HTTPException(
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
        raise HTTPException(status_code=400, detail="Saved search URL is too long")
    return out


_REF_NUMERIC_RE = re.compile(r"^\d+$")
_REF_ALPHA_RE = re.compile(r"^[A-Za-z]+$")
_REF_ALNUM_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


def _ref_token_key(token: str) -> tuple:
    t = (token or "").strip()
    if not t:
        return (9, "")

    if _REF_NUMERIC_RE.match(t):
        return (0, int(t))

    m = _REF_ALNUM_RE.match(t)
    if m:
        return (1, m.group(1).lower(), int(m.group(2)))

    if _REF_ALPHA_RE.match(t):
        # Sort single-letter suffixes naturally (e.g. a, b, c)
        return (2, t.lower())

    return (3, t.lower())


def ref_sort_key(ref: str | None) -> tuple:
    """Natural sort for control refs across multiple frameworks.

    Examples handled:
      - ISO Annex A refs: A.8.1, A.8.10, A.8.34
      - Numeric clauses: 4.1, 8.2.3
      - DVSTF-like refs: 11.8.1.a, 11.11.4.c
      - Mixed tokens with alpha suffixes
    """

    s = (ref or "").strip()
    if not s:
        return (9, "")

    parts = [p for p in s.split(".") if p != ""]
    if not parts:
        return (9, s.lower())

    first = parts[0]
    if _REF_ALPHA_RE.match(first):
        bucket = 0  # e.g. A.x (Annex style)
    elif _REF_NUMERIC_RE.match(first):
        bucket = 1  # clause/section style
    else:
        bucket = 2  # fallback

    # Keep token sort keys nested rather than flattening them into one tuple.
    # Flattening creates prefix-comparison cases such as ``5`` vs ``5.1`` where
    # Python eventually compares the final string fallback from the shorter key
    # with the next integer token from the longer key, raising:
    # ``TypeError: '<' not supported between instances of 'int' and 'str'``.
    # A nested tuple preserves natural ordering without ever comparing unlike
    # token component types at the same tuple position.
    token_keys = tuple(_ref_token_key(p) for p in parts)
    return (bucket, token_keys, s.lower())


def try_uuid(v: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(v))
    except Exception:
        return None


def _validate_event_source_url(url: str) -> Optional[str]:
    """Return a safe clickable upstream/source URL, or ``None``.

    Evidence payloads often contain fields named ``url`` that are not web links
    to an upstream system.  OSSEC/Wazuh alerts, for example, may put Unix paths
    such as ``/home/miguel`` in ``json.url``.  Rendering those values as links
    makes the browser resolve them against the KEEN origin, producing misleading
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

    Historically most providers put clickable upstream links in ``raw_pointer``.
    RSS events can also have the original entry link in
    ``normalized_payload["rss"]["entry"]["link"]``.  Accept both so older RSS
    rows can show links in list/table views without being re-ingested.

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
        raise HTTPException(status_code=400, detail="Link URL is blank")
    if len(u) > 2048:
        raise HTTPException(status_code=400, detail="Link URL is too long")

    parts = urlsplit(u)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https", "mailto"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported link scheme: {scheme or 'none'}",
        )

    if scheme in ("http", "https") and not parts.netloc:
        raise HTTPException(status_code=400, detail="Link URL must include a hostname")
    if scheme == "mailto" and not parts.path:
        raise HTTPException(
            status_code=400, detail="mailto: URL must include an address"
        )

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


@lru_cache(maxsize=8)
def load_control_links(path: str) -> dict[str, dict[str, str]]:
    """Load control->URL mapping from YAML.

    Supports both legacy single-framework files and multi-framework files.

    Supported layouts:

      Legacy / default-only:
        A.5.1: https://...
        A.8.1:
          url: https://...

      Explicit default bucket:
        default:
          A.5.1: https://...

      Framework buckets:
        frameworks:
          ISO27001:2022:
            A.5.1: https://...
          UK-DVSTF:1.0:
            12.5.a: https://...

      Short framework-map form:
        ISO27001:2022:
          A.5.1: https://...
        UK-DVSTF:1.0:
          12.5.a: https://...
    """

    DEFAULT_BUCKET = "__default__"

    def _extract_url_from_str(s: str) -> str | None:
        s = (s or "").strip()
        if not s:
            return None
        for needle in ("https://", "http://"):
            i = s.find(needle)
            if i >= 0:
                return s[i:].strip()
        if s.startswith("www."):
            return "https://" + s
        return None

    def _extract_url(v: Any) -> str | None:
        if isinstance(v, str):
            return _extract_url_from_str(v) or (
                v.strip() if v.strip().startswith(("http://", "https://")) else None
            )

        if isinstance(v, dict):
            for k in ("url", "href", "link", "upstream_url", "upstreamUrl"):
                u = v.get(k)
                if isinstance(u, str):
                    out = _extract_url_from_str(u) or (
                        u.strip()
                        if u.strip().startswith(("http://", "https://"))
                        else None
                    )
                    if out:
                        return out

            if len(v) == 1:
                only_val = next(iter(v.values()))
                out = _extract_url(only_val)
                if out:
                    return out

            for vv in v.values():
                out = _extract_url(vv)
                if out:
                    return out

        if isinstance(v, list):
            for item in v:
                out = _extract_url(item)
                if out:
                    return out

        return None

    def _parse_flat_mapping(node: Any) -> dict[str, str]:
        out: dict[str, str] = {}
        if isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str) or not k.strip():
                    continue
                url = _extract_url(v)
                if url:
                    out[k.strip()] = url
        elif isinstance(node, list):
            for item in node:
                if not isinstance(item, dict):
                    continue
                ref = item.get("ref") or item.get("control") or item.get("id")
                if not isinstance(ref, str) or not ref.strip():
                    continue
                url = _extract_url(item)
                if url:
                    out[ref.strip()] = url
        return out

    def _fallback_parse_lines(raw: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in (raw or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            k, rest = line.split(":", 1)
            k = k.strip()
            if not k:
                continue
            url = _extract_url_from_str(rest)
            if url:
                out[k] = url
        return out

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return {DEFAULT_BUCKET: {}}
    except Exception:
        return {DEFAULT_BUCKET: {}}

    try:
        data = yaml.safe_load(raw)
    except Exception:
        return {DEFAULT_BUCKET: _fallback_parse_lines(raw)}

    if data is None:
        return {DEFAULT_BUCKET: {}}

    out: dict[str, dict[str, str]] = {DEFAULT_BUCKET: {}}

    # Backward-compatible alias
    if (
        isinstance(data, dict)
        and "controls" in data
        and not isinstance(data.get("controls"), str)
    ):
        data = data.get("controls")

    if isinstance(data, list):
        out[DEFAULT_BUCKET] = _parse_flat_mapping(data)
        return out

    if not isinstance(data, dict):
        return out

    reserved_keys = {"frameworks", "default", "controls"}

    # Explicit default bucket
    if "default" in data:
        out[DEFAULT_BUCKET].update(_parse_flat_mapping(data.get("default")))

    # Explicit framework buckets
    fw_node = data.get("frameworks")
    if isinstance(fw_node, dict):
        for fw, bucket in fw_node.items():
            fw_slug = str(fw or "").strip()
            if not fw_slug:
                continue
            parsed = _parse_flat_mapping(bucket)
            if parsed:
                out[fw_slug] = parsed

    # Legacy top-level default map + shorthand framework buckets.
    for k, v in data.items():
        if k in reserved_keys:
            continue

        key = str(k or "").strip()
        if not key:
            continue

        url = _extract_url(v)
        if url:
            out[DEFAULT_BUCKET][key] = url
            continue

        parsed = _parse_flat_mapping(v)
        if parsed:
            out[key] = parsed

    return out


def control_upstream_url(control: Any) -> str | None:
    """Compute the upstream URL for a control (metadata overrides YAML)."""
    try:
        meta = getattr(control, "meta", None) or {}
        if isinstance(meta, dict):
            u = meta.get("upstream_url") or meta.get("upstreamUrl")
            if isinstance(u, str) and u.strip():
                return u.strip()
    except Exception:
        pass

    ref = getattr(control, "ref", None)
    framework = getattr(control, "framework_slug", None) or getattr(
        control, "framework", None
    )

    if isinstance(control, dict):
        ref = control.get("ref", ref)
        framework = control.get("framework_slug", framework) or control.get(
            "framework", framework
        )

    if not isinstance(ref, str) or not ref.strip():
        return None
    ref = ref.strip()

    m = load_control_links(settings.control_links_path)

    if isinstance(framework, str) and framework.strip():
        fw = framework.strip()
        fw_links = m.get(fw) or {}
        if ref in fw_links:
            return fw_links[ref]

    # Legacy/default fallback
    default_links = m.get("__default__", {})
    if ref in default_links:
        return default_links[ref]

    # If the caller supplied a framework, do not fall back to another
    # framework bucket just because the ref text happens to match. Clause
    # refs such as "5.1" are common across frameworks, so cross-framework
    # fallback can leak e.g. DVSTF URLs into ISO27001 clause displays.
    if isinstance(framework, str) and framework.strip():
        return None

    # Legacy fallback for callers that genuinely have no framework context.
    for fw, refs in m.items():
        if fw == "__default__":
            continue
        if ref in refs:
            return refs[ref]

    return None


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def safe_download_ext(ext: str) -> str:
    """Return a safe lowercase file extension (including leading dot) or empty."""
    if not ext:
        return ""
    if not ext.startswith("."):
        ext = "." + ext
    ext = ext.strip().lower()
    if re.fullmatch(r"\.[a-z0-9]{1,10}", ext):
        return ext
    return ""


def guess_extension(content_type: str | None, storage_key: str | None) -> str:
    """Best-effort extension inference from storage key or MIME type."""
    if storage_key:
        ext = safe_download_ext(os.path.splitext(storage_key)[1])
        if ext:
            return ext

    ctype = (content_type or "").split(";")[0].strip().lower()
    if not ctype:
        return ""

    if ctype in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if ctype == "image/svg+xml":
        return ".svg"

    ext = mimetypes.guess_extension(ctype) or ""
    return safe_download_ext(ext)


def download_filename_for_artifact(artifact: Any, storage_key: str | None) -> str:
    ext = guess_extension(getattr(artifact, "content_type", None), storage_key)
    return f"artifact-{getattr(artifact, 'id')}{ext}"


def content_disposition_attachment(filename: str) -> str:
    """RFC 5987-friendly Content-Disposition for downloads."""
    safe = filename.replace('"', "").replace("\\", "_")
    return f"attachment; filename=\"{safe}\"; filename*=UTF-8''{quote(safe)}"
