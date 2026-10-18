"""Control reference formatting and catalogue links shared across entry points."""

from __future__ import annotations

import logging
import re
import uuid
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import urlsplit

import yaml

from app.core.config import settings

log = logging.getLogger(__name__)

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


# Control and clause references are stored in columns of this length.
_LINK_REF_LIMIT = 64
_FRAMEWORK_SLUG = re.compile(r"[A-Za-z0-9._:-]{1,64}")


def _skip_link(path: str, entry: str, problem: str) -> None:
    log.warning("Skipping control link %s in %s: %s", entry[:160], path, problem)


@lru_cache(maxsize=8)
def load_control_links(path: str) -> dict[str, dict[str, str]]:
    """Load each framework's control references and their guidance links.

    The file names the links under ``frameworks``::

        frameworks:
          iso_27001_2022:
            A.5.1: https://...
          UK-DVSTF:1.0:
            12.5.a: https://...

    An entry that is not a framework of references to absolute web links is
    skipped and logged, and the rest of the file still applies. A missing or
    empty file means no catalogue links. Each file is read once per process.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        log.warning("Ignoring control links file %s: %s", path, exc)
        return {}
    if not data:
        return {}
    frameworks = data.get("frameworks") if isinstance(data, dict) else None
    if not isinstance(frameworks, dict):
        log.warning("Ignoring control links file %s: it needs a frameworks map", path)
        return {}
    for key in data:
        if key != "frameworks":
            _skip_link(path, repr(key), "only frameworks is read")

    out: dict[str, dict[str, str]] = {}
    for framework, links in frameworks.items():
        if not isinstance(framework, str) or not _FRAMEWORK_SLUG.fullmatch(framework):
            _skip_link(path, repr(framework), "not a framework identifier")
            continue
        if not isinstance(links, dict):
            _skip_link(path, framework, "needs a map of references to links")
            continue
        for ref, url in links.items():
            entry = f"{framework} {ref!r}"
            if (
                not isinstance(ref, str)
                or not ref.strip()
                or len(ref.strip()) > _LINK_REF_LIMIT
            ):
                _skip_link(path, entry, "not a control or clause reference")
            elif absolute_http_url(url) is None:
                _skip_link(path, entry, "needs an absolute http or https link")
            else:
                out.setdefault(framework, {})[ref.strip()] = url.strip()
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
    """Return the guidance link for a control or clause row.

    The row's catalogue metadata names it first, and the control links file
    otherwise names it under the row's own framework. Imported catalogue
    metadata may hold any text, so it counts only as an absolute web link.
    """
    meta = getattr(control, "meta", None)
    if isinstance(meta, dict):
        url = absolute_http_url(meta.get("upstream_url"))
        if url:
            return url
    ref = str(getattr(control, "ref", None) or "").strip()
    framework = str(getattr(control, "framework_slug", None) or "").strip()
    if not ref or not framework:
        return None
    return load_control_links(settings.control_links_path).get(framework, {}).get(ref)
