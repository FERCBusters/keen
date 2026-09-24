from __future__ import annotations

import hashlib
import os
import re
import time
from functools import lru_cache
from dataclasses import dataclass
from typing import Any

from app.core.managed_configuration import load_document

from app.core.config import settings


@dataclass(frozen=True)
class SourceMeta:
    """UI-facing display metadata for an event source."""

    source: str
    label: str
    color: str  # normalized hex: #RRGGBB


_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")
_HEX3_RE = re.compile(r"^#?[0-9a-fA-F]{3}$")
_ENV_PREFIX_NAME = "KEEN_SOURCE_NAME_"
_ENV_PREFIX_COLOR = "KEEN_SOURCE_COLOR_"


def _normalize_hex_color(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if _HEX3_RE.match(s):
        s = s[1:] if s.startswith("#") else s
        s = "#" + "".join(ch * 2 for ch in s)
        return s.upper()
    if _HEX_RE.match(s):
        if not s.startswith("#"):
            s = "#" + s
        return s.upper()
    return None


def normalize_hex_color(raw: str | None) -> str | None:
    """Normalize a hex color string to '#RRGGBB' (upper-case).

    Accepts '#RGB', 'RGB', '#RRGGBB', or 'RRGGBB'. Returns None if invalid.
    """
    return _normalize_hex_color(raw)


def _humanize_source(source: str) -> str:
    # Keep colon semantics readable (e.g. webhook:something -> Webhook: Something)
    parts = [p for p in re.split(r"(:)", source) if p != ""]
    out: list[str] = []
    for p in parts:
        if p == ":":
            out.append(":")
            continue
        # replace separators with spaces and Title Case
        s = re.sub(r"[_-]+", " ", p).strip()
        out.append(s[:1].upper() + s[1:])
    # Ensure spacing around colons is pretty
    joined = " ".join(out).replace(" : ", ": ").replace(" :", ":")
    return joined.strip() or source


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    # h: 0-360, s/l: 0-1
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs(((h / 60) % 2) - 1))
    m = l - c / 2
    if 0 <= h < 60:
        r1, g1, b1 = c, x, 0
    elif 60 <= h < 120:
        r1, g1, b1 = x, c, 0
    elif 120 <= h < 180:
        r1, g1, b1 = 0, c, x
    elif 180 <= h < 240:
        r1, g1, b1 = 0, x, c
    elif 240 <= h < 300:
        r1, g1, b1 = x, 0, c
    else:
        r1, g1, b1 = c, 0, x
    r = int(round((r1 + m) * 255))
    g = int(round((g1 + m) * 255))
    b = int(round((b1 + m) * 255))
    return max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))


def _auto_color(source: str) -> str:
    # Deterministic but pleasant colors.
    h = int(hashlib.sha1(source.encode("utf-8")).hexdigest()[:8], 16) % 360
    # tuned for readable text with a light background tint in the UI
    r, g, b = _hsl_to_rgb(float(h), 0.62, 0.42)
    return f"#{r:02X}{g:02X}{b:02X}"


def _env_key(source: str) -> str:
    # SOURCE_NAME_<KEY> where <KEY> is upper and non-alnum -> underscore.
    return re.sub(r"[^A-Za-z0-9]", "_", source).upper()


def _merge(
    meta: dict[str, dict[str, str]],
    source: str,
    *,
    label: str | None,
    color: str | None,
) -> None:
    cur = meta.get(source) or {}
    if label:
        cur["label"] = label
    if color:
        cur["color"] = color
    if cur:
        meta[source] = cur


@lru_cache(maxsize=2)
def _configured_source_meta(refresh_bucket: int) -> dict[str, dict[str, str]]:
    """Load configured source meta from YAML configs + env overrides."""

    meta: dict[str, dict[str, str]] = {}

    # Plugin configs (poll + other ingestion providers)
    plugin_cfgs: dict[str, str] = {
        "loki": settings.loki_queries_path,
        "cloudwatch_logs": settings.cloudwatch_logs_config_path,
        "github": settings.github_config_path,
        "forgejo": settings.forgejo_config_path,
        "jenkins": settings.jenkins_config_path,
        "taiga": settings.taiga_config_path,
        "bookstack": settings.bookstack_config_path,
        "rss": settings.rss_config_path,
        "google_workspace": settings.google_workspace_config_path,
    }

    for src, path in plugin_cfgs.items():
        cfg = load_document(src, path)
        label = (cfg.get("ui_name") or cfg.get("display_name") or "").strip() or None
        color = _normalize_hex_color(cfg.get("ui_color") or cfg.get("badge_color"))
        _merge(meta, src, label=label, color=color)

    # Webhooks: providers -> webhook:<provider>
    wcfg = load_document("webhooks", settings.webhooks_path)
    providers = wcfg.get("providers") or {}
    if isinstance(providers, dict):
        for prov, pcfg in providers.items():
            if not isinstance(pcfg, dict):
                continue
            src = f"webhook:{prov}"
            label = (
                pcfg.get("ui_name") or pcfg.get("display_name") or ""
            ).strip() or None
            color = _normalize_hex_color(
                pcfg.get("ui_color") or pcfg.get("badge_color")
            )
            _merge(meta, src, label=label, color=color)

    # Env overrides (highest priority)
    # - KEEN_SOURCE_NAME_TAIGA=Tracker
    # - KEEN_SOURCE_COLOR_TAIGA=#FF00AA
    env = os.environ
    # Build a lookup for all env keys we find.
    name_by_key: dict[str, str] = {}
    color_by_key: dict[str, str] = {}
    for k, v in env.items():
        if k.startswith(_ENV_PREFIX_NAME):
            key = k[len(_ENV_PREFIX_NAME) :].strip().upper()
            if key and v.strip():
                name_by_key[key] = v.strip()
        elif k.startswith(_ENV_PREFIX_COLOR):
            key = k[len(_ENV_PREFIX_COLOR) :].strip().upper()
            c = _normalize_hex_color(v)
            if key and c:
                color_by_key[key] = c

    # Apply env overrides for any known configured sources
    known_sources = set(meta.keys())
    for src in list(known_sources):
        key = _env_key(src)
        _merge(meta, src, label=name_by_key.get(key), color=color_by_key.get(key))

    # Also allow env overrides for sources not in config (e.g. diary)
    for k in set(name_by_key.keys()) | set(color_by_key.keys()):
        # We cannot reliably reverse the env key to a source string, so we only
        # support env overrides for non-config sources via explicit key match on
        # the raw source (e.g. SOURCE_NAME_DIARY for source="diary").
        # This is handled at lookup time.
        pass

    return meta


def get_source_meta(source: str) -> SourceMeta:
    """Return UI display metadata for a source.

    Priority:
      1) Env overrides (KEEN_SOURCE_NAME_*/KEEN_SOURCE_COLOR_*) for this source
      2) YAML config (ui_name/ui_color) for this source
      3) Generated defaults (humanized label + deterministic color)
    """

    src = (source or "").strip() or "unknown"
    cfg = _configured_source_meta(int(time.monotonic() // 30)).get(src) or {}

    # Env overrides for this specific source (works for any source key)
    ek = _env_key(src)
    env_label = os.environ.get(f"{_ENV_PREFIX_NAME}{ek}")
    env_color = _normalize_hex_color(os.environ.get(f"{_ENV_PREFIX_COLOR}{ek}"))

    label = (env_label or cfg.get("label") or "").strip() or _humanize_source(src)
    color = env_color or _normalize_hex_color(cfg.get("color")) or _auto_color(src)

    return SourceMeta(source=src, label=label, color=color)


def apply_user_source_overrides(
    meta: SourceMeta, source_colors: dict[str, str] | None
) -> SourceMeta:
    """Apply per-user source color overrides (if any).

    Only color is currently overridable. If the user has no override for a
    given source, the configured/default color is used.
    """
    if not source_colors or not isinstance(source_colors, dict):
        return meta
    raw = source_colors.get(meta.source)
    col = normalize_hex_color(raw)
    if col:
        return SourceMeta(source=meta.source, label=meta.label, color=col)
    return meta
