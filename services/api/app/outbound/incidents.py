from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_plain_text(value: str | None, *, max_chars: int) -> str:
    """Return safe plain text for storage and outbound JSON payloads.

    The UI renders the value as text, not HTML. This keeps intentional newlines
    and tabs, removes control characters, normalises CRLF to LF, trims outer
    whitespace, and enforces the requested character cap.
    """

    txt = str(value or "")
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    txt = _CONTROL_CHARS_RE.sub("", txt)
    txt = txt.strip()
    if len(txt) > max_chars:
        txt = txt[:max_chars]
    return txt


def _parse_json_headers(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in obj.items():
            if k is None or v is None:
                continue
            key = str(k).strip()
            if not key or any(ord(ch) < 33 or ord(ch) == 127 for ch in key):
                continue
            out[key] = str(v)
        return out
    except Exception:
        return {}


def _hmac_sha256(secret: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def incident_webhook_enabled() -> bool:
    return bool((settings.incident_webhook_url or "").strip())


def build_event_url(request, event_id: str) -> str:
    base = (settings.public_base_url or "").strip().rstrip("/")
    if not base:
        origin = (request.headers.get("origin") or "").strip().rstrip("/")
        if origin:
            base = origin
    if not base:
        proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
        host = (
            (
                request.headers.get("x-forwarded-host")
                or request.headers.get("host")
                or ""
            )
            .split(",")[0]
            .strip()
        )
        if host:
            base = f"{proto or request.url.scheme}://{host}"
    qs = urlencode({"id": event_id})
    return f"{base}/event.html?{qs}" if base else f"/event.html?{qs}"


@dataclass(frozen=True)
class IncidentWebhookConfig:
    url: str
    headers: dict[str, str]
    signing_secret: str
    signature_header: str
    timeout_seconds: float

    @classmethod
    def from_settings(cls) -> "IncidentWebhookConfig":
        return cls(
            url=(settings.incident_webhook_url or "").strip(),
            headers=_parse_json_headers(settings.incident_webhook_headers_json),
            signing_secret=(settings.incident_webhook_signing_secret or "").strip(),
            signature_header=(
                settings.incident_webhook_signature_header or "X-Keen-Signature"
            ).strip()
            or "X-Keen-Signature",
            timeout_seconds=float(settings.incident_webhook_timeout_seconds or 6.0),
        )

    def validate(self) -> None:
        if not self.url:
            raise ValueError("Incident webhook is not configured")
        if not self.url.startswith(("https://", "http://")):
            raise ValueError("Incident webhook URL must start with http:// or https://")


def build_incident_payload(
    *,
    incident_id: str,
    title: str,
    text: str,
    created_at: str,
    created_by: str | None,
    event: dict[str, Any],
    event_url: str,
) -> dict[str, Any]:
    return {
        "type": "keen.incident.created",
        "incident": {
            "id": incident_id,
            "title": title,
            "text": text,
            "created_at": created_at,
            "created_by": created_by,
        },
        "event": {
            "id": event.get("id"),
            "url": event_url,
            "timestamp": event.get("timestamp"),
            "source": event.get("source"),
            "system": event.get("system"),
            "actor": event.get("actor"),
            "action": event.get("action"),
            "outcome": event.get("outcome"),
            "severity": event.get("severity"),
            "summary": event.get("summary"),
        },
        "links": {"event": event_url},
    }


def post_incident_webhook(
    config: IncidentWebhookConfig, payload: dict[str, Any]
) -> tuple[int, dict[str, str]]:
    """Send the canonical incident payload and return status + response headers."""

    config.validate()
    timeout_seconds = max(1.0, min(float(config.timeout_seconds or 6.0), 30.0))
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0))
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    headers = dict(config.headers or {})
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("User-Agent", "Keen Incident Webhook")
    if config.signing_secret:
        headers[config.signature_header] = _hmac_sha256(config.signing_secret, body)

    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.post(config.url, content=body, headers=headers)
        if not 200 <= int(response.status_code) < 300:
            log.warning("incident webhook returned HTTP %s", response.status_code)
        safe_headers = {
            k.lower(): v
            for k, v in response.headers.items()
            if k.lower() in {"location", "x-request-id", "x-correlation-id"}
        }
        return int(response.status_code), safe_headers
