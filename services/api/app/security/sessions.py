from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

SESSION_PREFIX = "keen:session:"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_session_id() -> str:
    # URL-safe, high-entropy session id
    return secrets.token_urlsafe(32)


def session_key(session_id: str) -> str:
    return f"{SESSION_PREFIX}{session_id}"


def create_session(
    redis_client,
    *,
    user_id: str,
    ttl_seconds: int,
    auth_method: str = "local",
    oidc_id_token: str | None = None,
    mfa_version: int = 0,
    session_version: int = 0,
    mfa_verified: bool = False,
    effective_role: str | None = None,
    permission_codes: list[str] | set[str] | tuple[str, ...] | None = None,
    authz_version: int | None = None,
) -> str:
    session_id = make_session_id()
    key = session_key(session_id)
    payload = {
        "user_id": user_id,
        "created_at": _utc_iso(),
        "mfa_version": int(mfa_version),
        "session_version": int(session_version),
        "mfa_verified": bool(mfa_verified),
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
    # WATCH prevents a concurrent logout from being undone by this refresh.
    # A conflict needs no retry: the next request can recompute its permissions.
    from redis.exceptions import WatchError

    try:
        with redis_client.pipeline() as pipe:
            pipe.watch(key)
            raw = pipe.get(key)
            if not raw:
                return
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                return
            if not isinstance(payload, dict):
                return
            payload["effective_role"] = str(effective_role or "")
            payload["permission_codes"] = sorted(
                {str(c) for c in permission_codes if c}
            )
            payload["authz_version"] = int(authz_version)
            pipe.multi()
            pipe.set(key, json.dumps(payload), ex=int(ttl_seconds), xx=True)
            pipe.execute()
    except WatchError:
        return


def delete_session(redis_client, session_id: str) -> None:
    """Best-effort removal of a session already rejected by other checks."""
    if not session_id:
        return
    try:
        redis_client.delete(session_key(session_id))
    except Exception:
        pass


def revoke_session(redis_client, session_id: str) -> None:
    """Delete a session for an explicit sign-out; store failures propagate.

    A caller must not report sign-out while the server-side session remains
    valid, so unlike delete_session this never suppresses errors.
    """
    if not session_id:
        return
    redis_client.delete(session_key(session_id))
