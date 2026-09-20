from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

SESSION_PREFIX = "keen:session:"
AUTHZ_VERSION_PREFIX = "keen:authz-version:user:"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_session_id() -> str:
    # URL-safe, high-entropy session id
    return secrets.token_urlsafe(32)


def session_key(session_id: str) -> str:
    return f"{SESSION_PREFIX}{session_id}"


def authz_version_key(user_id: str) -> str:
    return f"{AUTHZ_VERSION_PREFIX}{user_id}"


def _coerce_authz_version(raw: Any) -> int | None:
    try:
        if raw is None:
            return 0
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        return max(0, int(raw or 0))
    except Exception:
        return None


def get_authz_version(redis_client, user_id: str) -> int | None:
    if not user_id:
        return None
    try:
        return _coerce_authz_version(redis_client.get(authz_version_key(str(user_id))))
    except Exception:
        # Callers must treat None as a cache miss and fall back to authoritative
        # DB-backed checks. Returning 0 here could incorrectly validate an old
        # permission snapshot.
        return None


def bump_authz_versions(redis_client, user_ids) -> None:
    ids = sorted({str(uid) for uid in (user_ids or []) if uid})
    if not ids:
        return
    try:
        pipe = redis_client.pipeline()
        for uid in ids:
            pipe.incr(authz_version_key(uid))
        pipe.execute()
    except Exception:
        # Do not make admin RBAC writes fail just because the session cache could
        # not be invalidated. Existing requests will still be DB-authoritative when
        # the cache cannot be read/refreshed.
        pass


def create_session(
    redis_client,
    *,
    user_id: str,
    ttl_seconds: int,
    auth_method: str = "local",
    oidc_id_token: str | None = None,
    effective_role: str | None = None,
    permission_codes: list[str] | set[str] | tuple[str, ...] | None = None,
    authz_version: int | None = None,
) -> str:
    session_id = make_session_id()
    key = session_key(session_id)
    payload = {
        "user_id": user_id,
        "created_at": _utc_iso(),
        "auth_method": (auth_method or "local")[:20],
    }
    if oidc_id_token:
        payload["oidc_id_token"] = oidc_id_token
    if effective_role:
        payload["effective_role"] = str(effective_role)
    if permission_codes is not None:
        payload["permission_codes"] = sorted({str(c) for c in permission_codes if c})
    if authz_version is not None:
        payload["authz_version"] = int(authz_version)
    redis_client.set(key, json.dumps(payload), ex=int(ttl_seconds))
    return session_id


def get_session(
    redis_client, session_id: str, *, refresh_ttl_seconds: int | None = None
) -> dict[str, Any] | None:
    if not session_id:
        return None
    key = session_key(session_id)
    raw = redis_client.get(key)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    if refresh_ttl_seconds:
        try:
            redis_client.expire(key, int(refresh_ttl_seconds))
        except Exception:
            pass
    if not isinstance(payload, dict):
        return None
    return payload


def update_session_authz(
    redis_client,
    session_id: str,
    *,
    effective_role: str,
    permission_codes: list[str] | set[str] | tuple[str, ...],
    authz_version: int,
    ttl_seconds: int,
) -> None:
    if not session_id:
        return
    key = session_key(session_id)
    raw = redis_client.get(key)
    if not raw:
        return
    try:
        payload = json.loads(raw)
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    payload["effective_role"] = str(effective_role or "")
    payload["permission_codes"] = sorted({str(c) for c in permission_codes if c})
    payload["authz_version"] = int(authz_version)
    redis_client.set(key, json.dumps(payload), ex=int(ttl_seconds))


def delete_session(redis_client, session_id: str) -> None:
    if not session_id:
        return
    try:
        redis_client.delete(session_key(session_id))
    except Exception:
        pass
