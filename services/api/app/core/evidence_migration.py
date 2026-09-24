"""Convert configured evidence collection and mappings into the unified model.

Pure transformations keep the database migration testable without network calls.
Broad rules remain source-wide definitions: guessing a specific job or query
would silently change which evidence they map.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.mapping.collector import collector_id
from app.mapping.rules import _as_rule_entries, parse_rules


SECTIONS = {
    "loki": ("queries", "name"), "jenkins": ("jobs", "name"),
    "rss": ("feeds", "url"), "cloudwatch_logs": ("queries", "name"),
    "google_workspace": ("streams", "name"), "forgejo": ("feeds", "url"),
    "taiga": ("projects", "id"),
}


def _origin_for_rule(when: dict, documents: dict[str, dict]) -> str | None:
    source = str(when.get("source") or "")
    if source.startswith("webhook:"):
        provider = source.split(":", 1)[1]
        if provider in (documents.get("webhooks", {}).get("providers") or {}):
            return collector_id("webhooks", "providers", provider)
        return None
    if source not in SECTIONS:
        return None
    section, key_field = SECTIONS[source]
    entries = documents.get(source, {}).get(section) or []
    # Only associate when the rule has an exact condition whose value is
    # definitely emitted by a single configured collection item. If a field
    # might be dynamic, or multiple items remain possible, preserve scope.
    candidates = []
    for entry in entries:
        defaults = entry.get("event", {}) if source in ("loki", "cloudwatch_logs") else entry
        if not isinstance(defaults, dict):
            continue
        if source == "jenkins":
            emitted = {"system": entry.get("label") or "jenkins", "action": entry.get("kind") or "build"}
        elif source == "google_workspace":
            emitted = {"system": entry.get("system") or entry.get("application")}
        elif source == "rss":
            emitted = {"system": entry.get("system")}
        else:
            emitted = {k: defaults.get(k) for k in ("system", "action", "outcome")}
        compared = [k for k in ("system", "action", "outcome") if when.get(k) is not None and emitted.get(k) is not None]
        if all(str(emitted[k]) == str(when[k]) for k in compared):
            identity = entry.get(key_field)
            if identity:
                candidates.append((str(identity), bool(compared)))
    if len(candidates) == 1 and candidates[0][1]:
        return collector_id(source, section, candidates[0][0])
    return None


def unify(documents: dict[str, dict], default_framework_slug: str) -> tuple[dict[str, dict], dict[str, int]]:
    docs = deepcopy(documents)
    rules = []
    ids = set()
    linked = 0
    for index, (hint, item) in enumerate(_as_rule_entries(docs.get("rules", {}))):
        rid = str(item.get("id") or f"r{index}")
        if rid in ids:
            raise ValueError(f"Duplicate rule ID {rid}; fix this before migration")
        ids.add(rid)
        # Match the ingestion parser, including framework bucket inheritance.
        context = hint or default_framework_slug
        parsed = parse_rules({"framework": context, "rules": [{**item, "enabled": True}]})
        if len(parsed) != 1 or not parsed[0].targets:
            raise ValueError(f"Cannot normalize rule {rid}; correct its targets before migration")
        parsed_rule = parsed[0]
        when = {key: val for key, val in vars(parsed_rule.when).items() if val is not None}
        if not when.get("collector"):
            origin = _origin_for_rule(when, docs)
            if origin:
                when["collector"] = origin
                linked += 1
        rules.append({"id": rid, "description": item.get("description") or "",
                      "when": when, "map_to": [
                          {"framework": target.framework_slug, "ref": target.ref,
                           **({"roles_any": sorted(target.roles_any)} if target.roles_any else {})}
                          for target in parsed_rule.targets],
                      "confidence": parsed_rule.confidence, "enabled": item.get("enabled") is not False})

    bookstack = docs.setdefault("bookstack", {})
    selectors = []
    selected = list(bookstack.get("selected_pages") or [])
    imported_pages = 0
    legacy_pages = list(bookstack.get("page_mappings") or bookstack.get("pages") or [])
    for index, item in enumerate(legacy_pages):
        if not isinstance(item, dict):
            raise ValueError(f"BookStack page mapping #{index + 1} is invalid")
        match = item.get("match", item)
        if not isinstance(match, dict):
            raise ValueError(f"BookStack page mapping #{index + 1} has no match")
        refs = item.get("map_to") or item.get("controls") or []
        if isinstance(refs, str):
            refs = [v.strip() for v in refs.split(",") if v.strip()]
        if not isinstance(refs, list) or not refs:
            raise ValueError(f"BookStack page mapping #{index + 1} has no targets")
        match = deepcopy(match)
        nested_book = match.get("book")
        if isinstance(nested_book, dict):
            match.setdefault("book_id", nested_book.get("id"))
            match.setdefault("book_slug", nested_book.get("slug") or nested_book.get("book_slug"))
        elif isinstance(nested_book, str):
            match.setdefault("book_slug", nested_book)
        for canonical, alias in (("slug", "page_slug"), ("slug_regex", "page_slug_regex"),
                                 ("title", "name"), ("title_regex", "name_regex")):
            if not match.get(canonical) and match.get(alias):
                match[canonical] = match[alias]
        selectors.append(match)
        when: dict[str, Any] = {"source": "bookstack"}
        for old, new in (("book_id", "bookstack_book_id"), ("book_slug", "bookstack_book_slug"),
                         ("id", "bookstack_page_id"), ("slug", "bookstack_page_slug"),
                         ("slug_regex", "bookstack_page_slug_regex"), ("title", "bookstack_page_title"),
                         ("title_regex", "bookstack_page_title_regex")):
            if match.get(old) is not None and match[old] != "":
                when[new] = match[old]
        if isinstance(when.get("bookstack_page_id"), str) and when["bookstack_page_id"].isdigit():
            when["bookstack_page_id"] = int(when["bookstack_page_id"])
        if isinstance(when.get("bookstack_book_id"), str) and when["bookstack_book_id"].isdigit():
            when["bookstack_book_id"] = int(when["bookstack_book_id"])
        if not any(key != "source" for key in when):
            raise ValueError(f"BookStack page mapping #{index + 1} has no usable selector")
        if isinstance(when.get("bookstack_page_id"), int):
            page_id = when["bookstack_page_id"]
            when["collector"] = collector_id("bookstack", "selected_pages", page_id)
            if all(page.get("id") != page_id for page in selected):
                selected.append({"id": page_id, **({"book_id": when["bookstack_book_id"]} if when.get("bookstack_book_id") else {}),
                                 **({"book_slug": when["bookstack_book_slug"]} if when.get("bookstack_book_slug") else {})})
        rid = f"bookstack_page_{index + 1}"
        while rid in ids:
            rid += "_mapped"
        ids.add(rid)
        rules.append({"id": rid, "description": item.get("rationale") or f"BookStack page {index + 1}",
                      "when": when, "map_to": [{"framework": item.get("framework") or default_framework_slug,
                                                "ref": str(ref)} for ref in refs],
                      "confidence": float(item.get("confidence", .95)), "enabled": True})
        imported_pages += 1
    bookstack["capture_pages"] = list(bookstack.get("capture_pages") or []) + selectors
    bookstack["selected_pages"] = selected
    bookstack["page_mappings"] = []
    bookstack.pop("pages", None)
    docs["rules"] = {"rules": rules}
    return docs, {"rules": len(rules), "linked": linked, "bookstack_pages": imported_pages}
