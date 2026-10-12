"""Control reference formatting and catalogue links shared across entry points."""

from __future__ import annotations

import re
import uuid
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import urlsplit

import yaml

from app.core.config import settings

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
          iso_27001_2022:
            A.5.1: https://...
          UK-DVSTF:1.0:
            12.5.a: https://...

      Short framework-map form:
        iso_27001_2022:
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


def absolute_http_url(value: Any) -> str | None:
    """Return an absolute http(s) link suitable for display, otherwise None."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if (
        not url
        or len(url) > 2048
        or "\\" in url
        or any(ord(c) < 32 or ord(c) == 127 for c in url)
    ):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    return url


def control_upstream_url(control: Any) -> str | None:
    """Compute the upstream URL for a control (metadata overrides YAML).

    Imported catalogue metadata is not trusted to contain only web links.
    """
    try:
        meta = getattr(control, "meta", None) or {}
        if isinstance(meta, dict):
            u = absolute_http_url(meta.get("upstream_url") or meta.get("upstreamUrl"))
            if u:
                return u
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
