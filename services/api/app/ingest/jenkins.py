from __future__ import annotations

import json
from typing import Any, cast

import httpx2 as httpx
from mosp.core.datetime_utils import to_utc_naive, utc_from_timestamp_naive
from mosp.security.errors import collection_error
from mosp.security.json_shape import decode_json
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
from app.ingest.http import retained_listing
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


PAGE = 50
MAX_PAGES = 20
SCAN = 1000
MAX_SCANS = 100
# Builds seen running that are followed until they finish.
MAX_PENDING = 100
BUILD_FIELDS = "number,url,timestamp,result,duration,building"


def _number(build: Any) -> int | None:
    number = build.get("number") if isinstance(build, dict) else None
    return number if type(number) is int else None


def _build_time(build: Any):
    """A build's start as naive UTC, or None when it has no timestamp."""
    ts_ms = build.get("timestamp") if isinstance(build, dict) else None
    if not ts_ms:
        return None
    if isinstance(ts_ms, bool) or not isinstance(ts_ms, (int, float)):
        raise TypeError("Jenkins build timestamp is not a number")
    # Jenkins gives epoch milliseconds. Store as naive UTC to match DB.
    return utc_from_timestamp_naive(ts_ms / 1000.0)


def _builds(c: httpx.Client, job_name: str, fields: str, start: int, end: int):
    """Builds ``start`` to ``end`` (exclusive) of the job, counted from the newest.

    Jenkins lists only the newest 100 builds as ``builds``; ``allBuilds`` with
    a range reaches all of them.
    """
    r = c.get(
        _job_api_path(job_name),
        params={"tree": f"allBuilds[{fields}]{{{start},{end}}}"},
    )
    r.raise_for_status()
    job = decode_json(r.content) or {}
    # A response or build listing of another shape holds no builds.
    items = job.get("allBuilds") if isinstance(job, dict) else None
    return items if isinstance(items, list) else []


def _listing(c: httpx.Client, job_name: str, reached, extra: bool = False) -> list[Any]:
    """Newest first, the job's builds down to the first page holding a build
    ``reached`` accepts (one already collected), or the start of its history,
    and with ``extra`` one page more.

    With no ``reached`` (a cursor that has collected nothing yet) only the
    newest page is read, so a first run does not fetch a job's whole history.
    A backlog longer than MAX_PAGES pages is read oldest part first: the
    collected builds are found with listings of numbers alone, and this run
    reads the MAX_PAGES pages just above them, so the next run continues from
    there and no build is passed over. Builds started meanwhile only move
    later pages down, which repeats a build rather than skipping one.
    """
    builds: list[Any] = []
    for page in range(MAX_PAGES):
        items = _builds(c, job_name, BUILD_FIELDS, page * PAGE, (page + 1) * PAGE)
        builds.extend(items)
        if reached is None or len(items) < PAGE:
            return builds
        if any(map(reached, items)):
            if extra:
                start = (page + 1) * PAGE
                builds.extend(_builds(c, job_name, BUILD_FIELDS, start, start + PAGE))
            return builds

    for scan in range(MAX_SCANS):
        start = MAX_PAGES * PAGE + scan * SCAN
        items = _builds(c, job_name, "number,timestamp", start, start + SCAN)
        hits = [i for i, b in enumerate(items) if reached(b)]
        if hits or len(items) < SCAN:
            position = start + (hits[0] if hits else len(items))
            break
    else:
        raise ValueError(
            "Jenkins job has more builds to collect than Keen reads; "
            "the cursor was not advanced"
        )

    builds = []
    start = position - MAX_PAGES * PAGE
    for _ in range(2 * MAX_PAGES):
        items = _builds(c, job_name, BUILD_FIELDS, start, start + PAGE)
        builds.extend(items)
        if len(items) < PAGE:
            return builds
        start += PAGE
        if any(map(reached, items)):
            if extra:
                builds.extend(_builds(c, job_name, BUILD_FIELDS, start, start + PAGE))
            return builds
    raise ValueError(
        "Jenkins builds kept arriving faster than Keen reads them; "
        "the cursor was not advanced"
    )


def ingest_jenkins_job(
    db: Session, job_name: str, label: str | None, kind: str | None
) -> dict[str, Any]:
    """Collect a job's builds that are new since the last run, and the final
    result of each build that was still running then.

    The cursor keeps, in its metadata, the highest build number collected
    (``last_number``) and the builds last seen running (``pending``), each of
    which is read by itself until it finishes. Build start times make no
    cursor: a build that finishes after a run started before that run's
    newest build, and would never be read again.
    """
    cursor_name = f"jenkins:{job_name}"
    cur = _get_or_create_cursor(db, cursor_name)
    db.commit()  # No database transaction spans the remote wait.

    meta = dict(cur.meta) if isinstance(cur.meta, dict) else {}
    last_done = meta.get("last_number")
    last_done = last_done if type(last_done) is int else None
    pending_meta = meta.get("pending")
    pending = {
        n
        for n in (pending_meta if isinstance(pending_meta, list) else [])
        if type(n) is int
    }
    cursor_last = to_utc_naive(cur.last_ts) if cur.last_ts else None

    def reached(b: Any) -> bool:
        number = _number(b)
        if number is None:
            return False
        if last_done is not None:
            return number <= last_done
        # A cursor from before build numbers were kept: its time marks what
        # it collected.
        try:
            ts = _build_time(b)
        except TypeError:
            return False
        return bool(cursor_last and ts and ts <= cursor_last)

    legacy = last_done is None and cursor_last is not None
    with _client() as c:
        # A cursor kept by time read the newest 50 builds each run, so a
        # build it stored running can lie a page below the first it reaches.
        builds = _listing(
            c,
            job_name,
            reached if last_done is not None or cursor_last else None,
            extra=legacy,
        )
        numbers = [n for n in map(_number, builds) if n is not None]
        if last_done is not None and numbers and max(numbers) < last_done:
            # The job was deleted and created again under its name, and counts
            # from 1 once more, or its newest builds were deleted: it is read
            # as at a first run, and builds seen running are still followed.
            last_done = None
            builds = _builds(c, job_name, BUILD_FIELDS, 0, PAGE)
            numbers = [n for n in map(_number, builds) if n is not None]
        # A build still running is read by itself, however far back it is.
        listed = set(numbers)
        # Together these hold no more than one response may.
        with retained_listing():
            for number in sorted(pending - listed)[:MAX_PENDING]:
                r = c.get(
                    f"{_job_api_path(job_name).removesuffix('/api/json')}"
                    f"/{number}/api/json",
                    params={"tree": BUILD_FIELDS},
                )
                if r.status_code == 404:
                    continue  # Discarded in Jenkins.
                r.raise_for_status()
                build = decode_json(r.content)
                if _number(build) == number:
                    builds.append(build)

    def wanted(b: Any) -> bool:
        number = _number(b)
        if number is None:
            return False
        if number in pending or legacy:
            # A build seen running is read again once it has finished. A
            # cursor kept by time stored running builds as running, so its
            # first run here reads every finished build it lists again;
            # those already stored are recognised.
            return not b.get("building") or not reached(b)
        if last_done is not None:
            return number > last_done
        return True

    created = 0
    newest_ts = cursor_last

    def read(b: dict[str, Any]) -> dict[str, Any] | None:
        ts = _build_time(b)
        if ts is None:
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
        # The result is part of the identity, so a build's final result is
        # stored beside the RUNNING event an earlier run stored for it.
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
    selected = [b for b in builds if reader.read(wanted, b)]
    # builds are newest-first; process oldest-first
    for event in reader.each(reversed(selected), read):
        res = store_event_with_artifact(db, **event)
        if not res.get("deduped"):
            created += 1

        if (newest_ts is None) or (event["timestamp"] > newest_ts):
            newest_ts = event["timestamp"]

    selected_numbers = {_number(b) for b in selected}
    # A pending build missing from the listing was discarded in Jenkins.
    running = sorted(
        {
            number
            for b in builds
            if isinstance(b, dict) and b.get("building")
            for number in [_number(b)]
            if number is not None
            and (number in pending or number in selected_numbers or legacy)
        }
    )
    meta["pending"] = running[-MAX_PENDING:]
    unfollowed = len(running) - len(meta["pending"])
    # A job with no builds yet starts from 0, so every build it runs is new.
    meta["last_number"] = max([*numbers, last_done or 0])
    cur.meta = meta
    cur.last_ts = newest_ts or cur.last_ts
    cur.updated_at = utcnow()
    db.add(cur)
    db.commit()

    return {
        "job": job_name,
        "created_events": created,
        "invalid_records": reader.invalid,
        "cursor_last_ts": cur.last_ts.isoformat() if cur.last_ts else None,
        **(
            {
                "error": f"{unfollowed} running builds beyond the {MAX_PENDING} "
                "Keen follows will not have their final result collected"
            }
            if unfollowed
            else {}
        ),
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
