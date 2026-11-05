"""GitLab project activity collection for user-owned projects and groups/subgroups."""

import hashlib
import json
from datetime import timedelta
from urllib.parse import quote

from app.core.datetime_utils import utc_now_naive
from app.db.models import IngestionCursor
from app.ingest.common import is_safe_url, store_event_with_artifact
from app.ingest.connections import connection_runs, load_document, namespace, settings
from app.ingest.git_activity import _parse_iso_ts
from app.ingest.http import client as ingestion_client
from app.security.errors import collection_error
from app.services.ingestion_pause import pausable


def _page_rows(client, base, path, params=None):
    """Each page of a listing in turn, read only when the caller asks for it.

    A page holds at most the 100 rows requested, and a listing at most 1,000
    pages."""
    seen = set()
    for page in range(1, 1001):
        response = client.get(
            base + "/api/v4/" + path,
            params={**(params or {}), "page": page, "per_page": 100},
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"GitLab API HTTP {response.status_code}; check token permissions and configured account"
            )
        data = response.json()
        if not isinstance(data, list):
            raise ValueError("GitLab API returned an invalid list")
        if not data:
            return
        if len(data) > 100:
            raise ValueError("GitLab returned more items than a page holds")
        signature = hashlib.sha256(
            json.dumps(data, sort_keys=True).encode()
        ).hexdigest()
        if signature in seen:
            raise ValueError("GitLab repeated a page; collection stopped")
        seen.add(signature)
        yield data
    raise ValueError("GitLab collection exceeded 1000 pages")


def _pages(client, base, path, params=None, *, keep):
    """Every item of a listing, each reduced by ``keep`` as its page arrives,
    so only the reduced items are held while later pages are read."""
    return [
        keep(item) for data in _page_rows(client, base, path, params) for item in data
    ]


def _project_reference(project):
    """The fields kept for each discovered project while its events are read.
    An ID other than a number or text reads as missing, so that project fails
    alone."""
    if not isinstance(project, dict):
        return {"id": None}
    path = project.get("path_with_namespace")
    ident = project.get("id")
    reference = {"id": ident if isinstance(ident, (int, str)) else None}
    if isinstance(path, str):
        reference["path_with_namespace"] = path
    return reference


def _project_events(db, client, base, section, key, project, label):
    project_id = int(project["id"])
    identity = json.dumps([base, section, key, project_id], separators=(",", ":"))
    digest = hashlib.sha256(identity.encode()).hexdigest()
    cursor_name = namespace("gitlab:" + digest)
    cur = db.query(IngestionCursor).filter_by(name=cursor_name).one_or_none()
    since = cur.last_ts if cur and cur.last_ts else utc_now_naive() - timedelta(days=14)
    if cur is None:
        cur = IngestionCursor(name=cursor_name, meta={})
    newest, count = since, 0

    def advance():
        # Events arrive oldest first, so every event up to the newest stored
        # has been collected: each page commits and moves the cursor before
        # the next is read, and a long backlog progresses a page at a time.
        cur.last_ts = newest
        cur.updated_at = utc_now_naive()
        db.add(cur)
        db.commit()

    # Date-based API filter overlaps the cursor day; stable IDs deduplicate retries.
    pages = _page_rows(
        client,
        base,
        f"projects/{project_id}/events",
        {"after": (since - timedelta(days=1)).date().isoformat(), "sort": "asc"},
    )
    advanced = False
    for events in pages:
        for event in events:
            newest, stored = _store_project_event(
                db,
                event,
                since,
                newest,
                digest,
                label,
                project,
                project_id,
                section,
                key,
            )
            count += stored
        advance()
        advanced = True
    if not advanced:
        advance()
    return {
        "project": project.get("path_with_namespace", project_id),
        "created_events": count,
    }


def _store_project_event(
    db, event, since, newest, digest, label, project, project_id, section, key
):
    """Store one event; the newest timestamp so far, and 1 if it was new."""
    timestamp = _parse_iso_ts(event.get("created_at"))
    if not timestamp or timestamp < since:
        return newest, 0
    if event.get("id") is None:
        raise ValueError("GitLab activity lacks a stable event ID")
    external_id = hashlib.sha256(f"{digest}:{event['id']}".encode()).hexdigest()
    payload = json.dumps(event, sort_keys=True).encode()
    result = store_event_with_artifact(
        db,
        timestamp=timestamp,
        source="gitlab",
        system=label or project.get("path_with_namespace") or str(project_id),
        actor=event.get("author_username")
        or (event.get("author") or {}).get("username"),
        action=str(event.get("action_name") or "activity"),
        outcome="info",
        severity=3,
        summary=f"GitLab {project.get('path_with_namespace', project_id)}: {event.get('action_name', 'activity')} {event.get('target_title') or ''}",
        raw_pointer={
            "gitlab": {"section": section, "key": key, "project_id": project_id}
        },
        normalized_payload={"activity": event},
        external_id=external_id,
        artifact_kind="gitlab_activity",
        artifact_bytes=payload,
        artifact_content_type="application/json",
        artifact_key=f"gitlab/{digest}/{external_id}.json",
        captured_by="keen:gitlab",
    )
    return max(newest, timestamp), int(not result.get("deduped", False))


@pausable("gitlab")
@connection_runs("gitlab")
def ingest_gitlab_all(db):
    base = settings.gitlab_base_url.rstrip("/")
    if not is_safe_url(base):
        return [{"error": "Configure a safe HTTPS KEEN_GITLAB_BASE_URL"}]
    cfg = load_document("gitlab", settings.gitlab_config_path)
    results = []
    with ingestion_client(
        timeout=30,
        verify=True,
        follow_redirects=False,
        headers={"PRIVATE-TOKEN": settings.gitlab_token, "Accept": "application/json"},
    ) as client:
        for section, field in [("groups", "group"), ("users", "user")]:
            for entry in cfg.get(section, []):
                key = str(entry[field])
                try:
                    projects = _pages(
                        client,
                        base,
                        f"{section}/{quote(key, safe='')}/projects",
                        {"include_subgroups": "true", "with_shared": "false"}
                        if section == "groups"
                        else {},
                        keep=_project_reference,
                    )
                    results.append(
                        {
                            "scope": section,
                            "account": key,
                            "discovered_projects": len(projects),
                        }
                    )
                    seen = set()
                    for project in projects:
                        if project["id"] in seen:
                            continue
                        seen.add(project["id"])
                        try:
                            results.append(
                                _project_events(
                                    db,
                                    client,
                                    base,
                                    section,
                                    key,
                                    project,
                                    entry.get("label"),
                                )
                            )
                        except Exception as exc:
                            db.rollback()
                            results.append(
                                {
                                    "project_id": project["id"],
                                    "error": collection_error(exc),
                                }
                            )
                except Exception as exc:
                    db.rollback()
                    results.append(
                        {
                            "scope": section,
                            "account": key,
                            "error": collection_error(exc),
                        }
                    )
    return results or [
        {"skipped": True, "reason": "No GitLab groups or users configured"}
    ]
