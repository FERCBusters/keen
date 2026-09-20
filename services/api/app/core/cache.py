from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Callable, TypeVar

from app.core.config import settings
from app.core.valkey import get_valkey

T = TypeVar("T")


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _normalise_part(value: Any) -> Any:
    """Convert cache key parts into stable JSON-serialisable values."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _normalise_part(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple, set)):
        return [_normalise_part(v) for v in value]
    return value


def user_cache_scope(user: Any) -> str:
    """Return a conservative per-user cache scope for visibility-sensitive data."""

    uid = getattr(user, "id", None)
    if uid:
        return f"user:{uid}"
    return "anonymous"


def make_cache_key(namespace: str, parts: dict[str, Any] | None = None) -> str:
    payload = _normalise_part(parts or {})
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=_json_default
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    ns = "".join(
        ch if ch.isalnum() or ch in ("-", "_", ":") else "_" for ch in namespace
    )
    return f"keen:cache:v1:{ns}:{digest}"


def cache_get_json(namespace: str, parts: dict[str, Any] | None = None) -> Any | None:
    ttl = int(getattr(settings, "aggregate_cache_ttl_seconds", 0) or 0)
    if ttl <= 0:
        return None
    try:
        raw = get_valkey().get(make_cache_key(namespace, parts))
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def cache_set_json(
    namespace: str, parts: dict[str, Any] | None, value: Any, *, ttl: int | None = None
) -> None:
    ttl_eff = int(
        ttl
        if ttl is not None
        else getattr(settings, "aggregate_cache_ttl_seconds", 0) or 0
    )
    if ttl_eff <= 0:
        return
    try:
        raw = json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=_json_default
        )
        get_valkey().set(make_cache_key(namespace, parts), raw, ex=ttl_eff)
    except Exception:
        return


def cached_json(
    namespace: str,
    parts: dict[str, Any] | None,
    factory: Callable[[], T],
    *,
    ttl: int | None = None,
) -> T:
    ttl_eff = int(
        ttl
        if ttl is not None
        else getattr(settings, "aggregate_cache_ttl_seconds", 0) or 0
    )
    if ttl_eff <= 0:
        return factory()

    key_parts = parts or {}
    try:
        raw = get_valkey().get(make_cache_key(namespace, key_parts))
        if raw:
            return json.loads(raw)
    except Exception:
        pass

    value = factory()

    try:
        raw = json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=_json_default
        )
        get_valkey().set(make_cache_key(namespace, key_parts), raw, ex=ttl_eff)
    except Exception:
        pass

    return value


def cache_delete_prefix(prefix: str = "keen:cache:v1:") -> int:
    """Best-effort deletion of cached aggregate keys with the supplied prefix."""

    try:
        client = get_valkey()
        deleted = 0
        batch: list[str] = []
        for key in client.scan_iter(f"{prefix}*"):
            batch.append(str(key))
            if len(batch) >= 500:
                deleted += int(client.delete(*batch) or 0)
                batch = []
        if batch:
            deleted += int(client.delete(*batch) or 0)
        return deleted
    except Exception:
        return 0
