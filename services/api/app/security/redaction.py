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

from __future__ import annotations

import ipaddress
import json
import os
import re
from contextvars import ContextVar
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "REDACTED"

MASKED_EMAIL = "[MASKED_EMAIL]"
MASKED_IP = "[MASKED_IP]"

_EMAIL_RE = re.compile(
    r"(?<![A-Z0-9._%+-])"
    r"([A-Z0-9._%+-]{1,128})@([A-Z0-9.-]+\.[A-Z]{2,63})"
    # A trailing full stop ends a sentence; only ".<alnum>" continues the domain.
    r"(?![A-Z0-9_%+-]|\.[A-Z0-9])",
    re.IGNORECASE,
)
# A mailbox as address headers write it: a display name before the address in
# angle brackets. The name identifies the person as much as the address does,
# so the whole mailbox is masked. A name is up to five words of letters, which
# may carry apostrophes, dots and hyphens, or one quoted string; every
# repetition is bounded, so malformed headers cannot stall the scan.
_NAME_WORD = r"[^\W\d_]{1,40}(?:['.-][^\W\d_]{1,40}){0,3}\.?"
_MAILBOX_RE = re.compile(
    r"(?:(?<![\w\"'.-])"
    r"(?:\"[^\"\n]{1,100}\"|" + _NAME_WORD + r"(?:[ \t]+" + _NAME_WORD + r"){0,4})"
    r"[ \t]*)?"
    r"<[ \t]*" + _EMAIL_RE.pattern + r"[ \t]*>",
    re.IGNORECASE,
)
_IPV4_RE = re.compile(
    r"(?<![\w.])(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
    # Reject a fifth octet or a longer token, but allow sentence punctuation.
    r"(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}(?!\w|\.\d)"
)
# Conservative IPv6 candidate matcher; each candidate is validated with ipaddress
# before replacement to avoid masking timestamps, ratios, or ordinary prose.
# Bound both repetitions: overlapping colon runs must never trigger quadratic
# backtracking on malformed evidence. Valid IPv6 addresses fit these bounds.
_IPV6_CANDIDATE_RE = re.compile(
    r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,8}[0-9A-Fa-f:.%]{1,64}(?![\w:])"
)

# NOTE: Defaults are intentionally broad; users can tighten/override via env vars.
DEFAULT_SENSITIVE_KEYS: set[str] = {
    "token",
    "bootstrap_key",
    "enrollment_nonce",
    "nonce",
    "renewal_nonce",
    "access_token",
    "refresh_token",
    "id_token",
    "set_cookie",
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

# Measured workstation-posture facts whose names contain a sensitive-key
# substring ("password", "signature") but which hold a boolean, a count or a
# timestamp rather than a secret. A name keeps its value only while the value
# has exactly that shape; any other value is redacted as usual. Naming one of
# these keys explicitly in KEEN_SENSITIVE_KEYS redacts it unconditionally.
_MEASURED_VALUE_SHAPES = {
    "boolean": re.compile(r"true|false"),
    "count": re.compile(r"[0-9]{1,10}"),
    "timestamp": re.compile(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})?"
    ),
}
MEASURED_FIELDS: dict[str, str] = {
    "posture.password_required": "boolean",
    "posture.password_required_configured": "boolean",
    "posture.password_delay_seconds": "count",
    "posture.signatures_age_days": "count",
    "posture.signatures_updated": "timestamp",
}

# Strings that hold a whole JSON document (an agent log body, a JSON log line
# kept beside its parsed form) receive the same key-aware redaction as parsed
# payloads. Larger or deeper documents fall back to text redaction.
_EMBEDDED_JSON_MAX_CHARS = 1024 * 1024
# Redacting a parsed document takes about one stack frame per nesting level, and
# a document can hold further JSON documents in its strings. All documents
# parsed within one value share this frame budget, so ordinary nested JSON is
# parsed while deeply nested or deeply embedded input falls back to text
# redaction instead of exhausting the interpreter's recursion limit.
_EMBEDDED_JSON_FRAME_BUDGET = 256
_EMBEDDED_JSON_FRAMES_PER_DOCUMENT = 4
_embedded_json_budget: ContextVar[int] = ContextVar(
    "embedded_json_budget", default=_EMBEDDED_JSON_FRAME_BUDGET
)

# A quoted key and its scalar value in JSON-style text, including truncated
# JSON that cannot be parsed. Keys stop at the next quote, so failed matches
# cost no more than the distance to that quote.
_JSON_PAIR_RE = re.compile(
    r'"((?:[^"\\\r\n]|\\[^\r\n]){1,128})"(\s{0,16}:\s{0,16})'
    r'("(?:[^"\\]|\\[\s\S])*(?:"|\\?\Z)|[^\s,:{}\[\]"]{1,1024})'
)


def _normalize_key(k: str) -> str:
    return str(k).strip().lower().replace("-", "_")


def _parse_env_list(raw: str) -> set[str]:
    if not raw:
        return set()
    raw = raw.replace(",", " ")
    return {_normalize_key(p) for p in raw.split() if p.strip()}


def _key_to_regex_fragment(k: str) -> str:
    # allow - and _ interchangeably: api_key matches api-key too
    return re.escape(k).replace("_", "[-_]")


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

    # A partial fetch can end inside a quoted secret. Consume to end-of-input
    # when its closing quote is absent, including a final escape character.
    kv_re = re.compile(
        rf"""(?i)\b({alts})\b["']?\s*[:=]\s*("(?:\\[\s\S]|[^"\\])*(?:"|\\?\Z)|'(?:\\[\s\S]|[^'\\])*(?:'|\\?\Z)|[^&\s]+)"""
    )
    return keys, substring_needles, line_re, kv_re


def is_sensitive_key(k: str) -> bool:
    nk = _normalize_key(k)
    exact_keys, needles, _, _ = _sensitive_cfg()
    if nk in exact_keys:
        return True
    return any(n in nk for n in needles)


def _is_measured_value(nk: str, value: Any) -> bool:
    shape = MEASURED_FIELDS.get(nk)
    if shape is None or nk in _sensitive_cfg()[0]:
        return False
    if isinstance(value, bool):
        return shape == "boolean"
    return isinstance(value, str) and bool(
        _MEASURED_VALUE_SHAPES[shape].fullmatch(value)
    )


def should_redact_value(k: str, value: Any) -> bool:
    """Return true when the value stored under key k must be replaced."""
    if not is_sensitive_key(k):
        return False
    return not _is_measured_value(_normalize_key(k), value)


def _json_depth(obj: Any, limit: int) -> int | None:
    """Return the container depth of obj, or None when it exceeds limit."""
    deepest = 0
    stack = [(obj, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (dict, list)):
            if depth > limit:
                return None
            deepest = max(deepest, depth)
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, depth + 1) for child in children)
    return deepest


def _redact_embedded_json(s: str) -> str | None:
    """Redact a string holding a whole JSON object or array as structured data.

    Returns None when the string is not such a document, or when parsing it
    would exceed the frame budget. An unchanged document keeps its original
    text; a changed one is re-serialised compactly.
    """
    head = s.lstrip()[:1]
    budget = _embedded_json_budget.get() - _EMBEDDED_JSON_FRAMES_PER_DOCUMENT
    if head not in ("{", "[") or len(s) > _EMBEDDED_JSON_MAX_CHARS or budget < 1:
        return None
    try:
        parsed = json.loads(s)
    except (ValueError, RecursionError):
        return None
    depth = _json_depth(parsed, budget) if isinstance(parsed, (dict, list)) else None
    if depth is None:
        return None
    token = _embedded_json_budget.set(budget - depth)
    try:
        redacted = redact_obj(parsed)
    finally:
        _embedded_json_budget.reset(token)
    if redacted == parsed:
        return s
    return json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))


def _redact_json_pairs(txt: str) -> str:
    def _sub(m: re.Match) -> str:
        quoted = m.group(3).startswith('"')
        value = m.group(3)[1:-1] if quoted else m.group(3)
        if value == REDACTED or not should_redact_value(m.group(1), value):
            return m.group(0)
        return f'"{m.group(1)}"{m.group(2)}"{REDACTED}"'

    return _JSON_PAIR_RE.sub(_sub, txt)


def redact_query_string(
    qs: str | None, extra_keys: frozenset[str] = frozenset()
) -> str | None:
    if not qs:
        return None
    parts: list[tuple[str, str]] = []
    for k, v in parse_qsl(qs, keep_blank_values=True):
        if (is_sensitive_key(k) or _normalize_key(k) in extra_keys) and v:
            parts.append((k, REDACTED))
        else:
            parts.append((k, v))
    return urlencode(parts, doseq=True)


def redact_url(url: str) -> str:
    """Remove userinfo and sensitive query/fragment values from displayed URLs."""
    try:
        parts = urlsplit(url)
        netloc = parts.netloc
        if "@" in netloc:
            netloc = REDACTED + "@" + netloc.rsplit("@", 1)[1]
        query = redact_query_string(parts.query) or ""
        # OAuth implicit responses and copied links can carry tokens in fragments.
        fragment = parts.fragment
        if "=" in fragment:
            fragment = redact_query_string(fragment) or ""
        return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))
    except (ValueError, TypeError):
        # A malformed credential-bearing URL must not escape redaction.
        return REDACTED


def redact_str(s: str | None) -> str | None:
    if s is None:
        return None
    structured = _redact_embedded_json(str(s))
    if structured is not None:
        return structured
    txt = _URL_RE.sub(lambda m: redact_url(m.group(0)), str(s))
    # JSON-style pairs keep their syntax and use the same key policy as objects.
    txt = _redact_json_pairs(txt)

    # Free-text key=value redaction
    _, _, line_re, kv_re = _sensitive_cfg()

    def _kv_sub(m: re.Match) -> str:
        if m.group(2).strip("\"'") == REDACTED:
            return m.group(0)
        return f"{m.group(1)}={REDACTED}"

    txt = line_re.sub(lambda m: f"{m.group(1)}={REDACTED}", txt)
    txt = kv_re.sub(_kv_sub, txt)

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
            if isinstance(k, str) and should_redact_value(k, v):
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
    txt = _MAILBOX_RE.sub(MASKED_EMAIL, txt)
    txt = _EMAIL_RE.sub(MASKED_EMAIL, txt)

    def _ipv6_sub(m: re.Match) -> str:
        whole = m.group(0)
        # Punctuation after an address is part of the candidate match; keep it
        # outside the address so "from fe80::1." and "address 2001:db8::1: not
        # known" are still masked. An address ends in a colon only as "::".
        raw = whole.rstrip(".")
        if raw.endswith(":") and not raw.endswith("::"):
            raw = raw[:-1]
        suffix = whole[len(raw) :]
        # Strip a zone id for validation only (e.g. fe80::1%eth0).
        val = raw.split("%", 1)[0]
        # Do not even try to treat short colon-delimited strings as IPv6.
        # Times such as 13:40:19 contain two colons but are not addresses.
        if "::" not in val and val.count(":") < 3:
            return whole
        # A candidate with colons parses only as IPv6.
        try:
            ipaddress.ip_address(val)
        except ValueError:
            return whole
        return MASKED_IP + suffix

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


def storable(value: Any) -> Any:
    """Replace NUL characters, which PostgreSQL text and JSON values cannot hold.

    Collected evidence can contain NUL characters from upstream systems. The
    replacement character keeps the position visible, and the record is stored
    instead of failing the whole collection batch.
    """
    if isinstance(value, str):
        return value.replace("\x00", "\ufffd") if "\x00" in value else value
    if isinstance(value, list):
        return [storable(item) for item in value]
    if isinstance(value, tuple):
        return tuple(storable(item) for item in value)
    if isinstance(value, dict):
        return {storable(key): storable(item) for key, item in value.items()}
    return value


def prepare_event_fields(fields: dict, *, mask: bool = False) -> dict:
    """Protect event columns and payloads equally in ORM and bulk insert paths."""
    result = redact_obj(fields)
    return storable(mask_event_data_obj(result) if mask else result)


def prepare_artifact_bytes(
    data: bytes, content_type: str | None, *, mask: bool = False
):
    data, status = redact_bytes(data, content_type)
    if mask:
        data, masked = mask_event_data_bytes(data, content_type)
        status = combine_redaction_status(status, masked)
    return data, status
