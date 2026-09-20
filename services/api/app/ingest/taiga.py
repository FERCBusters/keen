from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
import json
import re

import httpx
import yaml
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import IngestionCursor
from app.ingest.common import fingerprint, store_event_with_artifact, is_safe_url

# Simple in-process auth token cache (per worker/api process)
_AUTH_TOKEN: Optional[str] = None


def load_taiga_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _base_client(headers: Optional[dict[str, str]] = None) -> httpx.Client:
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    # Validate the base URL to prevent SSRF attacks
    base_url = settings.taiga_base_url.rstrip("/")
    if not is_safe_url(base_url):
        raise ValueError("Invalid or unsafe Taiga base URL")
    return httpx.Client(
        base_url=base_url,
        headers=h,
        timeout=30.0,
        verify=True,
    )


def _login_and_get_token() -> str:
    """Return a Taiga auth token.

    Priority:
      1) KEEN_TAIGA_TOKEN (if set)
      2) cached token obtained via /api/v1/auth using KEEN_TAIGA_USERNAME/PASSWORD

    Taiga auth endpoint expects: {type: "normal", username, password}
    and returns an "auth_token" field.
    """
    global _AUTH_TOKEN

    if settings.taiga_token:
        return settings.taiga_token

    if _AUTH_TOKEN:
        return _AUTH_TOKEN

    if not settings.taiga_username or not settings.taiga_password:
        raise RuntimeError(
            "Taiga auth required but KEEN_TAIGA_USERNAME/KEEN_TAIGA_PASSWORD are not set "
            "(or set KEEN_TAIGA_TOKEN to use a pre-generated token)."
        )

    client = _base_client()
    try:
        resp = client.post(
            "/api/v1/auth",
            json={
                "type": "normal",
                "username": settings.taiga_username,
                "password": settings.taiga_password,
            },
        )
        resp.raise_for_status()
        data = resp.json() or {}
        token = data.get("auth_token") or data.get("token") or data.get("access_token")
        if not token:
            raise RuntimeError(
                f"Taiga /api/v1/auth response missing auth_token field: keys={list(data.keys())}"
            )
        _AUTH_TOKEN = str(token)
        return _AUTH_TOKEN
    finally:
        client.close()


def _taiga_client() -> httpx.Client:
    token = _login_and_get_token()
    return _base_client(headers={"Authorization": f"Bearer {token}"})


_WS_RE = re.compile(r"\s+")


def _clean_snippet(s: str, limit: int = 160) -> str:
    s2 = _WS_RE.sub(" ", (s or "").strip())
    if len(s2) <= limit:
        return s2
    return s2[: limit - 1] + "…"


def _extract_object(
    data: dict[str, Any],
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    # Order matters: userstory is by far the most common in your examples
    for k in (
        "userstory",
        "task",
        "issue",
        "epic",
        "milestone",
        "wiki_page",
        "wikipage",
        "attachment",
    ):
        v = data.get(k)
        if isinstance(v, dict):
            return k, v
    return None, None


def _taiga_ui_url(base_url: str, data: dict[str, Any]) -> str | None:
    """Best-effort link to the Taiga UI object.

    Taiga's UI URLs are commonly:
      - User story: /project/<project-slug>/us/<ref>
      - Task:      /project/<project-slug>/task/<ref>
      - Issue:     /project/<project-slug>/issue/<ref>
      - Epic:      /project/<project-slug>/epic/<ref>

    If your Taiga instance uses a different URL scheme, you can still use
    the raw_pointer data to construct links externally.
    """
    base = (base_url or "").rstrip("/")
    if not base:
        return None

    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    slug = project.get("slug")
    if not slug:
        return None

    obj_type, obj = _extract_object(data)
    if not obj_type or not isinstance(obj, dict):
        return None

    ref = obj.get("ref") or obj.get("id")
    if not ref:
        return None

    seg = {
        "userstory": "us",
        "task": "task",
        "issue": "issue",
        "epic": "epic",
    }.get(obj_type)
    if not seg:
        return None

    return f"{base}/project/{slug}/{seg}/{ref}"


def _build_summary(
    entry: dict[str, Any], project_id: int, label: Optional[str]
) -> tuple[str, Optional[str], str, dict[str, Any]]:
    """Return (summary, actor, action, extracted_fields)."""
    etype = entry.get("event_type") or entry.get("type") or "timeline"
    data = entry.get("data") or {}

    # actor
    actor = None
    u = data.get("user")
    if isinstance(u, dict):
        actor = u.get("username") or u.get("name")

    # project
    project_name = None
    p = data.get("project")
    if isinstance(p, dict):
        project_name = p.get("name")

    obj_type, obj = _extract_object(data)
    obj_ref = None
    obj_subject = None
    if isinstance(obj, dict):
        obj_ref = obj.get("ref") or obj.get("id")
        obj_subject = obj.get("subject") or obj.get("name") or obj.get("title")

    comment = data.get("comment") if isinstance(data.get("comment"), str) else None

    # Human readable summary
    prefix = label or project_name or f"taiga project {project_id}"

    parts = [f"[{prefix}]", str(etype)]

    if obj_type:
        parts.append(str(obj_type))
    if obj_ref is not None:
        parts.append(f"#{obj_ref}")
    if obj_subject:
        parts.append(_clean_snippet(str(obj_subject), limit=120))

    if comment:
        parts.append("—")
        parts.append(_clean_snippet(comment, limit=180))

    if actor:
        parts.append(f"(by {actor})")

    summary = " ".join([p for p in parts if p])

    extracted = {
        "taiga_entry_id": entry.get("id"),
        "event_type": etype,
        "project_id": project_id,
        "project_name": project_name,
        "namespace": entry.get("namespace"),
        "object_type": obj_type,
        "object_ref": obj_ref,
        "object_subject": obj_subject,
        "comment": comment,
        "actor": actor,
    }

    action = f"taiga:{etype}"
    return summary, actor, action, extracted


def ingest_taiga_project_timeline(
    db: Session, project_id: int, label: str | None
) -> dict[str, Any]:
    cursor_name = f"taiga:project:{project_id}"
    cur = (
        db.query(IngestionCursor)
        .filter(IngestionCursor.name == cursor_name)
        .one_or_none()
    )
    if cur is None:
        cur = IngestionCursor(name=cursor_name, last_ts=None, meta={})
        db.add(cur)
        db.flush()

    client = _taiga_client()
    try:
        r = client.get(f"/api/v1/timeline/project/{project_id}")
        # If token expired / permission changed, refresh token once and retry.
        if r.status_code in (401, 403) and not settings.taiga_token:
            global _AUTH_TOKEN
            _AUTH_TOKEN = None
            client.close()
            client = _taiga_client()
            r = client.get(f"/api/v1/timeline/project/{project_id}")
        r.raise_for_status()
        entries = r.json() or []
    finally:
        try:
            client.close()
        except Exception:
            pass

    created = 0
    newest_ts = cur.last_ts

    # Entries include a created field; treat as ISO string where available.
    # We normalize to UTC naive (to match DB cursor storage).
    def parse_ts(e: dict) -> datetime | None:
        for k in ("created", "created_at", "date"):
            if k in e and e[k]:
                try:
                    return (
                        datetime.fromisoformat(str(e[k]).replace("Z", "+00:00"))
                        .astimezone(timezone.utc)
                        .replace(tzinfo=None)
                    )
                except Exception:
                    pass
        return None

    parsed_entries = [(parse_ts(e), e) for e in entries]
    parsed_entries = [(t, e) for (t, e) in parsed_entries if t is not None]
    parsed_entries.sort(key=lambda x: x[0])

    for ts, e in parsed_entries:
        if cur.last_ts and ts <= cur.last_ts:
            continue

        summary, actor, action, extracted = _build_summary(e, project_id, label)

        # Use Taiga's timeline entry id when present for strong dedupe.
        entry_id = e.get("id")
        if entry_id is not None:
            ext_id = f"taiga:{project_id}:{entry_id}"
        else:
            payload_bytes = json.dumps(e, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            ext_id = fingerprint([cursor_name, action], payload_bytes)

        payload_bytes = json.dumps(e, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )

        ui_url = _taiga_ui_url(settings.taiga_base_url, (e.get("data") or {}))

        key = f"taiga/project/{project_id}/{ts.date().isoformat()}/{ext_id}.json"
        res = store_event_with_artifact(
            db,
            timestamp=ts,
            source="taiga",
            system=label or extracted.get("project_name") or f"project:{project_id}",
            actor=actor,
            action=action,
            outcome="info",
            severity=3,
            summary=summary,
            raw_pointer={
                "taiga": {
                    "project_id": project_id,
                    "timeline_entry_id": entry_id,
                    "event_type": extracted.get("event_type"),
                    "url": ui_url,
                }
            },
            normalized_payload={"entry": e, "extracted": extracted},
            external_id=ext_id,
            artifact_kind="taiga_timeline",
            artifact_bytes=payload_bytes,
            artifact_content_type="application/json",
            artifact_key=key,
            captured_by="keen:taiga",
        )
        if not res.get("deduped"):
            created += 1

        if (newest_ts is None) or (ts > newest_ts):
            newest_ts = ts

    cur.last_ts = newest_ts or cur.last_ts
    # updated_at is stored as a naive UTC timestamp in DB.
    cur.updated_at = datetime.utcnow()
    db.add(cur)
    db.commit()

    return {
        "project_id": project_id,
        "created_events": created,
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
    }


def ingest_taiga_all(db: Session) -> list[dict[str, Any]]:
    if not settings.taiga_enabled:
        return [{"skipped": True, "reason": "KEEN_TAIGA_ENABLED=false"}]
    cfg = load_taiga_config(settings.taiga_config_path)
    out = []
    for p in cfg.get("projects", []) or []:
        try:
            out.append(ingest_taiga_project_timeline(db, int(p["id"]), p.get("label")))
        except Exception as e:
            db.rollback()
            out.append({"project_id": p.get("id"), "error": str(e)})
    return out
