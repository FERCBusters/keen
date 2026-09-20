from __future__ import annotations

from datetime import datetime
from typing import Any
import json

import httpx
import yaml
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.db.models import IngestionCursor
from app.db.models import utcnow
from app.ingest.common import fingerprint, store_event_with_artifact, is_safe_url


def _to_utc_naive(dt: datetime | None) -> datetime | None:
    """Normalize timestamps for comparisons.

    Keen stores timestamps in Postgres as naive UTC datetimes.
    If we ever receive tz-aware datetimes, strip tzinfo to avoid
    "can't compare offset-naive and offset-aware" errors.
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=None)


def _get_or_create_cursor(db: Session, name: str) -> IngestionCursor:
    """Concurrency-safe cursor fetch/create.

    Multiple ingestion runs can overlap (manual trigger + scheduler, multiple
    API workers, etc.). Use an upsert so we never crash on unique violations.
    """
    cur = db.query(IngestionCursor).filter(IngestionCursor.name == name).one_or_none()
    if cur is not None:
        return cur

    stmt = (
        insert(IngestionCursor.__table__)
        .values(name=name, last_ts=None, metadata={}, updated_at=utcnow())
        .on_conflict_do_nothing(index_elements=[IngestionCursor.__table__.c.name])
    )
    db.execute(stmt)
    db.flush()
    cur = db.query(IngestionCursor).filter(IngestionCursor.name == name).one()
    return cur


def load_jenkins_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _client() -> httpx.Client:
    # Jenkins Remote Access API exposes JSON under .../api/json
    # Validate the base URL to prevent SSRF attacks
    base_url = settings.jenkins_base_url.rstrip("/")
    if not is_safe_url(base_url):
        raise ValueError("Invalid or unsafe Jenkins base URL")

    auth = None
    if settings.jenkins_username and settings.jenkins_api_token:
        auth = (settings.jenkins_username, settings.jenkins_api_token)

    return httpx.Client(
        base_url=base_url,
        auth=auth,
        timeout=30.0,
        verify=True,
    )


def _job_api_path(job_name: str) -> str:
    # Jenkins jobs may be nested; this helper is for simple top-level jobs.
    return f"/job/{job_name}/api/json"


def ingest_jenkins_job(
    db: Session, job_name: str, label: str | None, kind: str | None
) -> dict[str, Any]:
    cursor_name = f"jenkins:{job_name}"
    cur = _get_or_create_cursor(db, cursor_name)

    tree = "builds[number,url,timestamp,result,duration,building],lastBuild[number,url,timestamp,result]"
    with _client() as c:
        r = c.get(_job_api_path(job_name), params={"tree": tree})
        r.raise_for_status()
        job = r.json() or {}

    builds = job.get("builds", []) or []
    created = 0
    newest_ts = _to_utc_naive(cur.last_ts)

    # builds are newest-first; process oldest-first
    for b in reversed(builds[:50]):
        ts_ms = b.get("timestamp")
        if not ts_ms:
            continue
        # Jenkins gives epoch milliseconds. Store as naive UTC to match DB.
        ts = datetime.utcfromtimestamp(ts_ms / 1000.0)
        last_ts = _to_utc_naive(cur.last_ts)
        if last_ts and ts <= last_ts:
            continue

        result = b.get("result") or ("RUNNING" if b.get("building") else None)
        outcome = (
            "success"
            if result == "SUCCESS"
            else ("failed" if result in ("FAILURE", "ABORTED", "UNSTABLE") else "info")
        )

        summary = f"jenkins {job_name} build #{b.get('number')} {result}"
        if label:
            summary = f"[{label}] {summary}"

        payload_bytes = json.dumps(
            {"job": job_name, "build": b}, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        ext_id = fingerprint(
            [cursor_name, str(b.get("number")), result or ""], payload_bytes
        )

        key = f"jenkins/{job_name}/{ts.date().isoformat()}/{ext_id}.json"
        res = store_event_with_artifact(
            db,
            timestamp=ts,
            source="jenkins",
            # Use the caller-provided label (prod/dev/qa/etc.) as the "system"
            # so rules.yml can map evidence by environment.
            system=label or "jenkins",
            actor=None,
            action=kind or "build",
            outcome=outcome,
            severity=4 if outcome == "failed" else 3,
            summary=summary,
            raw_pointer={
                "jenkins": {
                    "job": job_name,
                    "build_number": b.get("number"),
                    "url": b.get("url"),
                }
            },
            normalized_payload={
                "job": job_name,
                "build": b,
                "label": label,
                "kind": kind,
            },
            external_id=ext_id,
            artifact_kind="jenkins_build",
            artifact_bytes=payload_bytes,
            artifact_content_type="application/json",
            artifact_key=key,
            captured_by="keen:jenkins",
        )
        if not res.get("deduped"):
            created += 1

        if (newest_ts is None) or (ts > newest_ts):
            newest_ts = ts

    cur.last_ts = newest_ts or cur.last_ts
    cur.updated_at = utcnow()
    db.add(cur)
    db.commit()

    return {
        "job": job_name,
        "created_events": created,
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
    }


def ingest_jenkins_all(db: Session) -> list[dict[str, Any]]:
    if not settings.jenkins_enabled:
        return [{"skipped": True, "reason": "KEEN_JENKINS_ENABLED=false"}]
    cfg = load_jenkins_config(settings.jenkins_config_path)
    out = []
    for j in cfg.get("jobs", []) or []:
        try:
            out.append(ingest_jenkins_job(db, j["name"], j.get("label"), j.get("kind")))
        except Exception as e:
            db.rollback()
            out.append({"job": j.get("name"), "error": str(e)})
    return out
