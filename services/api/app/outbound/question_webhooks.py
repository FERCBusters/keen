from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlencode

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)


_SPLIT_RE = re.compile(r"[\s,]+")


def _split_urls(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in _SPLIT_RE.split(str(raw)) if p and p.strip()]
    # Preserve order but de-dupe.
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _safe_url_hint(url: str) -> str:
    """Return a log-safe hint for a URL (no query/token leakage)."""
    try:
        # Keep scheme + host only.
        m = re.match(r"^(https?://[^/]+)", url.strip())
        return m.group(1) if m else "(url)"
    except Exception:
        return "(url)"


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
            key_str = str(k)
            if not key_str or not re.match(r"^[a-zA-Z0-9_-]+$", key_str):
                continue
            out[key_str] = str(v)
        return out
    except Exception:
        return {}


def _hmac_sha256(secret: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _abs_url(path: str) -> str:
    base = (settings.public_base_url or "").strip().rstrip("/")
    if not base:
        return path
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def build_question_links(*, event_id: str, thread_id: str) -> dict[str, str]:
    qs = urlencode({"id": event_id, "thread": thread_id})
    return {
        "event": _abs_url(f"/event.html?{qs}#questions"),
        "admin_queue": _abs_url("/questions.html"),
    }


@dataclass(frozen=True)
class OutboundWebhookConfig:
    slack_urls: list[str]
    teams_urls: list[str]
    google_chat_urls: list[str]
    generic_urls: list[str]
    generic_headers: dict[str, str]
    generic_signing_secret: str
    generic_signature_header: str
    timeout_seconds: float

    @classmethod
    def from_settings(cls, *, event: str = "created") -> "OutboundWebhookConfig":
        """Build a config from env.

        event: "created" (new question) or "reply" (new post on existing thread).
        """

        ev = (event or "created").strip().lower()
        if ev not in ("created", "reply"):
            ev = "created"

        if ev == "reply":
            use_fallback = bool(
                getattr(
                    settings, "question_reply_webhook_use_question_urls_if_empty", True
                )
            )

            slack_raw = settings.question_reply_webhook_slack_urls or (
                settings.question_webhook_slack_urls if use_fallback else ""
            )
            teams_raw = settings.question_reply_webhook_teams_urls or (
                settings.question_webhook_teams_urls if use_fallback else ""
            )
            gchat_raw = settings.question_reply_webhook_google_chat_urls or (
                settings.question_webhook_google_chat_urls if use_fallback else ""
            )
            generic_raw = settings.question_reply_webhook_generic_urls or (
                settings.question_webhook_generic_urls if use_fallback else ""
            )
        else:
            slack_raw = settings.question_webhook_slack_urls
            teams_raw = settings.question_webhook_teams_urls
            gchat_raw = settings.question_webhook_google_chat_urls
            generic_raw = settings.question_webhook_generic_urls

        return cls(
            slack_urls=_split_urls(slack_raw),
            teams_urls=_split_urls(teams_raw),
            google_chat_urls=_split_urls(gchat_raw),
            generic_urls=_split_urls(generic_raw),
            generic_headers=_parse_json_headers(
                settings.question_webhook_generic_headers_json
            ),
            generic_signing_secret=str(
                settings.question_webhook_signing_secret or ""
            ).strip(),
            generic_signature_header=str(
                settings.question_webhook_signature_header or "X-Keen-Signature"
            ).strip()
            or "X-Keen-Signature",
            timeout_seconds=float(settings.question_webhook_timeout_seconds or 6.0),
        )

    def any_enabled(self) -> bool:
        return bool(
            self.slack_urls
            or self.teams_urls
            or self.google_chat_urls
            or self.generic_urls
        )


def build_generic_payload(
    *,
    event: dict[str, Any],
    thread: dict[str, Any],
    post: dict[str, Any],
    controls: list[dict[str, Any]] | None,
    links: dict[str, str],
    event_type: str = "keen.question.created",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical JSON payload sent to generic webhooks.

    event_type:
      - keen.question.created
      - keen.question.replied
    """

    payload: dict[str, Any] = {
        "type": event_type,
        "event": event,
        "thread": thread,
        "post": post,
        "controls": controls or [],
        "links": links,
    }
    if extra:
        try:
            payload.update(extra)
        except Exception:
            pass
    return payload


def _compact_text(payload: dict[str, Any]) -> str:
    """Build a friendly plain-text summary used for Slack/Teams/Chat."""

    ev = payload.get("event") or {}
    post = payload.get("post") or {}
    thread = payload.get("thread") or {}
    links = payload.get("links") or {}
    event_type = str(payload.get("type") or "").strip()

    ev_sum = str(ev.get("summary") or "(no summary)")
    ev_source = str(ev.get("source") or "")
    ev_ts = str(ev.get("timestamp") or "")

    thread_author = str(
        thread.get("created_by_username") or thread.get("created_by_user_id") or ""
    )
    post_author = str(post.get("author_username") or post.get("author_user_id") or "")

    body = str(post.get("body") or "")

    ctrls = payload.get("controls") or []
    ctrl_refs: list[str] = []
    try:
        for c in ctrls:
            ref = str((c or {}).get("ref") or "").strip()
            if ref:
                ctrl_refs.append(ref)
    except Exception:
        ctrl_refs = []

    ctrl_part = f"\nControls: {', '.join(ctrl_refs[:12])}" if ctrl_refs else ""
    if len(ctrl_refs) > 12:
        ctrl_part += f" (+{len(ctrl_refs) - 12} more)"

    link = str(links.get("event") or "")
    link_part = f"\nOpen: {link}" if link else ""

    lines: list[str] = []

    if event_type == "keen.question.replied":
        reply = payload.get("reply") or {}
        as_role = str(reply.get("as") or "").strip()
        header = "New reply on question"
        if as_role:
            header = f"New {as_role} reply on question"

        lines.append(header)
        lines.append(f"Event: {ev_sum}")
        lines.append(f"Source: {ev_source}{(' • ' + ev_ts) if ev_ts else ''}")
        if thread_author:
            lines.append(f"Thread author: {thread_author}")
        if post_author:
            if as_role:
                lines.append(f"Replied by: {post_author} ({as_role})")
            else:
                lines.append(f"Replied by: {post_author}")
        st = str(thread.get("status") or "").strip()
        if st:
            lines.append(f"Thread status: {st}")
    else:
        lines.append("New question on event")
        lines.append(f"Event: {ev_sum}")
        lines.append(f"Source: {ev_source}{(' • ' + ev_ts) if ev_ts else ''}")
        if thread_author:
            lines.append(f"Asked by: {thread_author}")

    head = "\n".join([line for line in lines if str(line).strip()])

    return f"{head}\n\n{body}".strip() + ctrl_part + link_part


def slack_payload(payload: dict[str, Any]) -> dict[str, Any]:
    links = payload.get("links") or {}
    ev_link = str(links.get("event") or "")
    text = _compact_text(payload)

    # Slack supports mrkdwn and <url|label>.
    if ev_link:
        text = text.replace(f"Open: {ev_link}", f"Open: <{ev_link}|View in Keen>")

    event_type = str(payload.get("type") or "").strip()
    emoji = (
        ":question:" if event_type == "keen.question.created" else ":speech_balloon:"
    )
    return {"text": f"{emoji} {text}"}


def google_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {"text": _compact_text(payload)}


def teams_payload(payload: dict[str, Any]) -> dict[str, Any]:
    links = payload.get("links") or {}
    ev_link = str(links.get("event") or "")
    event_type = str(payload.get("type") or "").strip()
    is_reply = event_type == "keen.question.replied"

    # Microsoft Teams Incoming Webhook (MessageCard)
    ev = payload.get("event") or {}
    title = str(ev.get("summary") or ("New reply" if is_reply else "New question"))
    txt = _compact_text(payload)
    if ev_link:
        # MessageCard supports basic markdown links.
        txt += f"\n\n[View in Keen]({ev_link})"
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": "Keen question reply" if is_reply else "Keen question",
        "title": f"Keen: {title}",
        "text": txt,
    }


class _TemporarySendError(RuntimeError):
    pass


def _post_json(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> int:
    r = client.post(url, json=payload, headers=headers or {})
    return int(r.status_code)


def _send_many(
    *,
    client: httpx.Client,
    urls: Iterable[str],
    mk_payload,
    base_payload: dict[str, Any],
    extra_headers: dict[str, str] | None = None,
    log_prefix: str,
    retry_on_status: set[int] | None = None,
) -> tuple[int, int]:
    sent = 0
    failed = 0
    retry_on_status = retry_on_status or set()

    for url in urls:
        if not url:
            continue
        try:
            p = mk_payload(base_payload)
            code = _post_json(client, url, p, headers=extra_headers)
            if 200 <= code < 300:
                sent += 1
                continue
            failed += 1
            if code in retry_on_status or code >= 500:
                raise _TemporarySendError(
                    f"{log_prefix} temporary failure: HTTP {code}"
                )
            # 4xx is typically a configuration error; don't retry.
            log.warning(
                "%s webhook returned HTTP %s (%s)",
                log_prefix,
                code,
                _safe_url_hint(url),
            )
        except _TemporarySendError:
            raise
        except Exception as e:
            failed += 1
            # Network/DNS/etc. -> retry.
            raise _TemporarySendError(f"{log_prefix} send error: {e}")

    return sent, failed


def send_question_created_webhooks(
    config: OutboundWebhookConfig, generic_payload: dict[str, Any]
) -> dict[str, Any]:
    """Send the 'question created' webhook fanout.

    Returns a dict with counts by channel (best-effort).
    Raises _TemporarySendError for retry-worthy errors.
    """

    if not config.any_enabled():
        return {"enabled": False}

    timeout = httpx.Timeout(config.timeout_seconds)
    # Keep a short connect timeout; most endpoints are public.
    if timeout.connect is None:
        timeout = httpx.Timeout(
            config.timeout_seconds, connect=min(config.timeout_seconds, 3.0)
        )

    out: dict[str, Any] = {"enabled": True}

    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        # Slack
        try:
            s, f = _send_many(
                client=client,
                urls=config.slack_urls,
                mk_payload=slack_payload,
                base_payload=generic_payload,
                log_prefix="slack",
                retry_on_status={429},
            )
            out["slack_sent"], out["slack_failed"] = s, f
        except _TemporarySendError:
            raise
        except Exception:
            # shouldn't happen, but keep fanout resilient
            out["slack_sent"], out["slack_failed"] = 0, len(config.slack_urls)

        # Teams
        try:
            s, f = _send_many(
                client=client,
                urls=config.teams_urls,
                mk_payload=teams_payload,
                base_payload=generic_payload,
                log_prefix="teams",
                retry_on_status={429},
            )
            out["teams_sent"], out["teams_failed"] = s, f
        except _TemporarySendError:
            raise
        except Exception:
            out["teams_sent"], out["teams_failed"] = 0, len(config.teams_urls)

        # Google Chat
        try:
            s, f = _send_many(
                client=client,
                urls=config.google_chat_urls,
                mk_payload=google_chat_payload,
                base_payload=generic_payload,
                log_prefix="google_chat",
                retry_on_status={429},
            )
            out["google_chat_sent"], out["google_chat_failed"] = s, f
        except _TemporarySendError:
            raise
        except Exception:
            out["google_chat_sent"], out["google_chat_failed"] = 0, len(
                config.google_chat_urls
            )

        # Generic (send canonical JSON so optional signature can be verified)
        generic_headers = dict(config.generic_headers or {})
        body = json.dumps(
            generic_payload, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        generic_headers.setdefault("Content-Type", "application/json")
        if config.generic_signing_secret:
            generic_headers[config.generic_signature_header] = _hmac_sha256(
                config.generic_signing_secret, body
            )

        g_sent = 0
        g_failed = 0
        for url in config.generic_urls:
            if not url:
                continue
            try:
                r = client.post(url, content=body, headers=generic_headers)
                code = int(r.status_code)
                if 200 <= code < 300:
                    g_sent += 1
                else:
                    g_failed += 1
                    if code == 429 or code >= 500:
                        raise _TemporarySendError(
                            f"generic temporary failure: HTTP {code}"
                        )
                    log.warning(
                        "generic webhook returned HTTP %s (%s)",
                        code,
                        _safe_url_hint(url),
                    )
            except _TemporarySendError:
                raise
            except Exception as e:
                g_failed += 1
                raise _TemporarySendError(f"generic send error: {e}")

        out["generic_sent"], out["generic_failed"] = g_sent, g_failed

    return out


def send_question_reply_webhooks(
    config: OutboundWebhookConfig, generic_payload: dict[str, Any]
) -> dict[str, Any]:
    """Send the 'question replied' webhook fanout.

    This reuses the same fanout implementation as question creation; the payload's
    `type` should be `keen.question.replied`.
    """

    return send_question_created_webhooks(config, generic_payload)
