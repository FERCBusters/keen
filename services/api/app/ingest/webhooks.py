from __future__ import annotations

import hashlib
import json
import hmac
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.managed_configuration import load_document
from app.core.config import settings
from app.core.valkey import get_valkey
from app.ingest.common import store_event_with_artifact

_MAX_WEBHOOK_PAYLOAD_BYTES = 10 * 1024 * 1024  # 10MB
# Replay protection: reject webhooks older than 15 minutes
_WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS = 900


def load_webhook_policies(path: str) -> dict[str, Any]:
    return load_document("webhooks", path)


def verify_secret(provider: str, headers: dict[str, str]) -> bool:
    pol = (
        load_webhook_policies(settings.webhooks_path)
        .get("providers", {})
        .get(provider, {})
    )
    secret_header = pol.get("secret_header")
    secret_env = pol.get("secret_env")
    # Fail closed by default: if the provider is not configured with a secret
    # policy, reject the request.
    if not secret_header or not secret_env:
        return not bool(settings.webhooks_require_secret)

    import os

    expected = os.environ.get(secret_env)

    if not expected:
        return False

    # Case-insensitive header lookup.
    headers_lc = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    got = headers_lc.get(str(secret_header).lower())
    if not got:
        return False

    # Constant-time compare.
    return hmac.compare_digest(str(got), str(expected))


def _fingerprint(provider: str, event_type: str, body: bytes) -> str:
    return hashlib.sha256(f"{provider}|{event_type}|".encode() + body).hexdigest()[:32]


def _check_replay_protection(
    provider: str, event_type: str, body: bytes, headers: dict[str, str]
) -> bool:
    """Check for replay attacks using Redis-based deduplication with TTL.

    Validates timestamp headers if present and rejects payloads outside the tolerance window.
    Returns True if the webhook should be rejected as a potential replay.
    """
    import time as time_module

    r = get_valkey()

    timestamp_header = headers.get("x-webhook-timestamp") or headers.get("x-timestamp")
    if timestamp_header:
        try:
            webhook_ts = int(timestamp_header)
            current_ts = int(time_module.time())
            age_seconds = current_ts - webhook_ts

            if age_seconds < 0:
                return True

            if age_seconds > _WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS:
                return True
        except (ValueError, TypeError):
            pass

    payload_hash = hashlib.sha256(body).hexdigest()
    replay_key = f"keen:webhook:replay:{provider}:{event_type}:{payload_hash}"

    existing = r.set(
        replay_key, "1", nx=True, ex=_WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS + 60
    )
    return existing is False


def ingest_webhook(
    db: Session, provider: str, event_type: str, body: bytes, headers: dict[str, str]
) -> dict[str, Any]:
    if len(body) > _MAX_WEBHOOK_PAYLOAD_BYTES:
        raise ValueError("Webhook payload too large")

    # Check for replay attacks with timestamp validation
    if _check_replay_protection(provider, event_type, body, headers):
        raise ValueError("Potential replay attack detected: duplicate webhook payload")

    summary = f"webhook:{provider}/{event_type}"
    try:
        parsed = json.loads(body.decode("utf-8"))
    except Exception:
        parsed = {"raw": body.decode("utf-8", errors="replace")}

    pol = (
        load_webhook_policies(settings.webhooks_path)
        .get("providers", {})
        .get(provider, {})
    )
    for k in pol.get("summary_hints", []) or []:
        if isinstance(parsed, dict) and k in parsed:
            hint_val = str(parsed.get(k) or "")
            hint_val = hint_val.replace("\n", " ").replace("\r", " ")
            summary += f" {k}={hint_val}"

    external_id = _fingerprint(provider, event_type, body)
    ts = datetime.now(timezone.utc)
    key = f"webhooks/{provider}/{event_type}/{ts.date().isoformat()}/{external_id}.json"

    res = store_event_with_artifact(
        db,
        timestamp=ts,
        source=f"webhook:{provider}",
        system=None,
        actor=None,
        action=event_type,
        outcome="info",
        severity=3,
        summary=summary,
        raw_pointer={
            "webhook": {
                "provider": provider,
                "event_type": event_type,
                "headers": {k: headers.get(k) for k in list(headers)[:30]},
            }
        },
        normalized_payload=parsed if isinstance(parsed, dict) else {"payload": parsed},
        external_id=external_id,
        artifact_kind="webhook_payload",
        artifact_bytes=body,
        artifact_content_type="application/json",
        artifact_key=key,
        captured_by=f"keen:webhook:{provider}",
    )
    return res
