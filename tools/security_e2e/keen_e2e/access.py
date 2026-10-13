"""Build concrete requests for every API operation and reason about permission sets.

The access-policy check sends each operation with real identifiers where the
installation has them, and with a minimal valid body, so that a request reaches
the permission check instead of stopping at validation or a missing record.
"""

from __future__ import annotations

import itertools
import uuid

ID_KEYS = ("id", "ref", "key", "name", "slug", "framework_slug", "control_ref")

# Path parameters whose value names a kind of record rather than identifying one.
FIXED_VALUES = {
    "entity_type": "risk",
    "target_type": "risk",
    "version": "1",
    "kind": "books",
}


def _first_identifier(body):
    items = body
    if isinstance(body, dict):
        for key in (
            "items",
            "results",
            "data",
            "controls",
            "clauses",
            "frameworks",
            "groups",
        ):
            if isinstance(body.get(key), list):
                items = body[key]
                break
        else:
            items = [body]
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ID_KEYS:
            value = item.get(key)
            if isinstance(value, (str, int)) and str(value):
                return str(value)
    return None


class Resolver:
    """Find a real identifier for a path parameter by listing its collection."""

    def __init__(self, admin, known=None):
        self.admin = admin
        self.known = dict(known or {})
        self.cache = {}

    def collection_value(self, prefix):
        if prefix not in self.cache:
            value = None
            try:
                response = self.admin.get("/api" + prefix)
                if response.status_code == 200:
                    value = _first_identifier(response.json())
            except ValueError:
                value = None
            self.cache[prefix] = value
        return self.cache[prefix]

    def fill(self, path):
        parts = path.split("/")
        out = []
        for index, part in enumerate(parts):
            if not (part.startswith("{") and part.endswith("}")):
                out.append(part)
                continue
            name = part[1:-1]
            prefix = "/".join(out)
            value = (
                self.known.get(prefix)
                or FIXED_VALUES.get(name)
                or (
                    self.known.get("entity")
                    if name in ("entity_id", "target_id")
                    else None
                )
                or self.collection_value(prefix)
            )
            if value is None:
                value = str(uuid.uuid4()) if name.endswith("id") else "keen-e2e"
            out.append(str(value))
        return "/".join(out)


def _resolve(schema, root):
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 20:
        schema = root["components"]["schemas"][schema["$ref"].split("/")[-1]]
        seen += 1
    return schema or {}


def minimal_value(schema, root, depth=0):
    """A value that satisfies a JSON schema's required structure."""
    schema = _resolve(schema, root)
    if depth > 6:
        return None
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [
                o for o in schema[key] if _resolve(o, root).get("type") != "null"
            ]
            return minimal_value((options or schema[key])[0], root, depth + 1)
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        required = schema.get("required", [])
        return {
            name: minimal_value(value, root, depth + 1)
            for name, value in schema.get("properties", {}).items()
            if name in required
        }
    if kind == "array":
        return []
    if kind in ("integer", "number"):
        return max(1, schema.get("minimum", 1))
    if kind == "boolean":
        return False
    return {
        "uuid": str(uuid.uuid4()),
        "date": "2026-01-01",
        "date-time": "2026-01-01T00:00:00Z",
        "email": "keen-e2e@example.org",
        "uri": "https://example.org/",
    }.get(schema.get("format"), "keen-e2e")


def request_kwargs(operation, root):
    kwargs = {}
    query = {
        p["name"]: minimal_value(p.get("schema", {}), root)
        for p in operation.get("parameters", [])
        if p.get("in") == "query" and p.get("required")
    }
    if query:
        kwargs["params"] = query
    content = operation.get("requestBody", {}).get("content", {})
    if "application/json" in content:
        kwargs["json"] = minimal_value(
            content["application/json"].get("schema", {}), root
        )
    elif "multipart/form-data" in content:
        form = _resolve(content["multipart/form-data"].get("schema", {}), root)
        files, data = {}, {}
        for name, field in form.get("properties", {}).items():
            field = _resolve(field, root)
            if field.get("format") == "binary" or field.get("contentMediaType"):
                files[name] = ("keen-e2e.txt", b"keen-e2e", "text/plain")
            elif name in form.get("required", []):
                data[name] = str(minimal_value(field, root))
        if files:
            kwargs["files"] = files
        if data:
            kwargs["data"] = data
    return kwargs


def denied_sets(alternatives, every_code):
    """The largest permission sets that satisfy none of the alternatives.

    Each alternative is a set of permissions that together grant access. Removing
    one permission from every alternative (a hitting set) leaves a set that must
    be refused; testing the largest such sets checks every other permission too.
    """
    if not alternatives:
        return [frozenset(every_code)]
    results = set()
    for removal in itertools.product(*[sorted(a) for a in alternatives]):
        results.add(frozenset(every_code) - set(removal))
    return sorted(results, key=sorted)
