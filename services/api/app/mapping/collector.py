"""Stable identity of the configured collection item that produced an event.

The identity is derived from stored provenance, including events ingested before
the evidence editor existed. A rule can therefore target a collection item
without relying on coincidental action/system strings.
"""
from __future__ import annotations

import json
from typing import Any


def collector_id(adapter: str, section: str, key: Any) -> str:
    return json.dumps([adapter, section, str(key)], separators=(",", ":"), ensure_ascii=False)


def event_collector(event: dict) -> str | None:
    source = str(event.get("source") or "")
    pointer = event.get("raw_pointer") or {}
    if not isinstance(pointer, dict):
        return None
    info = pointer.get("webhook" if source.startswith("webhook:") else source) or {}
    if not isinstance(info, dict):
        return None
    section, key = None, None
    if source == "loki":
        section, key = "queries", info.get("query_name")
    elif source == "cloudwatch_logs":
        section, key = "queries", info.get("name")
    elif source == "jenkins":
        section, key = "jobs", info.get("job")
    elif source == "rss":
        section, key = "feeds", info.get("feed_url")
    elif source == "forgejo":
        section, key = "feeds", info.get("feed")
    elif source == "taiga":
        section, key = "projects", info.get("project_id")
    elif source == "google_workspace":
        section, key = "streams", info.get("stream")
    elif source == "bookstack":
        section, key = "selected_pages", info.get("page_id")
    elif source == "github":
        if info.get("feed"):
            section, key = "feeds", info["feed"]
        elif info.get("org"):
            section, key = "organizations", info["org"]
        elif info.get("owner") and info.get("repo"):
            section, key = "repos", f'{info["owner"]}/{info["repo"]}'
    elif source.startswith("webhook:"):
        section, key = "providers", info.get("provider")
        source = "webhooks"
    return collector_id(source, section, key) if section and key is not None else None


def collector_pointer_filter(identity: str) -> dict:
    """JSONB containment predicate for source samples and historical batches."""
    adapter, section, key = json.loads(identity)
    if adapter == "webhooks":
        return {"webhook": {"provider": key}}
    if adapter == "github":
        if section == "repos":
            owner, repo = key.split("/", 1)
            return {"github": {"endpoint": "repo_events", "owner": owner, "repo": repo}}
        return {"github": {"feed" if section == "feeds" else "org": key}}
    field = {"loki": "query_name", "cloudwatch_logs": "name", "jenkins": "job",
             "rss": "feed_url", "forgejo": "feed", "taiga": "project_id",
             "google_workspace": "stream", "bookstack": "page_id"}[adapter]
    return {adapter: {field: int(key) if adapter in ("taiga", "bookstack") else key}}
