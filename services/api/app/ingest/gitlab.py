from app.core.datetime_utils import utc_now_naive
from app.services.ingestion_pause import pausable
"""GitLab project activity collection for user-owned projects and groups/subgroups."""
from datetime import datetime, timedelta
import hashlib
import json
from urllib.parse import quote
import httpx
from app.core.config import settings
from app.core.managed_configuration import load_document
from app.db.models import IngestionCursor
from app.ingest.common import is_safe_url, store_event_with_artifact
from app.ingest.forgejo import _parse_iso_ts


def _pages(client, base, path, params=None):
    rows, seen = [], set()
    for page in range(1, 1001):
        response = client.get(base + '/api/v4/' + path,
                              params={**(params or {}), 'page': page, 'per_page': 100})
        if response.status_code != 200:
            raise RuntimeError(f'GitLab API HTTP {response.status_code}; check token permissions and configured account')
        data = response.json()
        if not isinstance(data, list):
            raise ValueError('GitLab API returned an invalid list')
        if not data:
            return rows
        signature = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        if signature in seen:
            raise ValueError('GitLab repeated a page; collection stopped')
        seen.add(signature); rows.extend(data)
        if len(rows) > 100000:
            raise ValueError('GitLab response exceeds collection limit')
    raise ValueError('GitLab collection exceeded 1000 pages')


def _project_events(db, client, base, section, key, project, label):
    project_id = int(project['id'])
    identity = json.dumps([base, section, key, project_id], separators=(',', ':'))
    digest = hashlib.sha256(identity.encode()).hexdigest()
    cursor_name = 'gitlab:' + digest
    cur = db.query(IngestionCursor).filter_by(name=cursor_name).one_or_none()
    since = cur.last_ts if cur and cur.last_ts else utc_now_naive() - timedelta(days=14)
    # Date-based API filter overlaps the cursor day; stable IDs deduplicate retries.
    events = _pages(client, base, f'projects/{project_id}/events',
                    {'after': (since - timedelta(days=1)).date().isoformat(), 'sort': 'asc'})
    newest, count = since, 0
    for event in events:
        timestamp = _parse_iso_ts(event.get('created_at'))
        if not timestamp or timestamp < since:
            continue
        if event.get('id') is None:
            raise ValueError('GitLab activity lacks a stable event ID')
        external_id = hashlib.sha256(f'{digest}:{event["id"]}'.encode()).hexdigest()
        payload = json.dumps(event, sort_keys=True).encode()
        result = store_event_with_artifact(db, timestamp=timestamp, source='gitlab',
            system=label or project.get('path_with_namespace') or str(project_id),
            actor=event.get('author_username') or (event.get('author') or {}).get('username'),
            action=str(event.get('action_name') or 'activity'), outcome='info', severity=3,
            summary=f"GitLab {project.get('path_with_namespace', project_id)}: {event.get('action_name', 'activity')} {event.get('target_title') or ''}",
            raw_pointer={'gitlab': {'section': section, 'key': key, 'project_id': project_id}},
            normalized_payload={'activity': event}, external_id=external_id,
            artifact_kind='gitlab_activity', artifact_bytes=payload, artifact_content_type='application/json',
            artifact_key=f'gitlab/{digest}/{external_id}.json', captured_by='keen:gitlab')
        count += not result.get('deduped', False)
        newest = max(newest, timestamp)
    if cur is None:
        cur = IngestionCursor(name=cursor_name, meta={})
    cur.last_ts = newest; cur.updated_at = utc_now_naive()
    db.add(cur); db.commit()
    return {'project': project.get('path_with_namespace', project_id), 'created_events': count}


@pausable('gitlab')
def ingest_gitlab_all(db):
    if not settings.gitlab_enabled:
        return [{'skipped': True, 'reason': 'KEEN_GITLAB_ENABLED=false'}]
    base = settings.gitlab_base_url.rstrip('/')
    if not is_safe_url(base):
        return [{'error': 'Configure a safe HTTPS KEEN_GITLAB_BASE_URL'}]
    cfg = load_document('gitlab', settings.gitlab_config_path)
    results = []
    with httpx.Client(timeout=30, verify=True, follow_redirects=False,
                      headers={'PRIVATE-TOKEN': settings.gitlab_token, 'Accept': 'application/json'}) as client:
        for section, field in [('groups', 'group'), ('users', 'user')]:
            for entry in cfg.get(section, []):
                key = str(entry[field])
                try:
                    projects = _pages(client, base, f'{section}/{quote(key, safe="")}/projects',
                                      {'include_subgroups': 'true', 'with_shared': 'false'} if section == 'groups' else {})
                    results.append({'scope': section, 'account': key, 'discovered_projects': len(projects)})
                    seen = set()
                    for project in projects:
                        if project['id'] in seen:
                            continue
                        seen.add(project['id'])
                        try:
                            results.append(_project_events(db, client, base, section, key, project, entry.get('label')))
                        except Exception as exc:
                            db.rollback(); results.append({'project_id': project['id'], 'error': str(exc)})
                except Exception as exc:
                    db.rollback(); results.append({'scope': section, 'account': key, 'error': str(exc)})
    return results or [{'skipped': True, 'reason': 'No GitLab groups or users configured'}]
