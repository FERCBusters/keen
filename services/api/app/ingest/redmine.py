"""Read-only Redmine issues and journals, using the standard REST JSON API."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

import httpx2 as httpx

from app.db.models import IngestionCursor
from app.ingest.common import RecordReader, is_safe_url, store_event_with_artifact
from app.ingest.connections import (
    advisory_lock,
    connection_runs,
    load_document,
    namespace,
    settings,
)
from app.ingest.http import client as ingestion_client
from app.ingest.registry import HTTP_QUEUE, Source, register
from app.security.errors import collection_error
from app.security.json_shape import decode_json
from app.security.regex import scan_pattern
from app.services.ingestion_pause import pausable


class RedmineError(ValueError):
    """Safe operational message: never contains credentials or response bodies."""


def validate_config(config):
    projects = config.get("projects", [])
    if not isinstance(projects, list) or len(projects) > 500:
        raise RedmineError("Redmine projects must be a list of up to 500 entries")
    seen = set()
    for entry in projects:
        if not isinstance(entry, dict):
            raise RedmineError("Each Redmine project must be an object")
        value = entry.get("project")
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise RedmineError("Use a Redmine project ID, identifier or *")
        value = str(value)
        if value != "*" and not scan_pattern(r"[a-z0-9][a-z0-9_-]{0,99}").fullmatch(
            value
        ):
            raise RedmineError("Use a Redmine project ID, identifier or *")
        if value.isdigit() and int(value) <= 0:
            raise RedmineError("Redmine project ID must be positive")
        if value in seen:
            raise RedmineError("Duplicate Redmine project selection")
        seen.add(value)
        if (
            not isinstance(entry.get("label", ""), str)
            or len(entry.get("label", "")) > 128
        ):
            raise RedmineError("Redmine project label must be at most 128 characters")
    if "*" in seen and len(seen) != 1:
        raise RedmineError("Choose either all projects (*) or individual projects")
    for field, default, lo, hi in [
        ("initial_lookback_days", 0, 0, 36500),
        ("overlap_seconds", 300, 60, 86400),
        ("max_pages", 1000, 1, 10000),
        ("full_scan_hours", 24, 1, 8760),
    ]:
        value = config.get(field, default)
        if type(value) is not int or not lo <= value <= hi:
            raise RedmineError(
                f"Redmine {field} must be an integer between {lo} and {hi}"
            )
    if type(config.get("include_journals", True)) is not bool:
        raise RedmineError("Redmine include_journals must be true or false")
    return config


def _timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError, TypeError):
        raise RedmineError("Redmine record has an invalid timestamp") from None


def _id(value):
    if type(value) is not int or value <= 0:
        raise RedmineError("Redmine record has an invalid numeric ID")
    return value


def _json(client, base, path, params=None):
    # Paths are generated locally from numeric IDs; no server-provided links are followed.
    try:
        with client.stream("GET", base + "/" + path, params=params) as response:
            if response.status_code != 200:
                raise RedmineError(
                    f"Redmine API HTTP {response.status_code}; check access, API enablement and rate limits"
                )
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 8 * 1024 * 1024:
                    raise RedmineError("Redmine response exceeds the 8 MiB limit")
        data = decode_json(body)
        if not isinstance(data, dict):
            raise RedmineError("Redmine returned an invalid JSON object")
        return data
    except httpx.HTTPError:
        raise RedmineError("Redmine network/TLS failure; cursor retained") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RedmineError("Redmine returned invalid JSON") from None


def _row_digest(row) -> str | None:
    try:
        text = json.dumps(row, sort_keys=True)
    except RecursionError:
        return None
    return hashlib.sha256(text.encode()).hexdigest()


def _pages(client, base, resource, params=None, max_pages=1000):
    offset, seen = 0, set()
    for _ in range(max_pages):
        data = _json(
            client,
            base,
            resource + ".json",
            {**(params or {}), "offset": offset, "limit": 100},
        )
        rows = data.get(resource)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RedmineError("Redmine returned an invalid collection")
        total = data.get("total_count")
        if type(total) is not int or total < 0 or data.get("offset") != offset:
            raise RedmineError("Redmine pagination metadata is missing or inconsistent")
        if not rows:
            if offset < total:
                raise RedmineError(
                    "Redmine returned an incomplete page; cursor retained"
                )
            return
        # A row's ID is read with the row, so an unusable ID is that row's
        # failure alone. Such a row is known by its content instead, so the
        # page is still recognised if it repeats and not otherwise.
        signature = tuple(
            value if type(value) is int else _row_digest(row)
            for value, row in ((row.get("id"), row) for row in rows)
        )
        if signature in seen:
            raise RedmineError("Redmine repeated a page; cursor retained")
        seen.add(signature)
        yield from rows
        offset += len(rows)
        if offset >= total:
            return
    raise RedmineError(
        "Redmine page limit reached; narrow the project/history selection or increase max_pages"
    )


def _fields(issue, project):
    fields = {
        "project.id": _id(project["id"]),
        "project.identifier": project.get("identifier", ""),
        "project.name": project.get("name", ""),
        "issue.id": _id(issue["id"]),
        "issue.subject": issue.get("subject", ""),
        "issue.done_ratio": issue.get("done_ratio", 0),
    }
    for name in (
        "status",
        "tracker",
        "priority",
        "assigned_to",
        "author",
        "category",
        "fixed_version",
    ):
        for part in ("id", "name"):
            value = (issue.get(name) or {}).get(part)
            if value is not None:
                fields[f"issue.{name}.{part}"] = value
    if "is_closed" in (issue.get("status") or {}):
        fields["issue.status.is_closed"] = issue["status"]["is_closed"]
    for custom in issue.get("custom_fields", []):
        if isinstance(custom, dict) and type(custom.get("id")) is int:
            value = custom.get("value")
            fields[f"issue.custom_field.{custom['id']}"] = (
                json.dumps(value) if isinstance(value, (list, dict)) else value
            )
    return fields


def _read(base, collection, project, issue, journal=None):
    """What is stored for an issue snapshot, or for one of its journals."""
    issue_id = _id(issue["id"])
    fields = _fields(issue, project)
    snapshot = {key: value for key, value in issue.items() if key != "journals"}
    record = journal if journal is not None else snapshot
    timestamp = _timestamp(record.get("updated_on") or record.get("created_on"))
    action = "issue.snapshot"
    actor = (
        None  # An issue's author is not necessarily the author of its latest change.
    )
    summary = f"Redmine #{issue_id}: {issue.get('subject', '')}"
    identity = ["issue", issue_id]
    payload = {"fields": fields, "issue": snapshot}
    if journal is not None:
        journal_id = _id(journal["id"])
        identity = ["journal", issue_id, journal_id]
        notes = str(journal.get("notes") or "")
        action = "issue.comment" if notes else "issue.updated"
        actor = (journal.get("user") or {}).get("name")
        fields.update(
            {
                "journal.id": journal_id,
                "journal.notes": notes,
                "journal.private_notes": bool(journal.get("private_notes", False)),
            }
        )
        for change in journal.get("details", []):
            if not isinstance(change, dict):
                continue
            prop, name = str(change.get("property", "")), str(change.get("name", ""))
            if prop in ("attr", "cf", "attachment") and scan_pattern(
                r"[A-Za-z0-9_]{1,80}"
            ).fullmatch(name):
                prefix = f"change.{prop}.{name}"
                fields[prefix + ".changed"] = True
                for part in ("old_value", "new_value"):
                    if change.get(part) is not None:
                        fields[prefix + "." + part] = str(change[part])
        payload["journal"] = journal
        summary += ": " + (notes[:500] if notes else "fields updated")
    # Journal identity excludes the current issue snapshot: later issue changes must not
    # turn unchanged historical comments into new events. Edited journals get a new hash.
    digest = hashlib.sha256(
        json.dumps([base, identity, record], sort_keys=True).encode()
    ).hexdigest()
    url = f"{base}/issues/{issue_id}"
    if journal is not None:
        url += f"#change-{journal['id']}"
    return dict(
        timestamp=timestamp,
        source="redmine",
        system=collection.get("label")
        or project.get("identifier")
        or str(project["id"]),
        actor=actor,
        action=action,
        outcome="info",
        severity=3,
        summary=summary[:2000],
        raw_pointer={
            "redmine": {
                "section": "projects",
                "project_selector": str(collection["project"]),
                "project_id": project["id"],
                "issue_id": issue_id,
                "url": url,
            }
        },
        normalized_payload=payload,
        external_id=digest,
        artifact_kind="redmine_issue" if journal is None else "redmine_journal",
        artifact_bytes=json.dumps(payload, sort_keys=True).encode(),
        artifact_content_type="application/json",
        artifact_key=f"redmine/{digest}.json",
        captured_by="keen:redmine",
    )


def _project(db, client, base, selection, project, config):
    pid = _id(project["id"])
    # Physical project identity survives changing from wildcard to a specific project.
    digest = hashlib.sha256(f"{base}:{pid}".encode()).hexdigest()
    name = namespace("redmine:" + digest)
    cursor = db.query(IngestionCursor).filter_by(name=name).one_or_none()
    started = datetime.now(timezone.utc).replace(tzinfo=None)
    since = cursor.last_ts if cursor and cursor.last_ts else None
    history_since = (cursor.meta or {}).get("history_since") if cursor else None
    if since is None and config.get("initial_lookback_days", 0):
        since = started - timedelta(days=config["initial_lookback_days"])
        history_since = since.isoformat()
    last_full = (cursor.meta or {}).get("last_full_scan") if cursor else None
    full_scan = since is None or (
        last_full is not None
        and started - _timestamp(last_full)
        >= timedelta(hours=config.get("full_scan_hours", 24))
    )
    if full_scan:
        since = _timestamp(history_since) if history_since else None
    params = {
        "project_id": pid,
        "subproject_id": "!*",
        "status_id": "*",
        "sort": "updated_on:asc,id:asc",
    }
    if since:
        params["updated_on"] = (
            ">="
            + (since - timedelta(seconds=config.get("overlap_seconds", 300))).isoformat(
                timespec="seconds"
            )
            + "Z"
        )
    created, examined = 0, 0
    reader = RecordReader()
    db.commit()  # No database transaction spans the remote wait.
    stubs = _pages(client, base, "issues", params, config.get("max_pages", 1000))
    for issue_id in reader.each(stubs, lambda stub: _id(stub.get("id"))):
        detail = _json(
            client,
            base,
            f"issues/{issue_id}.json",
            {"include": "journals"} if config.get("include_journals", True) else {},
        )
        issue = detail.get("issue")
        if not isinstance(issue, dict) or issue.get("id") != issue_id:
            raise RedmineError("Redmine returned an inconsistent issue detail")
        if (issue.get("project") or {}).get("id") != pid:
            raise RedmineError("Issue moved project during collection; retry required")
        # The issue snapshot, then each journal, each read by itself.
        records: list = [None]
        if config.get("include_journals", True):
            journals = issue.get("journals", [])
            records += [
                {"journal": journal}
                for journal in (journals if isinstance(journals, list) else [journals])
            ]

        def read(record, issue=issue):
            if record is None:
                return _read(base, selection, project, issue)
            journal = record["journal"]
            if not isinstance(journal, dict):
                raise TypeError("Redmine journal is not an object")
            return _read(base, selection, project, issue, journal)

        for event in reader.each(records, read):
            result = store_event_with_artifact(db, **event)
            created += int(not result.get("deduped", False))
        examined += 1
    if cursor is None:
        cursor = IngestionCursor(name=name, meta={})
    if full_scan or last_full is None:
        cursor.meta = {
            **(cursor.meta or {}),
            "last_full_scan": started.isoformat(),
            "history_since": history_since,
        }
    cursor.last_ts = started
    cursor.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(cursor)
    db.commit()
    return {
        "project_id": pid,
        "project": project.get("identifier", str(pid)),
        "examined_issues": examined,
        "created_events": created,
        "invalid_records": reader.invalid,
        "cursor": started.isoformat() + "Z",
    }


@contextmanager
def _run_lock(db, base):
    # A dedicated connection keeps the session advisory lock across per-event commits.
    lock_id = int.from_bytes(
        hashlib.sha256(("redmine:" + base).encode()).digest()[:8], "big", signed=True
    )
    with advisory_lock(db, lock_id) as acquired:
        yield acquired


@pausable("redmine")
@connection_runs("redmine")
def ingest_redmine_all(db):
    if settings.demo_mode or not settings.redmine_enabled:
        return [{"skipped": True, "reason": "Redmine disabled (or demo mode)"}]
    base = settings.redmine_base_url.strip().rstrip("/")
    try:
        parsed = urlsplit(base)
    except ValueError:
        return [{"error": "Invalid KEEN_REDMINE_BASE_URL"}]
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not is_safe_url(base)
    ):
        return [
            {
                "error": "Configure a safe HTTPS KEEN_REDMINE_BASE_URL without credentials, query or fragment"
            }
        ]
    if not settings.redmine_api_key.strip():
        return [{"error": "Set KEEN_REDMINE_API_KEY in the API/worker environment"}]
    try:
        config = validate_config(load_document("redmine", settings.redmine_config_path))
    except (ValueError, OSError):
        return [
            {
                "error": "Invalid Redmine configuration; check projects and shared settings"
            }
        ]
    if not config.get("projects"):
        return [
            {
                "skipped": True,
                "reason": "Add a Redmine project collection; use project * for all accessible projects",
            }
        ]
    results, seen = [], set()
    with _run_lock(db, base) as acquired:
        if not acquired:
            return [{"skipped": True, "reason": "Another Redmine run is in progress"}]
        with ingestion_client(
            timeout=httpx.Timeout(30, connect=10),
            verify=True,
            follow_redirects=False,
            headers={
                "X-Redmine-API-Key": settings.redmine_api_key.strip(),
                "Accept": "application/json",
            },
        ) as client:
            for selection in config["projects"]:
                try:
                    key = str(selection["project"])
                    projects = (
                        _pages(
                            client,
                            base,
                            "projects",
                            max_pages=config.get("max_pages", 1000),
                        )
                        if key == "*"
                        else [
                            _json(client, base, f"projects/{quote(key, safe='')}.json")[
                                "project"
                            ]
                        ]
                    )
                    reader = RecordReader()
                    for project, pid in reader.each(
                        projects, lambda project: (project, _id(project["id"]))
                    ):
                        if pid in seen:
                            continue
                        seen.add(pid)
                        try:
                            results.append(
                                _project(db, client, base, selection, project, config)
                            )
                        except Exception as exc:
                            db.rollback()
                            results.append(
                                {
                                    "project_id": pid,
                                    "error": str(exc)
                                    if isinstance(exc, RedmineError)
                                    else collection_error(exc),
                                }
                            )
                    if reader.invalid:
                        results.append(
                            {
                                "collection": key,
                                "invalid_records": reader.invalid,
                                "error": "Redmine listed projects without a valid numeric ID",
                            }
                        )
                except Exception as exc:
                    db.rollback()
                    results.append(
                        {
                            "collection": str(selection["project"]),
                            "error": str(exc)
                            if isinstance(exc, RedmineError)
                            else collection_error(exc),
                        }
                    )
    return results or [
        {"skipped": True, "reason": "No accessible projects returned by Redmine"}
    ]


register(
    Source(
        name="redmine",
        module=__name__,
        queue=HTTP_QUEUE,
        schedule=float(settings.redmine_poll_seconds),
        config_setting="redmine_config_path",
        public_fields=("base_url",),
        secret_fields=("api_key",),
        input_sections=("projects",),
        section="projects",
        id_field="project",
        definition_sections=("projects",),
        key_field="project",
        demo=False,
        validate_config=validate_config,
    )
)
