"""Bounded, literal JSON-key paths for evidence conditions (no expressions)."""

import ipaddress
import json

from app.security.regex import compile_pattern

OPS = {
    "regex",
    "equals",
    "starts_with",
    "contains",
    "not_contains",
    "exists",
    "in_cidr",
}


def validate_fields(items):
    if not isinstance(items, list) or len(items) > 20:
        raise ValueError("Use at most 20 structured field conditions")
    for item in items:
        if not isinstance(item, dict) or set(item) - {
            "path",
            "operator",
            "value",
            "scope",
        }:
            raise ValueError("Invalid structured field condition")
        path = item.get("path")
        if (
            not isinstance(path, list)
            or not 1 <= len(path) <= 8
            or any(not isinstance(k, str) or not k or len(k) > 256 for k in path)
        ):
            raise ValueError("Field path must contain 1-8 literal JSON keys")
        if item.get("scope", "payload") not in ("payload", "event"):
            raise ValueError("Unsupported field scope")
        if item.get("scope") == "event" and path != ["summary"]:
            raise ValueError("Only Summary is supported as an event field")
        if item.get("operator") not in OPS:
            raise ValueError("Unsupported structured field operator")
        if item["operator"] != "exists" and (
            not isinstance(item.get("value"), str) or len(item["value"]) > 4096
        ):
            raise ValueError("Field match value must be text up to 4096 characters")
        if item["operator"] == "regex":
            compile_pattern(item["value"])
        if item["operator"] == "in_cidr":
            try:
                ipaddress.ip_network(item["value"], strict=False)
            except ValueError as exc:
                raise ValueError("Enter a valid IPv4 or IPv6 CIDR") from exc
    return items


def field_matches(payload, condition):
    value = payload
    for key in condition["path"]:
        if not isinstance(value, dict) or key not in value:
            return False
        value = value[key]
    if condition["operator"] == "exists":
        return True
    if value is None or isinstance(value, (dict, list)):
        return False
    text = value if isinstance(value, str) else json.dumps(value, allow_nan=False)
    expected = condition["value"]
    if condition["operator"] == "regex":
        return bool(compile_pattern(expected).search(text))
    if condition["operator"] == "in_cidr":
        try:
            if "%" in text:
                return False
            return ipaddress.ip_address(text) in ipaddress.ip_network(
                expected, strict=False
            )
        except ValueError:
            return False
    return {
        "equals": lambda: text == expected,
        "starts_with": lambda: text.startswith(expected),
        "contains": lambda: expected in text,
        "not_contains": lambda: expected not in text,
    }[condition["operator"]]()


def observed_fields(payload):
    result: list[dict] = []

    def visit(value, path):
        if len(result) >= 100 or len(path) > 8:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str) and 0 < len(key) <= 256:
                    visit(child, path + [key])
        elif path and value is not None and not isinstance(value, list):
            text = value if isinstance(value, str) else json.dumps(value)
            if len(text) <= 4096:
                result.append({"path": path, "value": text})

    visit(payload, [])
    return result
