from __future__ import annotations

"""Redaction utilities.

These helpers aim to keep secrets (e.g. webhook secrets, API keys, feed tokens)
from being persisted in Postgres or object storage, while still keeping
URLs/fields useful for the UI.

We redact:
  - sensitive query parameters in URLs (token=..., api_key=..., etc)
  - common key=value / key: value patterns embedded in free text
  - JSON structures recursively (dict/list)
  - text-like artifacts (json/xml/rss/plaintext) before writing to S3

Configuration (environment variables):
  - KEEN_SENSITIVE_KEYS:
      Comma/space-separated list of additional keys to treat as sensitive.
      Example: "x-some-secret, stripe-signature, x-api-key"
  - KEEN_SENSITIVE_KEYS_MODE:
      "extend" (default) to add to defaults, or "replace" to use ONLY
      KEEN_SENSITIVE_KEYS.
"""

import ipaddress
import json
import os
import re
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "REDACTED"

MASKED_EMAIL = "[MASKED_EMAIL]"
MASKED_IP = "[MASKED_IP]"

_EMAIL_RE = re.compile(
    r"(?<![A-Z0-9._%+-])"
    r"([A-Z0-9._%+-]{1,128})@([A-Z0-9.-]+\.[A-Z]{2,63})"
    r"(?![A-Z0-9._%+-])",
    re.IGNORECASE,
)
_IPV4_RE = re.compile(
    r"(?<![\w.])(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}(?![\w.])"
)
# Conservative IPv6 candidate matcher; each candidate is validated with ipaddress
# before replacement to avoid masking timestamps, ratios, or ordinary prose.
_IPV6_CANDIDATE_RE = re.compile(
    r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f:.%]+(?![\w:])"
)

# NOTE: Defaults are intentionally broad; users can tighten/override via env vars.
DEFAULT_SENSITIVE_KEYS: set[str] = {
    "token",
    "access_token",
    "api_key",
    "apikey",
    "key",
    "member_key",
    "secret",
    "client_secret",
    "signature",
    "auth",
    "authorization",
    "password",
    "cookie",
}

_URL_RE = re.compile(r'https?://[^\s"\'<>]+', re.IGNORECASE)


def _normalize_key(k: str) -> str:
    return str(k).strip().lower().replace("-", "_")


def _parse_env_list(raw: str) -> set[str]:
    if not raw:
        return set()
    raw = raw.replace(",", " ")
    return {_normalize_key(p) for p in raw.split() if p.strip()}


def _key_to_regex_fragment(k: str) -> str:
    # allow - and _ interchangeably: api_key matches api-key too
    return re.escape(k).replace("\\_", "[-_]")


@lru_cache(maxsize=1)
def _sensitive_cfg() -> tuple[set[str], tuple[str, ...], re.Pattern, re.Pattern]:
    """Build sensitive-key configuration."""
    mode = (os.getenv("KEEN_SENSITIVE_KEYS_MODE") or "extend").strip().lower()
    env_keys = _parse_env_list(os.getenv("KEEN_SENSITIVE_KEYS") or "")
    base = {_normalize_key(k) for k in DEFAULT_SENSITIVE_KEYS}

    if mode in {"replace", "override", "only"}:
        keys = env_keys
    else:
        keys = base | env_keys

    substring_needles = tuple(
        sorted({k for k in keys if len(k) >= 5}, key=len, reverse=True)
    )

    alts = (
        "|".join(
            sorted({_key_to_regex_fragment(k) for k in keys}, key=len, reverse=True)
        )
        or r"(?!x)x"
    )

    line_keys = [k for k in ("authorization", "cookie", "set_cookie") if k in keys]
    if line_keys:
        alts_line = "|".join(
            sorted(
                {_key_to_regex_fragment(k) for k in line_keys}, key=len, reverse=True
            )
        )
        line_re = re.compile(rf"(?i)\b({alts_line})\b\s*[:=]\s*([^\r\n]+)")
    else:
        line_re = re.compile(r"(?!x)x")

    kv_re = re.compile(rf"(?i)\b({alts})\b\s*[:=]\s*([^&\s]+)")
    return keys, substring_needles, line_re, kv_re


def is_sensitive_key(k: str) -> bool:
    nk = _normalize_key(k)
    exact_keys, needles, _, _ = _sensitive_cfg()
    if nk in exact_keys:
        return True
    return any(n in nk for n in needles)


def redact_query_string(qs: str | None) -> str | None:
    if not qs:
        return None
    try:
        parts: list[tuple[str, str]] = []
        changed = False
        for k, v in parse_qsl(qs, keep_blank_values=True):
            if isinstance(k, str) and is_sensitive_key(k) and v:
                parts.append((k, REDACTED))
                changed = True
            else:
                parts.append((k, v))
        out = urlencode(parts, doseq=True)
        return out if (changed or out != qs) else qs
    except Exception:
        return qs


def redact_url(url: str) -> str:
    """Redact sensitive query param values in a URL."""
    try:
        parts = urlsplit(url)
    except Exception:
        return url

    if not parts.query:
        return url

    changed = False
    q = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if isinstance(k, str) and is_sensitive_key(k) and v:
            q.append((k, REDACTED))
            changed = True
        else:
            q.append((k, v))
    if not changed:
        return url

    new_query = urlencode(q, doseq=True)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
    )


def redact_str(s: str | None) -> str | None:
    if s is None:
        return None
    txt = str(s)

    # Free-text key=value redaction
    _, _, line_re, kv_re = _sensitive_cfg()

    txt = line_re.sub(lambda m: f"{m.group(1)}={REDACTED}", txt)
    txt = kv_re.sub(lambda m: f"{m.group(1)}={REDACTED}", txt)

    # URL redaction (handles cases where the query string contains secrets)
    def _url_sub(m: re.Match) -> str:
        return redact_url(m.group(0))

    txt = _URL_RE.sub(_url_sub, txt)
    return txt


def redact_obj(obj: Any) -> Any:
    """Recursively redact secrets in dict/list/string objects."""
    if obj is None:
        return None
    if isinstance(obj, str):
        return redact_str(obj)
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(redact_obj(x) for x in obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and is_sensitive_key(k):
                out[k] = REDACTED
            else:
                out[k] = redact_obj(v)
        return out
    return obj


def mask_event_data_str(s: str | None) -> str | None:
    """Mask IP-address and email-address substrings in event data."""
    if s is None:
        return None
    txt = str(s)
    txt = _EMAIL_RE.sub(MASKED_EMAIL, txt)

    def _ipv6_sub(m: re.Match) -> str:
        raw = m.group(0)
        # Strip a zone id for validation only (e.g. fe80::1%eth0).
        val = raw.split("%", 1)[0]
        # Do not even try to treat short colon-delimited strings as IPv6.
        # Times such as 13:40:19 contain two colons but are not addresses.
        if "::" not in val and val.count(":") < 3:
            return raw
        try:
            parsed = ipaddress.ip_address(val)
        except ValueError:
            return raw
        if parsed.version != 6:
            return raw
        return MASKED_IP

    txt = _IPV6_CANDIDATE_RE.sub(_ipv6_sub, txt)
    txt = _IPV4_RE.sub(MASKED_IP, txt)
    return txt


def mask_event_data_obj(obj: Any) -> Any:
    """Recursively mask IP/email substrings in strings contained in an object."""
    if obj is None:
        return None
    if isinstance(obj, str):
        return mask_event_data_str(obj)
    if isinstance(obj, list):
        return [mask_event_data_obj(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(mask_event_data_obj(x) for x in obj)
    if isinstance(obj, dict):
        return {k: mask_event_data_obj(v) for k, v in obj.items()}
    return obj


def is_textual_content_type(content_type: str | None) -> bool:
    """Return true when byte-level text redaction/masking can be attempted."""
    return _is_textual_content_type(content_type)


def mask_event_data_bytes(data: bytes, content_type: str | None) -> tuple[bytes, str]:
    """Mask IP/email substrings in text-like artifacts.

    Returns: (possibly_modified_bytes, status)
      status in {"clean", "redacted", "skipped"}
    """
    if not data:
        return data, "clean"
    if not _is_textual_content_type(content_type):
        return data, "skipped"

    ct = (content_type or "").split(";", 1)[0].strip().lower()
    try:
        txt = data.decode("utf-8")
    except Exception:
        txt = data.decode("utf-8", errors="replace")

    if ct == "application/json" or ct.endswith("+json"):
        try:
            obj = json.loads(txt)
            obj2 = mask_event_data_obj(obj)
            if obj2 == obj:
                return data, "clean"
            new_txt = json.dumps(obj2, ensure_ascii=False, sort_keys=True)
        except Exception:
            new_txt = mask_event_data_str(txt) or txt
    else:
        new_txt = mask_event_data_str(txt) or txt

    if new_txt == txt:
        return data, "clean"
    return new_txt.encode("utf-8"), "redacted"


def combine_redaction_status(*statuses: str | None) -> str:
    vals = {(s or "").strip().lower() for s in statuses if s is not None}
    if "redacted" in vals:
        return "redacted"
    if vals and vals <= {"clean"}:
        return "clean"
    if vals and vals <= {"skipped"}:
        return "skipped"
    if vals:
        # Mixed clean/skipped still means no modification happened.
        return "clean"
    return "unknown"


def _is_textual_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    ct = (content_type.split(";")[0] or "").strip().lower()
    if ct.startswith("text/"):
        return True
    if ct.endswith("+json") or ct.endswith("+xml"):
        return True
    return ct in {
        "application/json",
        "application/xml",
        "application/rss+xml",
        "application/atom+xml",
        "application/xhtml+xml",
        "application/x-yaml",
        "application/yaml",
    }


def redact_bytes(data: bytes, content_type: str | None) -> tuple[bytes, str]:
    """Redact secrets in text-like artifacts.

    Returns: (possibly_modified_bytes, status)
      status in {"clean", "redacted", "skipped"}
    """
    if not data:
        return data, "clean"
    if not _is_textual_content_type(content_type):
        return data, "skipped"

    ct = (content_type or "").split(";")[0].strip().lower()
    try:
        txt = data.decode("utf-8")
    except Exception:
        txt = data.decode("utf-8", errors="replace")

    new_txt = txt
    if ct == "application/json" or ct.endswith("+json"):
        try:
            obj = json.loads(txt)
            obj2 = redact_obj(obj)
            new_txt = json.dumps(obj2, ensure_ascii=False, sort_keys=True)
        except Exception:
            # fall back to best-effort text redaction
            new_txt = redact_str(txt) or txt
    else:
        new_txt = redact_str(txt) or txt

    if new_txt == txt:
        return data, "clean"
    return new_txt.encode("utf-8"), "redacted"
