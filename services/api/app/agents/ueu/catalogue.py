"""UEU event catalogues.

A catalogue is the implementor's vocabulary: its event types, their class
(observation or event), the typed fields each may carry and the fields each
must never carry. KEEN ships its own catalogue; operators may add catalogues
for other producers in ``KEEN_UEU_CATALOGUE_DIR``. Envelopes name the
catalogue they follow, and envelopes that name an unknown catalogue are
accepted with structural checks only.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.agents.ueu.jcs import MAX_SAFE_INTEGER

MAX_STRING = 1024
MAX_LIST_ITEMS = 64
MAX_LIST_STRING = 256
MAX_FIELDS = 48
MAX_CATALOGUE_BYTES = 1024 * 1024
FIELD_NAME = re.compile(r"[a-z][a-z0-9_.]{0,63}")
EVENT_TYPE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,4}")
CATALOGUE_ID = re.compile(r"[a-z][a-z0-9_.-]{0,31}")
CATALOGUE_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z_.-]{0,31}")
TYPES = {"boolean", "integer", "string", "timestamp", "string_list"}
SENDABLE = {"", "evidence_metadata", "operational", "security_sensitive"}
POLICIES = {"forbidden", "optional", "required"}
BUILTIN = Path(__file__).with_name("catalogues")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


class CatalogueError(ValueError):
    pass


@dataclass(frozen=True)
class FieldSpec:
    type: str
    classification: str = ""


@dataclass(frozen=True)
class EventType:
    name: str
    cls: str
    object_type: str
    summary: str
    subject: str
    payload: str
    additional_facts: str
    additional_attributes: str
    facts: dict[str, FieldSpec]
    attributes: dict[str, FieldSpec]
    prohibited: frozenset[str]


@dataclass(frozen=True)
class Catalogue:
    id: str
    version: str
    description: str
    event_types: dict[str, EventType] = field(default_factory=dict)
    # Where KEEN found the file, and its bytes as loaded, for publication.
    origin: str = "built-in"
    content: bytes = field(default=b"", repr=False, compare=False)

    @property
    def ref(self) -> str:
        return self.id + "@" + self.version


def _fields(name, raw, policy):
    if policy not in ("", "reject", "scalar_string"):
        raise CatalogueError(f"{name}: unknown additional field policy")
    if not isinstance(raw, dict):
        raise CatalogueError(f"{name}: fields must be an object")
    out = {}
    for key, spec in raw.items():
        if not FIELD_NAME.fullmatch(key) or not isinstance(spec, dict):
            raise CatalogueError(f"{name}: invalid field {key!r}")
        if set(spec) - {"type", "classification"}:
            raise CatalogueError(f"{name}.{key}: unknown field property")
        kind, classification = spec.get("type"), spec.get("classification", "")
        if kind not in TYPES:
            raise CatalogueError(f"{name}.{key}: unknown type")
        if classification not in SENDABLE:
            raise CatalogueError(f"{name}.{key}: classification cannot be sent")
        out[key] = FieldSpec(kind, classification)
    return out


def parse_catalogue(data: bytes) -> Catalogue:
    if len(data) > MAX_CATALOGUE_BYTES:
        raise CatalogueError("catalogue too large")
    doc = json.loads(data)
    if not isinstance(doc, dict) or set(doc) - {
        "catalogue",
        "classifications",
        "event_types",
    }:
        raise CatalogueError("unexpected catalogue members")
    head = doc.get("catalogue") or {}
    ident, version = head.get("id", ""), head.get("version", "")
    if not (
        isinstance(ident, str)
        and CATALOGUE_ID.fullmatch(ident)
        and isinstance(version, str)
        and CATALOGUE_VERSION.fullmatch(version)
    ):
        raise CatalogueError("catalogue needs a valid id and version")
    types = {}
    allowed = {
        "class",
        "object_type",
        "summary",
        "subject",
        "payload",
        "additional_facts",
        "additional_attributes",
        "facts",
        "attributes",
        "prohibited",
    }
    for name, raw in (doc.get("event_types") or {}).items():
        if not EVENT_TYPE.fullmatch(name) or not isinstance(raw, dict):
            raise CatalogueError(f"invalid event type {name!r}")
        if set(raw) - allowed:
            raise CatalogueError(f"{name}: unknown property")
        if raw.get("class") not in ("observation", "event"):
            raise CatalogueError(f"{name}: class must be observation or event")
        if raw.get("subject") not in POLICIES or raw.get("payload") not in POLICIES:
            raise CatalogueError(f"{name}: subject and payload need a policy")
        prohibited = raw.get("prohibited", [])
        if not isinstance(prohibited, list) or not all(
            isinstance(p, str) for p in prohibited
        ):
            raise CatalogueError(f"{name}: prohibited must be a list of names")
        summary = raw.get("summary", "")
        object_type = raw.get("object_type", "")
        if not isinstance(summary, str) or len(summary) > 256:
            raise CatalogueError(f"{name}: summary template too long")
        if not isinstance(object_type, str) or not FIELD_NAME.fullmatch(object_type):
            raise CatalogueError(f"{name}: invalid object type")
        types[name] = EventType(
            name=name,
            cls=raw["class"],
            object_type=object_type,
            summary=summary,
            subject=raw["subject"],
            payload=raw["payload"],
            additional_facts=raw.get("additional_facts", ""),
            additional_attributes=raw.get("additional_attributes", ""),
            facts=_fields(name, raw.get("facts", {}), raw.get("additional_facts", "")),
            attributes=_fields(
                name, raw.get("attributes", {}), raw.get("additional_attributes", "")
            ),
            prohibited=frozenset(prohibited),
        )
    if not types:
        raise CatalogueError("catalogue has no event types")
    description = head.get("description", "")
    return Catalogue(ident, version, str(description)[:1024], types)


def _directory():
    return os.getenv("KEEN_UEU_CATALOGUE_DIR", "/app/config/ueu/catalogues")


@lru_cache(maxsize=4)
def _load(directory: str) -> dict[tuple[str, str], Catalogue]:
    found = {}
    paths = sorted(BUILTIN.glob("*.json"))
    extra = Path(directory)
    if extra.is_dir():
        paths += sorted(p for p in extra.glob("*.json") if p.is_file())
    for path in paths:
        data = path.read_bytes()
        catalogue = replace(
            parse_catalogue(data),
            origin="built-in" if path.parent == BUILTIN else "deployment",
            content=data,
        )
        key = (catalogue.id, catalogue.version)
        if key in found:
            raise CatalogueError(f"catalogue {catalogue.ref} is defined twice")
        found[key] = catalogue
    return found


def catalogues() -> dict[tuple[str, str], Catalogue]:
    return _load(_directory())


def lookup(ident: str, version: str) -> Catalogue | None:
    return catalogues().get((ident, version))


def keen() -> Catalogue:
    """KEEN's own catalogue, which KEEN agents are granted."""
    return catalogues()[("keen", "2026.10")]


def typed_ok(kind: str, value: Any) -> bool:
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER
        )
    if kind == "string":
        return isinstance(value, str) and len(value.encode("utf-8")) <= MAX_STRING
    if kind == "timestamp":
        if not isinstance(value, str) or len(value) > 64:
            return False
        if not _TIMESTAMP.fullmatch(value):
            return False
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
        return True
    if kind == "string_list":
        return (
            isinstance(value, list)
            and len(value) <= MAX_LIST_ITEMS
            and all(
                isinstance(v, str) and len(v.encode("utf-8")) <= MAX_LIST_STRING
                for v in value
            )
        )
    return False


def check_fields(
    values: Any,
    specs: dict[str, FieldSpec] | None,
    additional: str,
    prohibited: frozenset[str],
) -> str | None:
    """Return a UEU error code when typed fields break their declaration.

    With ``specs`` None (an unknown catalogue) only names, scalar types and
    sizes are checked.
    """
    if not isinstance(values, dict):
        return "UEU_SCHEMA_VALIDATION_FAILED"
    if len(values) > MAX_FIELDS:
        return "UEU_SCHEMA_VALIDATION_FAILED"
    for key, value in values.items():
        if key in prohibited:
            return "UEU_PROHIBITED_FIELD_DETECTED"
        if not FIELD_NAME.fullmatch(key):
            return "UEU_SCHEMA_VALIDATION_FAILED"
        if specs is None:
            if not any(typed_ok(k, value) for k in TYPES):
                return "UEU_SCHEMA_VALIDATION_FAILED"
            continue
        spec = specs.get(key)
        if spec is None:
            if additional != "scalar_string":
                return "UEU_SCHEMA_VALIDATION_FAILED"
            spec = FieldSpec("string")
        if not typed_ok(spec.type, value):
            return "UEU_SCHEMA_VALIDATION_FAILED"
    return None
