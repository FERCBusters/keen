"""Database-managed collection settings and unified evidence definitions."""
from __future__ import annotations

import json
import re
import httpx
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.managed_configuration import load_document
from app.db.models import ControlItem, Event, ManagedConfiguration, ManagedConfigurationRevision
from app.db.session import get_db
from app.mapping.rules import (
    _KNOWN_WHEN_KEYS, _as_rule_entries, evaluate_by_framework, parse_rules,
)
from app.mapping.collector import collector_id, collector_pointer_filter
from app.security.auth import require_admin
from app.security.redaction import redact_url

router = APIRouter(dependencies=[Depends(require_admin)])

CONNECTORS = {
    "jenkins": ("jenkins_config_path", "jobs", "name"),
    "loki": ("loki_queries_path", "queries", "name"),
    "rss": ("rss_config_path", "feeds", "name"),
    "github": ("github_config_path", "organizations", "org"),
    "forgejo": ("forgejo_config_path", "feeds", "url"),
    "cloudwatch_logs": ("cloudwatch_logs_config_path", "queries", "name"),
    "taiga": ("taiga_config_path", "projects", "id"),
    "bookstack": ("bookstack_config_path", "page_mappings", "match"),
    "google_workspace": ("google_workspace_config_path", "streams", "name"),
    "webhooks": ("webhooks_path", "providers", None),
}
COLLECTION_SECTIONS = {"jobs", "queries", "feeds", "organizations", "repos", "orgs",
                       "projects", "streams", "providers", "selected_pages", "capture_pages",
                       "page_mappings", "pages"}
# Credential material stays in Docker environment variables, not editable JSON.
SECRET_KEYS = {"password", "token", "api_key", "authorization", "client_secret", "private_key", "secret"}


class DocumentInput(BaseModel):
    version: int
    document: dict[str, Any]


class RuleInput(BaseModel):
    version: int
    rule: dict[str, Any]
    create_only: bool = False
    apply_existing: bool = True


class EvidenceDefinitionInput(BaseModel):
    adapter: str
    section: str
    entry_key: str | None = None
    entry: dict[str, Any]
    rule: dict[str, Any]
    connector_version: int
    rules_version: int
    apply_existing: bool = True


class AdapterSettingsInput(BaseModel):
    version: int
    settings: dict[str, Any]


@router.get("/v1/admin/adapter-settings/{adapter}")
def get_adapter_settings(adapter: str, db: Session = Depends(get_db)):
    if adapter not in CONNECTORS:
        raise HTTPException(404, "Unknown adapter")
    doc = _connector_document(adapter, db)
    _safe_document(doc)
    return {"version": _version(db, adapter),
            "settings": {k: v for k, v in doc.items() if k not in COLLECTION_SECTIONS}}


@router.put("/v1/admin/adapter-settings/{adapter}")
def put_adapter_settings(adapter: str, payload: AdapterSettingsInput,
                         user=Depends(require_admin), db: Session = Depends(get_db)):
    if adapter not in CONNECTORS:
        raise HTTPException(404, "Unknown adapter")
    if COLLECTION_SECTIONS.intersection(payload.settings):
        raise HTTPException(400, "Edit collection items in evidence definitions")
    _safe_document(payload.settings)
    doc = deepcopy(_connector_document(adapter, db))
    for key in list(doc):
        if key not in COLLECTION_SECTIONS:
            del doc[key]
    doc.update(payload.settings)
    _validate_connector(adapter, doc)
    return _save(db, adapter, doc, payload.version, user)


def _safe_document(document: dict[str, Any]):
    if len(json.dumps(document, ensure_ascii=False)) > 2_000_000:
        raise HTTPException(400, "Configuration exceeds 2 MB")

    def walk(value: Any, path: str = ""):
        if isinstance(value, dict):
            for key, child in value.items():
                key = str(key)
                if key.lower() in SECRET_KEYS or key.lower().endswith("_password"):
                    raise HTTPException(400, f"Move credential {path + key} into a server environment variable")
                walk(child, path + key + ".")
        elif isinstance(value, list):
            for child in value:
                walk(child, path)
    walk(document)


def _connector_document(name: str, db: Session) -> dict:
    path = getattr(settings, CONNECTORS[name][0])
    return load_document(name, path, db=db)


def _version(db: Session, name: str) -> int:
    row = db.get(ManagedConfiguration, name)
    return row.version if row else 0


def _save(db: Session, name: str, document: dict, version: int, user: Any):
    row = db.query(ManagedConfiguration).filter_by(name=name).with_for_update().one_or_none()
    current = row.version if row else 0
    if current != version:
        raise HTTPException(409, "Configuration changed in another session. Reload before saving.")
    if row is None:
        row = ManagedConfiguration(name=name, document=document, version=1, updated_by=user.id)
        db.add(row)
    else:
        row.version += 1
        row.document = document
        row.updated_by = user.id
    db.add(ManagedConfigurationRevision(name=name, version=row.version, document=deepcopy(document), updated_by=user.id))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Configuration changed in another session. Reload before saving.") from exc
    return {"version": row.version, "document": document}


def _validate_connector(name: str, document: dict):
    _safe_document(document)
    key = CONNECTORS[name][1]
    rows = document.get(key, {} if name == "webhooks" else [])
    if name == "webhooks":
        if not isinstance(rows, dict) or any(not isinstance(v, dict) for v in rows.values()):
            raise HTTPException(400, "providers must be an object of named provider settings")
    elif not isinstance(rows, list) or len(rows) > 500 or any(not isinstance(v, dict) for v in rows):
        raise HTTPException(400, f"{key} must be a list of up to 500 objects")
    id_fields = {"jenkins": "name", "loki": "name", "rss": "url", "github": "org",
                 "forgejo": "url", "cloudwatch_logs": "name", "google_workspace": "name"}
    identity = id_fields.get(name)
    if identity:
        keys = [str(item.get(identity) or "").strip() for item in rows]
        if any(not value for value in keys) or len(set(keys)) != len(keys):
            raise HTTPException(400, f"Every {name} entry needs a distinct {identity}")
    if name == "rss":
        for feed in rows:
            address = urlparse(str(feed.get("url") or ""))
            if address.scheme != "https" or not address.hostname:
                raise HTTPException(400, "RSS feeds require an HTTPS URL")
            if feed.get("verify_tls") is False:
                raise HTTPException(400, "TLS verification cannot be disabled")
            if feed.get("auth") or feed.get("headers"):
                raise HTTPException(400, "RSS credentials and custom headers require administrator review outside the web editor")
    if name == "loki":
        for query in rows:
            if not query.get("name") or not query.get("logql"):
                raise HTTPException(400, "Each Loki query needs a name and LogQL")
    if name == "jenkins" and any(not re.fullmatch(r"[A-Za-z0-9_. -]{1,128}", j["name"]) for j in rows):
        raise HTTPException(400, "Jenkins job names must be simple top-level names")
    if name == "bookstack":
        selected = document.get("selected_pages", [])
        if (not isinstance(selected, list) or len(selected) > 500
                or any(not isinstance(page, dict) for page in selected)):
            raise HTTPException(400, "BookStack selected pages must be a list of up to 500 pages")
        page_ids = [page.get("id") for page in selected]
        if any(not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 for pid in page_ids) or len(set(page_ids)) != len(page_ids):
            raise HTTPException(400, "BookStack pages need distinct positive IDs")
        for mapping in rows:
            match = mapping.get("match", {})
            if not isinstance(match, dict):
                raise HTTPException(400, "BookStack page match must be an object")
            for key in ("title_regex", "slug_regex"):
                if match.get(key):
                    _validate_regex(match[key], key)


@router.get("/v1/admin/managed-configurations")
def list_configurations(db: Session = Depends(get_db)):
    return {"items": [{"name": n, "section": spec[1], "key": spec[2],
                       "version": _version(db, n)} for n, spec in CONNECTORS.items()]}


@router.get("/v1/admin/managed-configurations/{name}")
def get_configuration(name: str, db: Session = Depends(get_db)):
    if name not in CONNECTORS:
        raise HTTPException(404, "Unknown connector")
    document = _connector_document(name, db)
    _safe_document(document)
    if name == "rss" and any(feed.get("headers") or feed.get("auth")
                             for feed in document.get("feeds", []) if isinstance(feed, dict)):
        raise HTTPException(409, "This RSS configuration contains headers or credentials. Move them out of YAML before editing it in the web UI.")
    return {"name": name, "version": _version(db, name), "document": document,
            "origin": "database" if _version(db, name) else "shipped YAML"}


@router.put("/v1/admin/managed-configurations/{name}")
def put_configuration(name: str, payload: DocumentInput, user=Depends(require_admin), db: Session = Depends(get_db)):
    if name not in CONNECTORS:
        raise HTTPException(404, "Unknown connector")
    _validate_connector(name, payload.document)
    _check_linked_collectors(name, payload.document, db)
    return _save(db, name, payload.document, payload.version, user)


def _normalized_rules(db: Session) -> list[dict]:
    raw = load_document("rules", settings.rules_path, db=db)
    out = []
    for hint, item in _as_rule_entries(raw):
        # A single rule under its inherited framework is parsed exactly as it
        # would be during ingestion, including multi-framework map_to forms.
        parsed = parse_rules({"framework": hint, "rules": [{**item, "enabled": True}]})
        if not parsed:
            continue
        rule = parsed[0]
        out.append({"id": rule.id, "description": item.get("description") or "",
                    "when": {k: v for k, v in vars(rule.when).items() if v is not None},
                    "map_to": [{"framework": t.framework_slug, "ref": t.ref,
                                **({"roles_any": sorted(t.roles_any)} if t.roles_any else {})}
                               for t in rule.targets],
                    "confidence": rule.confidence, "enabled": item.get("enabled") is not False})
    return out


def _collector_key(adapter: str, section: str, entry: dict, provider: str | None = None) -> str:
    if adapter == "webhooks":
        return str(provider or "").strip()
    if adapter == "github" and section == "repos":
        return f'{entry.get("owner", "")}/{entry.get("repo", "")}'
    if adapter == "bookstack" and section == "selected_pages":
        return str(entry.get("id") or "")
    field = {"jenkins": "name", "loki": "name", "rss": "url", "github": "org",
             "forgejo": "url", "cloudwatch_logs": "name", "taiga": "id",
             "google_workspace": "name"}.get(adapter)
    if adapter == "github" and section == "feeds":
        field = "url"
    return str(entry.get(field) or "").strip() if field else ""


def _editable_collector(adapter: str, section: str) -> bool:
    return (adapter in CONNECTORS and section in {
        "jenkins": {"jobs"}, "loki": {"queries"}, "rss": {"feeds"},
        "github": {"organizations", "repos", "feeds"}, "forgejo": {"feeds"},
        "cloudwatch_logs": {"queries"}, "taiga": {"projects"},
        "google_workspace": {"streams"}, "webhooks": {"providers"},
        "bookstack": {"selected_pages"},
    }.get(adapter, set()))


def _check_linked_collectors(adapter: str, document: dict, db: Session):
    """Keep advanced edits from silently orphaning published definitions."""
    linked = {rule.get("when", {}).get("collector") for rule in _normalized_rules(db)}
    for section in ("jobs", "queries", "feeds", "organizations", "repos", "projects", "streams", "providers", "selected_pages"):
        if not _editable_collector(adapter, section):
            continue
        old = _connector_document(adapter, db).get(section, {} if adapter == "webhooks" else [])
        new = document.get(section, {} if adapter == "webhooks" else [])
        def ids(rows):
            iterator = rows.items() if adapter == "webhooks" else ((None, row) for row in rows)
            return {collector_id(adapter, section, _collector_key(adapter, section, row, provider))
                    for provider, row in iterator}
        if ids(old) & linked - ids(new):
            raise HTTPException(409, "A collection item has framework mappings. Remove those rules before deleting or renaming it.")


def _validate_collection_entry(adapter: str, section: str, entry: dict, key: str):
    """Reject entries that cannot be ingested even if the generic JSON is valid."""
    if adapter == "webhooks" and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key):
        raise HTTPException(400, "Webhook provider name must use letters, numbers, underscores or hyphens")
    if section == "feeds":
        url = urlparse(key)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise HTTPException(400, "Feed URLs must be HTTPS without embedded credentials")
        if redact_url(key) != key:
            raise HTTPException(400, "Feed URLs containing credentials cannot be linked to evidence definitions")
    if adapter == "github" and section in ("organizations", "repos"):
        parts = [key] if section == "organizations" else key.split("/")
        if len(parts) != (1 if section == "organizations" else 2) or any(
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", part) for part in parts
        ):
            raise HTTPException(400, "Give a valid GitHub organisation or owner/repository name")
    if adapter == "taiga":
        try:
            if int(key) <= 0:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(400, "Taiga project ID must be a positive integer") from exc
    if adapter == "bookstack":
        if not isinstance(entry.get("id"), int) or isinstance(entry.get("id"), bool) or entry["id"] <= 0:
            raise HTTPException(400, "Select a BookStack page from the catalogue")
        if entry.get("book_id") is not None and (not isinstance(entry["book_id"], int) or entry["book_id"] <= 0):
            raise HTTPException(400, "BookStack book ID must be a positive integer")
    if adapter == "google_workspace" and not entry.get("application"):
        raise HTTPException(400, "Choose a Google Workspace application for this stream")
    if adapter in ("loki", "cloudwatch_logs"):
        if adapter == "loki" and not entry.get("logql"):
            raise HTTPException(400, "Enter a LogQL query")
        if adapter == "cloudwatch_logs" and not entry.get("log_group"):
            raise HTTPException(400, "Enter the CloudWatch log group")
        event_defaults = entry.get("event") or {}
        if not isinstance(event_defaults, dict):
            raise HTTPException(400, "Event fields must be an object")
        if event_defaults.get("source") not in (None, adapter):
            raise HTTPException(400, "The event source must match its adapter")


@router.get("/v1/admin/evidence-definitions")
def get_evidence_definitions(db: Session = Depends(get_db)):
    """Show collection items alongside source-wide and item-specific definitions."""
    items = []
    warnings = []
    sections = {"jenkins": ["jobs"], "loki": ["queries"], "rss": ["feeds"],
                "github": ["organizations", "repos", "feeds"], "forgejo": ["feeds"],
                "cloudwatch_logs": ["queries"], "taiga": ["projects"],
                "google_workspace": ["streams"], "webhooks": ["providers"],
                "bookstack": ["selected_pages"]}
    for adapter, groups in sections.items():
        doc = _connector_document(adapter, db)
        try:
            _safe_document(doc)
        except HTTPException:
            warnings.append(f"{adapter} contains sensitive settings that require server administrator review")
            continue
        for section in groups:
            entries = doc.get(section, {} if adapter == "webhooks" else [])
            for provider, entry in (entries.items() if adapter == "webhooks" else ((None, e) for e in entries)):
                key = _collector_key(adapter, section, entry, provider)
                if key:
                    if section == "feeds" and redact_url(key) != key:
                        warnings.append(f"{adapter} has a feed with a credential in its URL; edit it outside the evidence definition editor")
                        continue
                    items.append({"adapter": adapter, "section": section, "key": key,
                                  "collector": collector_id(adapter, section, key), "entry": entry,
                                  "version": _version(db, adapter)})
    rules = _normalized_rules(db)
    return {"collectors": items, "rules": rules, "rules_version": _version(db, "rules"),
            "warnings": warnings}


@router.get("/v1/admin/bookstack/catalog")
def bookstack_catalog(kind: str = "books", book_id: int | None = None,
                      offset: int = 0):
    """Small, metadata-only BookStack pages; credentials remain on the server."""
    if kind not in ("books", "chapters", "pages"):
        raise HTTPException(400, "Choose books, chapters or pages")
    if kind != "books" and (book_id is None or book_id <= 0):
        raise HTTPException(400, "Select a book first")
    if offset < 0 or offset > 100_000:
        raise HTTPException(400, "Invalid catalogue offset")
    from app.ingest.bookstack import _client
    try:
        with _client() as client:
            params = {"count": 100, "offset": offset}
            if book_id is not None and kind != "books":
                params["filter[book_id]"] = book_id
            response = client.get(f"/api/{kind}", params=params)
            response.raise_for_status()
            body = response.json()
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(502, "BookStack catalogue is unavailable; check the configured backend URL and API credentials") from exc
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise HTTPException(502, "BookStack returned an unexpected catalogue response")
    items = []
    for value in body["data"][:100]:
        if not isinstance(value, dict) or not isinstance(value.get("id"), int):
            continue
        if (book_id is not None and kind != "books" and isinstance(value.get("book_id"), int)
                and value["book_id"] != book_id):
            continue
        items.append({"id": value["id"], "name": str(value.get("name") or "")[:250],
                      "slug": str(value.get("slug") or "")[:250],
                      "book_id": value.get("book_id") if isinstance(value.get("book_id"), int) else book_id,
                      "chapter_id": value.get("chapter_id") if isinstance(value.get("chapter_id"), int) else None})
    total = body.get("total")
    more = (isinstance(total, int) and offset + len(body["data"]) < total) or (
        not isinstance(total, int) and len(body["data"]) >= 100)
    return {"items": items, "next_offset": offset + len(body["data"]) if more else None}


@router.put("/v1/admin/evidence-definitions/{rule_id}")
def save_evidence_definition(rule_id: str, payload: EvidenceDefinitionInput,
                             user=Depends(require_admin), db: Session = Depends(get_db)):
    """Publish a collection item and mapping rule in one database transaction."""
    adapter, section = payload.adapter, payload.section
    if section == "source":
        rule = deepcopy(payload.rule)
        if rule.get("id") != rule_id or not isinstance(rule.get("when"), dict):
            raise HTTPException(400, "Rule identity is required")
        source = rule["when"].get("source")
        if (source != adapter and not (adapter == "webhooks" and
                                       isinstance(source, str) and source.startswith("webhook:"))
                or rule["when"].get("collector")):
            raise HTTPException(400, "Source-wide definition must use the selected source")
        _validate_rule(rule, db)
        rules = _normalized_rules(db)
        if len({r["id"] for r in rules}) != len(rules):
            raise HTTPException(409, "Existing rules have duplicate IDs")
        found = next((i for i, item in enumerate(rules) if item["id"] == rule_id), None)
        if found is None:
            rules.append(rule)
        else:
            rules[found] = rule
        saved = _save(db, "rules", {"rules": rules}, payload.rules_version, user)
        return {"rules_version": saved["version"], "rule": rule,
                "backfill": _queue_rule_backfill(db, rule, saved["version"]) if payload.apply_existing else None}
    if not _editable_collector(adapter, section):
        raise HTTPException(400, "This adapter entry requires the advanced configuration editor")
    key = _collector_key(adapter, section, payload.entry,
                         payload.entry_key if adapter == "webhooks" else None)
    if not key or len(key) > 512:
        raise HTTPException(400, "Provide a stable name or URL for the collection item")
    _validate_collection_entry(adapter, section, payload.entry, key)
    if payload.entry_key is not None and payload.entry_key != key:
        raise HTTPException(400, "Collection identity cannot be renamed; create a new definition instead")
    rule = deepcopy(payload.rule)
    if rule.get("id") != rule_id:
        raise HTTPException(400, "Rule ID does not match the URL")
    when = rule.setdefault("when", {})
    expected_source = f"webhook:{key}" if adapter == "webhooks" else adapter
    if when.get("source") not in (None, "", expected_source):
        raise HTTPException(400, "Rule source must match the selected adapter")
    when["source"] = expected_source
    when["collector"] = collector_id(adapter, section, key)
    _validate_rule(rule, db)

    document = deepcopy(_connector_document(adapter, db))
    rules = _normalized_rules(db)
    if len({r["id"] for r in rules}) != len(rules):
        raise HTTPException(409, "Existing rules have duplicate IDs")
    entries = document.setdefault(section, {} if adapter == "webhooks" else [])
    if adapter == "webhooks":
        if payload.entry_key is None and key in entries:
            raise HTTPException(409, "Collection item already exists; reload and edit it")
        entries[key] = deepcopy(payload.entry)
    else:
        found = next((i for i, item in enumerate(entries)
                      if _collector_key(adapter, section, item) == key), None)
        if payload.entry_key is None and found is not None:
            raise HTTPException(409, "Collection item already exists; reload and edit it")
        if payload.entry_key is not None and found is None:
            raise HTTPException(409, "Collection item was removed; reload before saving")
        if found is None:
            entries.append(deepcopy(payload.entry))
        else:
            entries[found] = deepcopy(payload.entry)
    found_rule = next((i for i, item in enumerate(rules) if item["id"] == rule_id), None)
    if found_rule is None:
        rules.append(rule)
    else:
        previous = rules[found_rule].get("when", {}).get("collector")
        if previous and previous != when["collector"]:
            raise HTTPException(409, "Rule is linked to a different collection item")
        rules[found_rule] = rule
    _validate_connector(adapter, document)
    _safe_document({"rules": rules})

    # Lock in a consistent order, check both versions, then commit both revisions.
    # Readers never observe a partial definition.
    docs = {adapter: document, "rules": {"rules": rules}}
    expected = {adapter: payload.connector_version, "rules": payload.rules_version}
    staged = {}
    for name in sorted(docs):
        row = db.query(ManagedConfiguration).filter_by(name=name).with_for_update().one_or_none()
        if (row.version if row else 0) != expected[name]:
            db.rollback()
            raise HTTPException(409, f"{name} changed in another session. Reload before saving.")
        staged[name] = row
    for name in sorted(docs):
        row = staged[name]
        if row is None:
            row = ManagedConfiguration(name=name, document=docs[name], version=1, updated_by=user.id)
            db.add(row)
        else:
            row.version += 1
            row.document = docs[name]
            row.updated_by = user.id
        db.add(ManagedConfigurationRevision(name=name, version=row.version,
                                            document=deepcopy(docs[name]), updated_by=user.id))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Configuration changed in another session. Reload before saving.") from exc
    version = expected["rules"] + 1
    backfill = _queue_rule_backfill(db, rule, version) if payload.apply_existing else None
    return {"rules_version": version, "connector_version": expected[adapter] + 1,
            "rule": rule, "backfill": backfill}


def _validate_regex(pattern: str, key: str):
    """Reject common catastrophic patterns before admins can publish them.

    This deliberately excludes nested repetitions, repeated alternations and
    backreferences. Existing YAML is unchanged until a rule is edited.
    """
    if len(pattern) > 512:
        raise HTTPException(400, f"{key} must be under 512 characters")
    try:
        parsed = re._parser.parse(pattern, 0)
    except re.error as exc:
        raise HTTPException(400, f"Invalid {key}: {exc}") from exc

    def check(items, repeated=False):
        unbounded = 0
        for op, value in items:
            if op in (re._parser.MAX_REPEAT, re._parser.MIN_REPEAT):
                if repeated:
                    raise HTTPException(400, f"{key}: nested repetition can stall ingestion")
                if value[1] == re._parser.MAXREPEAT:
                    unbounded += 1
                check(value[2], repeated=True)
            elif op == re._parser.SUBPATTERN:
                check(value[-1], repeated)
            elif op == re._parser.BRANCH:
                if repeated:
                    raise HTTPException(400, f"{key}: repeated alternatives can stall ingestion")
                for branch in value[1]:
                    check(branch, repeated)
            elif op in (re._parser.GROUPREF, re._parser.GROUPREF_EXISTS):
                raise HTTPException(400, f"{key}: backreferences are unsupported")
            elif op in (re._parser.ASSERT, re._parser.ASSERT_NOT):
                check(value[1], repeated)
        if unbounded > 1:
            raise HTTPException(400, f"{key}: use one unbounded repetition at most")
    check(parsed)


def _validate_rule(rule: dict, db: Session):
    if not isinstance(rule.get("id"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,96}", rule["id"]):
        raise HTTPException(400, "Rule ID must be 1–96 letters, numbers, underscores or hyphens")
    when = rule.get("when")
    if not isinstance(when, dict) or not isinstance(when.get("source"), str) or not when["source"].strip():
        raise HTTPException(400, "Choose a source for every rule")
    unknown = set(when) - _KNOWN_WHEN_KEYS
    if unknown:
        raise HTTPException(400, f"Unsupported match fields: {', '.join(sorted(unknown))}")
    if when.get("collector"):
        try:
            identity = json.loads(when["collector"])
            if (not isinstance(identity, list) or len(identity) != 3
                    or any(not isinstance(v, str) or not v or len(v) > 512 for v in identity)
                    or not _editable_collector(identity[0], identity[1])
                    or when["source"] != (f"webhook:{identity[2]}" if identity[0] == "webhooks" else identity[0])):
                raise ValueError("Invalid collection identity")
            collector_pointer_filter(when["collector"])
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise HTTPException(400, "Invalid collection identity or source") from exc
    for key, value in when.items():
        if key in ("severity", "bookstack_book_id", "bookstack_page_id"):
            upper = 1000 if key == "severity" else 2147483647
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= upper:
                raise HTTPException(400, f"{key} must be an integer from 0 to {upper}")
        elif not isinstance(value, str) or len(value) > 512:
            raise HTTPException(400, f"{key} must be text under 512 characters")
        elif key.endswith("_regex"):
            _validate_regex(value, key)
    targets = rule.get("map_to")
    if not isinstance(targets, list) or not targets or len(targets) > 100:
        raise HTTPException(400, "Select at least one framework control or clause")
    for target in targets:
        if (not isinstance(target, dict) or not isinstance(target.get("framework"), str)
                or not isinstance(target.get("ref"), str) or not target["framework"] or not target["ref"]
                or len(target["framework"]) > 64 or len(target["ref"]) > 64):
            raise HTTPException(400, "Each target needs a framework and reference")
        if not db.query(ControlItem.id).filter_by(framework_slug=target["framework"], ref=target["ref"]).first():
            raise HTTPException(400, f"Unknown target: {target['framework']} / {target['ref']}")
    try:
        confidence = float(rule.get("confidence", .8))
    except (TypeError, ValueError):
        confidence = -1
    if not 0 <= confidence <= 1:
        raise HTTPException(400, "Confidence must be between 0 and 1")
    parsed = parse_rules({"rules": [rule]})
    if rule.get("enabled") is not False and (len(parsed) != 1 or len(parsed[0].targets) != len(targets)):
        raise HTTPException(400, "Invalid rule targets")


@router.get("/v1/admin/mapping-rules")
def get_rules(db: Session = Depends(get_db)):
    rules = _normalized_rules(db)
    return {"version": _version(db, "rules"), "items": rules}


def _queue_rule_backfill(db: Session, rule: dict, version: int) -> dict | None:
    if rule.get("enabled") is False:
        return None
    from datetime import datetime
    from app.db.models import RuleBackfillJob
    from app.worker.tasks import backfill_rule_task

    source = rule["when"]["source"].strip()
    current = (db.query(RuleBackfillJob).filter_by(rule_id=rule["id"], rules_version=version)
               .filter(RuleBackfillJob.status.in_(["queued", "running"]))
               .order_by(RuleBackfillJob.created_at.desc()).first())
    if current:
        return {"id": str(current.id), "status": current.status,
                "total_estimate": current.total_estimate}
    cutoff = datetime.utcnow()
    exists = db.query(Event.id).filter(
        Event.source == source, Event.created_at <= cutoff).first()
    if not exists:
        return {"status": "no_previous_evidence", "total_estimate": 0}
    job = RuleBackfillJob(rule_id=rule["id"], source=source,
                          rule_document=deepcopy(rule), rules_version=version,
                          total_estimate=0, cutoff=cutoff, status="queued")
    db.add(job)
    db.commit()
    try:
        backfill_rule_task.delay(str(job.id))
    except Exception:
        # Beat retries queued jobs once the broker becomes available.
        pass
    return {"id": str(job.id), "status": "queued", "total_estimate": 0}


@router.put("/v1/admin/mapping-rules/{rule_id}")
def put_rule(rule_id: str, payload: RuleInput, user=Depends(require_admin), db: Session = Depends(get_db)):
    if payload.rule.get("id") != rule_id:
        raise HTTPException(400, "Rule ID does not match the URL")
    _validate_rule(payload.rule, db)
    rules = _normalized_rules(db)
    if len({r["id"] for r in rules}) != len(rules):
        raise HTTPException(409, "Existing rules have duplicate IDs; resolve in YAML before editing")
    found = next((i for i, item in enumerate(rules) if item["id"] == rule_id), None)
    if found is None:
        rules.append(payload.rule)
    elif payload.create_only:
        raise HTTPException(409, "A rule with this ID already exists. Choose a different ID.")
    else:
        rules[found] = payload.rule
    _safe_document({"rules": rules})
    saved = _save(db, "rules", {"rules": rules}, payload.version, user)
    backfill = _queue_rule_backfill(db, payload.rule, saved["version"]) if payload.apply_existing else None
    return {"version": saved["version"], "item": payload.rule,
            "backfill": backfill}


@router.delete("/v1/admin/mapping-rules/{rule_id}")
def delete_rule(rule_id: str, version: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    rules = _normalized_rules(db)
    remaining = [rule for rule in rules if rule["id"] != rule_id]
    if len(remaining) == len(rules):
        raise HTTPException(404, "Rule not found")
    saved = _save(db, "rules", {"rules": remaining}, version, user)
    return {"version": saved["version"]}


@router.post("/v1/admin/mapping-rules/preview")
def preview_rule(payload: RuleInput, db: Session = Depends(get_db)):
    # Validation also prevents a typo from creating placeholder controls.
    _validate_rule(payload.rule, db)
    if payload.rule.get("enabled") is False:
        return {"examined": 0, "matched": 0, "examples": [], "nonmatches": [],
                "note": "Rule is paused; enable it to preview matches."}
    rule = parse_rules({"rules": [payload.rule]})[0]
    source = payload.rule["when"]["source"]
    query = db.query(Event).filter(Event.source == source)
    if payload.rule["when"].get("collector"):
        query = query.filter(Event.raw_pointer.contains(collector_pointer_filter(payload.rule["when"]["collector"])))
    events = query.order_by(Event.timestamp.desc()).limit(200).all()
    examples = []
    nonmatches = []
    count = 0
    for event in events:
        hits = evaluate_by_framework({"source": event.source, "system": event.system,
            "actor": event.actor, "action": event.action, "outcome": event.outcome,
            "severity": event.severity, "summary": event.summary,
            "normalized_payload": event.normalized_payload, "raw_pointer": event.raw_pointer}, [rule])
        if hits:
            count += 1
            if len(examples) < 10:
                examples.append({"event_id": str(event.id), "summary": event.summary[:180], "targets": hits})
        elif len(nonmatches) < 10:
            nonmatches.append({"event_id": str(event.id), "summary": event.summary[:180],
                               "system": event.system, "action": event.action, "outcome": event.outcome})
    return {"examined": len(events), "matched": count, "examples": examples,
            "nonmatches": nonmatches,
            "note": "Recent sample only. Saving affects new evidence; remap existing evidence separately."}

@router.get("/v1/admin/evidence-event-samples")
def evidence_event_samples(source: str | None = None, collector: str | None = None,
                           db: Session = Depends(get_db)):
    """Expose the event fields used by exact-match rule conditions."""
    if not source:
        sources = [slug for (slug,) in db.query(Event.source).distinct().order_by(Event.source).limit(100).all()]
        return {"sources": sources, "items": []}
    source = source.strip()[:128]
    query = db.query(Event).filter(Event.source == source)
    if collector:
        try:
            adapter, _, _ = json.loads(collector)
            if adapter != source and not (adapter == "webhooks" and source.startswith("webhook:")):
                raise ValueError("Source and collection item differ")
            query = query.filter(Event.raw_pointer.contains(collector_pointer_filter(collector)))
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise HTTPException(400, "Invalid collection item") from exc
    rows = query.order_by(Event.timestamp.desc()).limit(20).all()
    return {"items": [{"id": str(row.id), "source": row.source,
                       "system": row.system, "actor": row.actor,
                       "action": row.action, "outcome": row.outcome,
                       "severity": row.severity, "summary": row.summary[:180]}
                      for row in rows]}

class RestoreInput(BaseModel):
    version: int  # current revision, for optimistic concurrency


@router.get("/v1/admin/configuration-revisions/{name}")
def list_revisions(name: str, db: Session = Depends(get_db)):
    if name != "rules" and name not in CONNECTORS:
        raise HTTPException(404, "Unknown configuration")
    rows = db.query(ManagedConfigurationRevision).filter_by(name=name).order_by(
        ManagedConfigurationRevision.version.desc()).limit(50).all()
    return {"items": [{"version": row.version, "at": row.updated_at.isoformat()}
                      for row in rows]}


@router.post("/v1/admin/configuration-revisions/{name}/{revision}/restore")
def restore_revision(name: str, revision: int, payload: RestoreInput,
                     user=Depends(require_admin), db: Session = Depends(get_db)):
    if name != "rules" and name not in CONNECTORS:
        raise HTTPException(404, "Unknown configuration")
    row = db.query(ManagedConfigurationRevision).filter_by(name=name, version=revision).one_or_none()
    if row is None:
        raise HTTPException(404, "Revision not found")
    if name == "rules":
        _safe_document(row.document)
    else:
        _validate_connector(name, row.document)
    if name in CONNECTORS:
        _check_linked_collectors(name, row.document, db)
    saved = _save(db, name, deepcopy(row.document), payload.version, user)
    return {"version": saved["version"]}

class PruneInput(BaseModel):
    version: int
    source: str
    mapping_ids: list[str]


def _event_for_rules(event: Event) -> dict[str, Any]:
    return {"source": event.source, "system": event.system, "actor": event.actor,
            "action": event.action, "outcome": event.outcome, "severity": event.severity,
            "summary": event.summary, "raw_pointer": event.raw_pointer,
            "normalized_payload": event.normalized_payload}


def _stale(mapping, event, control, rules) -> bool:
    matched = evaluate_by_framework(_event_for_rules(event), rules)
    return control.ref not in matched.get(control.framework_slug, [])


@router.get("/v1/admin/stale-rule-mappings")
def preview_stale_rule_mappings(source: str, offset: int = 0, db: Session = Depends(get_db)):
    """Review one bounded page of mappings; never include manual mappings."""
    source = source.strip()
    if not source or len(source) > 128 or not 0 <= offset <= 100_000:
        raise HTTPException(400, "Choose a source and an offset between 0 and 100000")
    from app.db.models import Mapping
    from app.mapping.rules import load_rules
    rules = load_rules(settings.rules_path, db=db)
    query = (db.query(Mapping, Event, ControlItem)
             .join(Event, Event.id == Mapping.event_id)
             .join(ControlItem, ControlItem.id == Mapping.control_item_id)
             .filter(Mapping.method == "rule", Event.source == source)
             .order_by(Event.timestamp.desc(), Mapping.id)
             .offset(offset).limit(500))
    stale = []
    count = 0
    for mapping, event, control in query:
        count += 1
        if _stale(mapping, event, control, rules):
            stale.append({"mapping_id": str(mapping.id), "event_id": str(event.id),
                          "summary": event.summary[:180], "framework": control.framework_slug,
                          "ref": control.ref})
    return {"source": source, "version": _version(db, "rules"), "offset": offset,
            "examined": count, "stale": stale, "next_offset": offset + count if count == 500 else None}


@router.post("/v1/admin/stale-rule-mappings/prune")
def prune_stale_rule_mappings(payload: PruneInput, db: Session = Depends(get_db)):
    """Recheck reviewed IDs against current rules before removing rule mappings."""
    if _version(db, "rules") != payload.version:
        raise HTTPException(409, "Rules changed since the review. Preview again.")
    if not payload.source.strip() or not 1 <= len(payload.mapping_ids) <= 500:
        raise HTTPException(400, "Review 1–500 mapping IDs for a source first")
    import uuid
    try:
        ids = [uuid.UUID(value) for value in payload.mapping_ids]
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Invalid mapping ID") from exc
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "Duplicate mapping IDs")
    from app.db.models import Mapping
    from app.mapping.rules import load_rules
    from app.services.control_evidence_stats import clear_stats_caches, rebuild_framework_event_stats
    rules = load_rules(settings.rules_path, db=db)
    rows = (db.query(Mapping, Event, ControlItem)
            .join(Event, Event.id == Mapping.event_id)
            .join(ControlItem, ControlItem.id == Mapping.control_item_id)
            .filter(Mapping.id.in_(ids), Mapping.method == "rule", Event.source == payload.source).all())
    if len(rows) != len(ids):
        raise HTTPException(409, "Mappings changed since the review. Preview again.")
    for mapping, event, control in rows:
        if not _stale(mapping, event, control, rules):
            raise HTTPException(409, "At least one mapping is now valid. Preview again.")
    for mapping, _, _ in rows:
        db.delete(mapping)
    db.flush()
    rebuild_framework_event_stats(db, clear_cache=False)
    db.commit()
    clear_stats_caches()
    return {"deleted": len(rows)}


@router.get("/v1/admin/rule-backfills/{job_id}")
def get_rule_backfill(job_id: str, db: Session = Depends(get_db)):
    import uuid
    from app.db.models import RuleBackfillJob
    try:
        job = db.get(RuleBackfillJob, uuid.UUID(job_id))
    except ValueError:
        job = None
    if not job:
        raise HTTPException(404, "Backfill not found")
    return {"id": str(job.id), "rule_id": job.rule_id, "source": job.source,
            "status": job.status, "total_estimate": job.total_estimate,
            "examined": job.examined, "matched": job.matched,
            "created_mappings": job.created_mappings, "error": job.error}


@router.post("/v1/admin/rule-backfills/{job_id}/cancel")
def cancel_rule_backfill(job_id: str, db: Session = Depends(get_db)):
    import uuid
    from app.db.models import RuleBackfillJob
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid job ID") from exc
    job = db.query(RuleBackfillJob).filter_by(id=job_uuid).with_for_update().one_or_none()
    if not job:
        raise HTTPException(404, "Backfill not found")
    if job.status in ("queued", "running"):
        job.status = "cancelled"
        db.commit()
    return {"status": job.status}


@router.post("/v1/admin/mapping-rules/{rule_id}/backfill")
def start_rule_backfill(rule_id: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    rule = next((item for item in _normalized_rules(db) if item["id"] == rule_id), None)
    if not rule:
        raise HTTPException(404, "Rule not found")
    _validate_rule(rule, db)
    if rule.get("enabled") is False:
        raise HTTPException(400, "Enable this rule before applying it to older evidence")
    result = _queue_rule_backfill(db, rule, _version(db, "rules"))
    return {"backfill": result}

@router.get("/v1/admin/rule-backfills")
def list_rule_backfills(rule_id: str | None = None, db: Session = Depends(get_db)):
    from app.db.models import RuleBackfillJob
    query = db.query(RuleBackfillJob)
    if rule_id:
        query = query.filter(RuleBackfillJob.rule_id == rule_id)
    jobs = query.order_by(RuleBackfillJob.created_at.desc()).limit(20).all()
    return {"items": [{"id": str(job.id), "rule_id": job.rule_id,
                       "status": job.status, "total_estimate": job.total_estimate,
                       "examined": job.examined, "matched": job.matched,
                       "created_mappings": job.created_mappings, "error": job.error}
                      for job in jobs]}
