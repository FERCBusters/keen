from __future__ import annotations

"""Google Workspace audit ingester.

This ingester polls the Admin SDK Reports API Activities feed.

Applications you likely care about:
  - admin: Admin audit events (user provisioning, settings changes, etc.)
  - login: Login audit events (success/failure, suspicious logins, etc.)

Each activity is stored as a JSON artifact and can be mapped to ISO27001:2022 controls via config/rules.yml.

Configuration:
  - Enable with KEEN_GOOGLE_WORKSPACE_ENABLED=true
  - Define streams in config/google_workspace.yml
  - Credentials are provided via env vars:
      GOOGLE_WORKSPACE_IMPERSONATE
      GOOGLE_WORKSPACE_SA_JSON_B64 (recommended) or GOOGLE_WORKSPACE_SA_JSON
      GOOGLE_WORKSPACE_SA_KEYFILE (path inside container)
"""

import base64
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.managed_configuration import load_document
from app.core.config import settings
from app.db.models import IngestionCursor
from app.ingest.common import fingerprint, store_event_with_artifact

SCOPES = ["https://www.googleapis.com/auth/admin.reports.audit.readonly"]


def load_google_workspace_config(path: str) -> dict[str, Any]:
    return load_document("google_workspace", path)


def _to_utc_naive(dt: datetime) -> datetime:
    """Convert to UTC naive (DB stores TIMESTAMP WITHOUT TIME ZONE)."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_rfc3339(ts: str | None) -> datetime | None:
    if not ts:
        return None
    s = str(ts).strip()
    if not s:
        return None
    try:
        # e.g. 2010-10-28T10:26:35.000Z
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return _to_utc_naive(dt)
    except Exception:
        return None


def _rfc3339(dt: datetime) -> str:
    dt2 = dt
    if dt2.tzinfo is None:
        dt2 = dt2.replace(tzinfo=timezone.utc)
    dt2 = dt2.astimezone(timezone.utc).replace(microsecond=0)
    return dt2.isoformat().replace("+00:00", "Z")


def _sanitize_hint(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", s)
    return s.strip("-")[:40]


def _cursor_name(stream: dict[str, Any]) -> str:
    hint = _sanitize_hint(str(stream.get("name") or ""))
    app = (stream.get("application") or "").strip().lower()
    user_key = (stream.get("user_key") or "all").strip().lower()
    event_names = stream.get("event_names") or []
    if not isinstance(event_names, list):
        event_names = []
    # stable hash for uniqueness even if hint collides
    payload = json.dumps(
        {
            "app": app,
            "user_key": user_key,
            "event_names": sorted(map(str, event_names)),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    h = hashlib.sha256(payload).hexdigest()[:12]
    if hint:
        return f"gws:{hint}:{h}"[:128]
    return f"gws:{app or 'activity'}:{h}"[:128]


def _load_credentials(impersonate: str) -> service_account.Credentials:
    """Build service-account creds for Reports API, using DWD impersonation."""

    if settings.google_workspace_sa_keyfile:
        creds = service_account.Credentials.from_service_account_file(
            settings.google_workspace_sa_keyfile, scopes=SCOPES
        )
    elif settings.google_workspace_sa_json_b64:
        raw = base64.b64decode(settings.google_workspace_sa_json_b64.encode("utf-8"))
        info = json.loads(raw.decode("utf-8"))
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
    elif settings.google_workspace_sa_json:
        info = json.loads(settings.google_workspace_sa_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
    else:
        raise RuntimeError(
            "Google Workspace credentials missing. Set one of "
            "GOOGLE_WORKSPACE_SA_JSON_B64 / GOOGLE_WORKSPACE_SA_JSON / GOOGLE_WORKSPACE_SA_KEYFILE"
        )

    if impersonate:
        creds = creds.with_subject(impersonate)
    return creds


def _reports_service(impersonate: str):
    creds = _load_credentials(impersonate)
    return build("admin", "reports_v1", credentials=creds, cache_discovery=False)


def _iter_activities(
    service,
    *,
    user_key: str,
    application: str,
    start_time: str,
    end_time: str | None,
    event_name: str | None,
    max_results: int,
) -> Iterable[dict[str, Any]]:
    req = service.activities().list(
        userKey=user_key,
        applicationName=application,
        startTime=start_time,
        maxResults=max_results,
        eventName=event_name,
        endTime=end_time,
    )
    while req is not None:
        resp = req.execute()
        for item in resp.get("items", []) or []:
            if isinstance(item, dict):
                yield item
        req = service.activities().list_next(req, resp)


def _event_names(activity: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for ev in activity.get("events") or []:
        if not isinstance(ev, dict):
            continue
        nm = (ev.get("name") or "").strip()
        if nm:
            out.append(nm)
    # de-dupe, keep stable order
    seen: set[str] = set()
    uniq: list[str] = []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def _event_params(activity: dict[str, Any]) -> dict[str, str]:
    """Flatten the first value per parameter name across all events."""
    params: dict[str, str] = {}
    for ev in activity.get("events") or []:
        if not isinstance(ev, dict):
            continue
        for p in ev.get("parameters") or []:
            if not isinstance(p, dict):
                continue
            name = (p.get("name") or "").strip()
            if not name or name in params:
                continue
            # Reports API can use "value" or "intValue"/"boolValue"
            if "value" in p and p.get("value") is not None:
                params[name] = str(p.get("value"))
            elif "intValue" in p and p.get("intValue") is not None:
                params[name] = str(p.get("intValue"))
            elif "boolValue" in p and p.get("boolValue") is not None:
                params[name] = str(p.get("boolValue"))
    return params


def _pick(params: dict[str, str], keys: list[str]) -> str:
    for k in keys:
        if k in params and str(params[k]).strip():
            return str(params[k]).strip()
    return ""


def _outcome_from_names(names: list[str]) -> str:
    joined = " ".join([n.lower() for n in names])
    if "fail" in joined or "denied" in joined or "reject" in joined:
        return "failure"
    if "success" in joined or "login_success" in joined or "logged_in" in joined:
        return "success"
    return "info"


def _summarize_activity(
    application: str,
    names: list[str],
    actor: str | None,
    target: str | None,
    ip: str | None,
) -> str:
    head = f"Google Workspace {application}".strip()
    if names:
        head = f"{head}: {', '.join(names[:3])}" + ("…" if len(names) > 3 else "")
    bits = [head]
    if target:
        bits.append(f"target={target}")
    if actor:
        bits.append(f"by {actor}")
    if ip:
        bits.append(f"ip={ip}")
    s = " ".join(bits)
    return " ".join(s.split())[:240]


def _external_id(
    cursor_name: str, application: str, activity: dict[str, Any], payload: bytes
) -> str:
    aid = activity.get("id") or {}
    uq = (aid.get("uniqueQualifier") or "").strip() if isinstance(aid, dict) else ""
    if uq:
        return hashlib.sha256(f"{application}|{uq}".encode("utf-8")).hexdigest()[:32]
    # fallback to content-based fingerprint
    return fingerprint([cursor_name, application], payload)


def ingest_google_workspace_stream(
    db: Session, service, cfg: dict[str, Any], defaults: dict[str, Any]
) -> dict[str, Any]:
    if cfg.get("enabled") is False:
        return {"skipped": True, "reason": "stream disabled", "name": cfg.get("name")}

    application = (cfg.get("application") or "").strip()
    if not application:
        return {"ok": False, "error": "missing application", "name": cfg.get("name")}

    name = (cfg.get("name") or application).strip()
    user_key = (cfg.get("user_key") or "all").strip()
    system = (cfg.get("system") or "google-workspace").strip()

    since_minutes = int(
        cfg.get("since_minutes") or defaults.get("since_minutes") or 120
    )
    overlap_seconds = int(
        cfg.get("overlap_seconds") or defaults.get("overlap_seconds") or 180
    )
    max_items = int(cfg.get("max_items") or defaults.get("max_items") or 2000)

    # event_names can be omitted to ingest everything
    event_names = cfg.get("event_names") or []
    if not isinstance(event_names, list):
        event_names = []
    event_names = [str(x).strip() for x in event_names if str(x).strip()]

    max_results = int(cfg.get("max_results") or 1000)
    max_results = max(1, min(max_results, 1000))  # API max

    cursor_name = _cursor_name({**cfg, "name": name})
    cur = (
        db.query(IngestionCursor)
        .filter(IngestionCursor.name == cursor_name)
        .one_or_none()
    )
    if cur is None:
        cur = IngestionCursor(
            name=cursor_name,
            last_ts=None,
            meta={
                "name": name,
                "application": application,
                "user_key": user_key,
                "system": system,
                "event_names": event_names,
            },
        )
        db.add(cur)
        db.flush()

    cursor_last = cur.last_ts
    if cursor_last and cursor_last.tzinfo is not None:
        cursor_last = cursor_last.replace(tzinfo=None)

    if cursor_last:
        start_dt = cursor_last.replace(tzinfo=timezone.utc) - timedelta(
            seconds=overlap_seconds
        )
    else:
        start_dt = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

    start_time = _rfc3339(start_dt)

    # Collect items (then sort) for cursor stability.
    items: list[dict[str, Any]] = []

    def _collect(event_name: str | None) -> None:
        for a in _iter_activities(
            service,
            user_key=user_key,
            application=application,
            start_time=start_time,
            end_time=None,
            event_name=event_name,
            max_results=max_results,
        ):
            items.append(a)
            if max_items and len(items) >= max_items:
                break

    try:
        if event_names:
            for en in event_names:
                _collect(en)
        else:
            _collect(None)
    except HttpError as e:
        # Keep error string compact but useful
        return {
            "ok": False,
            "name": name,
            "application": application,
            "error": f"Google API error: {getattr(e, 'status_code', '')} {str(e)}",
        }

    def _ts(a: dict[str, Any]) -> datetime:
        t = None
        if isinstance(a.get("id"), dict):
            t = a.get("id", {}).get("time")
        dt = _parse_rfc3339(t)
        if dt is None:
            dt = _to_utc_naive(datetime.now(timezone.utc))
        return dt

    # If we hit the cap, keep the newest max_items by timestamp.
    if max_items and len(items) > max_items:
        items = sorted(items, key=_ts)[-max_items:]

    items_sorted = sorted(items, key=_ts)

    # Overlap floor for cursor filtering
    floor_ts: datetime | None = None
    if cursor_last:
        floor_ts = cursor_last - timedelta(seconds=overlap_seconds)

    created = 0
    newest_ts: datetime | None = cursor_last

    for a in items_sorted:
        ts = _ts(a)
        if floor_ts and ts < floor_ts:
            continue

        actor = None
        if isinstance(a.get("actor"), dict):
            actor = (
                a.get("actor", {}).get("email")
                or a.get("actor", {}).get("profileId")
                or ""
            ).strip() or None

        ip = (a.get("ipAddress") or "").strip() or None

        names = _event_names(a)
        params = _event_params(a)

        # Heuristics for a primary "target" (useful for user management/login)
        target = (
            _pick(
                params,
                [
                    "USER_EMAIL",
                    "TARGET_USER",
                    "TARGET_USER_EMAIL",
                    "EMAIL",
                    "OLD_VALUE",
                    "NEW_VALUE",
                    "GROUP_EMAIL",
                ],
            )
            or None
        )

        outcome = (cfg.get("outcome") or "").strip() or _outcome_from_names(names)

        if "severity" in cfg and cfg.get("severity") is not None:
            try:
                severity = int(cfg.get("severity"))
            except Exception:
                severity = 3
        else:
            severity = 6 if outcome == "failure" else 3

        action = "+".join(names[:3]) if names else application

        summary = _summarize_activity(application, names, actor, target, ip)

        payload = {
            "stream": name,
            "application": application,
            "activity": a,
            "parsed": {
                "event_names": names,
                "params": params,
            },
            "fetched_at": _to_utc_naive(datetime.now(timezone.utc)).isoformat(),
        }
        payload_bytes = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

        ext_id = _external_id(cursor_name, application, a, payload_bytes)

        key = f"google_workspace/{_sanitize_hint(name) or application}/{ts.date().isoformat()}/{ext_id}.json"

        res = store_event_with_artifact(
            db,
            timestamp=ts,
            source="google_workspace",
            system=system,
            actor=actor,
            action=action,
            outcome=outcome,
            severity=severity,
            summary=summary,
            raw_pointer={
                "google_workspace": {
                    "stream": name,
                    "application": application,
                    "uniqueQualifier": (
                        (a.get("id", {}) or {}).get("uniqueQualifier")
                        if isinstance(a.get("id"), dict)
                        else None
                    ),
                    "event_names": names,
                    "actor": actor,
                    "ip": ip,
                }
            },
            normalized_payload={"google_workspace": payload},
            external_id=ext_id,
            artifact_kind="google_workspace_activity",
            artifact_bytes=payload_bytes,
            artifact_content_type="application/json",
            artifact_key=key,
            captured_by="keen:google_workspace",
        )
        if not res.get("deduped"):
            created += 1

        if newest_ts is None or ts > newest_ts:
            newest_ts = ts

    if newest_ts:
        cur.last_ts = newest_ts
    cur.updated_at = datetime.utcnow()
    db.add(cur)
    db.commit()

    return {
        "ok": True,
        "name": name,
        "application": application,
        "system": system,
        "created_events": created,
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
        "start_time": start_time,
        "fetched_items": len(items_sorted),
    }


def ingest_google_workspace_all(db: Session) -> list[dict[str, Any]]:
    if not settings.google_workspace_enabled:
        return [{"skipped": True, "reason": "google_workspace_enabled=false"}]

    cfg = load_google_workspace_config(settings.google_workspace_config_path)
    if cfg.get("enabled") is False:
        return [{"skipped": True, "reason": "config enabled=false"}]

    impersonate = (cfg.get("impersonate") or "").strip() or (
        settings.google_workspace_impersonate or ""
    ).strip()
    if not impersonate:
        return [
            {
                "ok": False,
                "error": "Missing impersonation email. Set GOOGLE_WORKSPACE_IMPERSONATE or config: impersonate",
            }
        ]

    defaults = {
        "since_minutes": cfg.get("since_minutes"),
        "overlap_seconds": cfg.get("overlap_seconds"),
        "max_items": cfg.get("max_items"),
    }

    streams = cfg.get("streams") or []
    if not isinstance(streams, list) or not streams:
        return [{"ok": True, "created_events": 0, "note": "no streams configured"}]

    out: list[dict[str, Any]] = []

    service = _reports_service(impersonate)
    for s in streams:
        if not isinstance(s, dict):
            continue
        if s.get("enabled") is False:
            out.append(
                {"skipped": True, "reason": "stream disabled", "name": s.get("name")}
            )
            continue
        try:
            out.append(ingest_google_workspace_stream(db, service, s, defaults))
        except Exception as e:
            db.rollback()
            out.append({"ok": False, "name": s.get("name"), "error": str(e)})

    return out
