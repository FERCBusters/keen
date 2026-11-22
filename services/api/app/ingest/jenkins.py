from __future__ import annotations

import json
from typing import Any, cast

import httpx2 as httpx
from myf.core.datetime_utils import to_utc_naive, utc_from_timestamp_naive
from myf.security.errors import collection_error
from myf.security.json_shape import decode_json
from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import IngestionCursor, utcnow
from app.ingest.common import (
    RecordReader,
    fingerprint,
    is_safe_url,
    store_event_with_artifact,
)
from app.ingest.connections import connection_runs, load_document, namespace, settings
from app.ingest.http import client as ingestion_client
from app.ingest.registry import HTTP_QUEUE, Source, register
from app.services.ingestion_pause import pausable


def _get_or_create_cursor(db: Session, name: str) -> IngestionCursor:
    """Concurrency-safe cursor fetch/create.

    Multiple ingestion runs can overlap (manual trigger + scheduler, multiple
    API workers, etc.). Use an upsert so we never crash on unique violations.
    """
    name = namespace(name)
    cur = db.query(IngestionCursor).filter(IngestionCursor.name == name).one_or_none()
    if cur is not None:
        return cur

    stmt = (
        # Declarative mapping types __table__ as FromClause; this one is a Table.
        insert(cast(Table, IngestionCursor.__table__))
        .values(name=name, last_ts=None, metadata={}, updated_at=utcnow())
        .on_conflict_do_nothing(index_elements=[IngestionCursor.__table__.c.name])
    )
    db.execute(stmt)
    db.flush()
    cur = db.query(IngestionCursor).filter(IngestionCursor.name == name).one()
    return cur


def load_jenkins_config(path: str) -> dict[str, Any]:
    return load_document("jenkins", path)


def _client() -> httpx.Client:
    # Jenkins Remote Access API exposes JSON under .../api/json
    # Validate the base URL to prevent SSRF attacks
    base_url = settings.jenkins_base_url.rstrip("/")
    if not is_safe_url(base_url):
        raise ValueError("Invalid or unsafe Jenkins base URL")

    auth = None
    if settings.jenkins_username and settings.jenkins_api_token:
        auth = (settings.jenkins_username, settings.jenkins_api_token)

    return ingestion_client(
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
    db.commit()  # No database transaction spans the remote wait.

    tree = "builds[number,url,timestamp,result,duration,building],lastBuild[number,url,timestamp,result]"
    with _client() as c:
        r = c.get(_job_api_path(job_name), params={"tree": tree})
        r.raise_for_status()
        job = decode_json(r.content) or {}

    # A response or build listing of another shape holds no builds, and a
    # build other than an object is skipped.
    builds = job.get("builds") if isinstance(job, dict) else None
    builds = builds if isinstance(builds, list) else []
    created = 0
    cursor_last = to_utc_naive(cur.last_ts) if cur.last_ts else None
    newest_ts = cursor_last

    def read(b: Any) -> dict[str, Any] | None:
        if not isinstance(b, dict):
            return None
        ts_ms = b.get("timestamp")
        if not ts_ms:
            return None
        if isinstance(ts_ms, bool) or not isinstance(ts_ms, (int, float)):
            raise TypeError("Jenkins build timestamp is not a number")
        # Jenkins gives epoch milliseconds. Store as naive UTC to match DB.
        ts = utc_from_timestamp_naive(ts_ms / 1000.0)
        if cursor_last and ts <= cursor_last:
            return None

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
            [cursor_name, str(b.get("number")), str(result or "")], payload_bytes
        )
        return dict(
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
            artifact_key=f"jenkins/{job_name}/{ts.date().isoformat()}/{ext_id}.json",
            captured_by="keen:jenkins",
        )

    reader = RecordReader()
    # builds are newest-first; process oldest-first
    for event in reader.each(reversed(builds[:50]), read):
        res = store_event_with_artifact(db, **event)
        if not res.get("deduped"):
            created += 1

        if (newest_ts is None) or (event["timestamp"] > newest_ts):
            newest_ts = event["timestamp"]

    cur.last_ts = newest_ts or cur.last_ts
    cur.updated_at = utcnow()
    db.add(cur)
    db.commit()

    return {
        "job": job_name,
        "created_events": created,
        "invalid_records": reader.invalid,
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
    }


@pausable("jenkins")
@connection_runs("jenkins")
def ingest_jenkins_all(db: Session) -> list[dict[str, Any]]:
    cfg = load_jenkins_config(settings.jenkins_config_path)
    out = []
    for j in cfg.get("jobs", []) or []:
        try:
            out.append(ingest_jenkins_job(db, j["name"], j.get("label"), j.get("kind")))
        except Exception as e:
            db.rollback()
            out.append({"job": j.get("name"), "error": collection_error(e)})
    return out


register(
    Source(
        name="jenkins",
        module=__name__,
        queue=HTTP_QUEUE,
        schedule=4500.0,
        config_setting="jenkins_config_path",
        public_fields=("base_url", "username"),
        secret_fields=("api_token",),
        input_sections=("jobs",),
        section="jobs",
        id_field="name",
        definition_sections=("jobs",),
        key_field="name",
    )
)
