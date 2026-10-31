"""Redaction of validated envelopes, with a custody record of every change.

The envelope's catalogue says what each fact or attribute is. Booleans, and
integers and RFC 3339 times the catalogue declares, cannot hold a secret, so
they are kept whatever their names (``password_required: true`` is a
measurement). An undeclared integer under a sensitive name is redacted. Text is redacted
with KEEN's ordinary rules. Fields whose form the profile fixes (hashes,
identifiers, times, pseudonyms and privacy transformation records) are
already validated and are kept as they are.

When anything changes, the stored envelope no longer hashes to the evidence
hash. The custody record keeps the original hash, the paths that changed and
why, so a reviewer can tell signed content from KEEN's own changes.
"""

from __future__ import annotations

from typing import Any

from app.agents.ueu.catalogue import EventType
from app.security.redaction import REDACTED, redact_str, should_redact_value

SAFE_TYPES = {"boolean", "integer", "timestamp"}
# Members whose values the profile validator has already restricted to fixed
# forms that cannot carry free text.
FIXED = {
    ("ueu_spec_version",),
    ("evidence_id",),
    ("event_type",),
    ("event_time",),
    ("evidence_time",),
    ("catalogue", "id"),
    ("catalogue", "version"),
    ("event", "outcome"),
    ("event", "object_type"),
    ("subject", "identifier_type"),
    ("subject", "identifier"),
    ("evidence", "payload_hash"),
    ("evidence", "collection_mode"),
    ("privacy", "profile"),
    ("integrity", "canonicalisation"),
    ("integrity", "hash_algorithm"),
    ("integrity", "stream"),
    ("integrity", "previous_evidence_hash"),
    ("observation", "method"),
    ("source", "application_id"),
    ("source", "instance_id"),
}


class Custody:
    def __init__(self):
        self.changes: list[dict[str, str]] = []

    def note(self, path, reason):
        self.changes.append({"path": ".".join(path), "reason": reason})


def _text(value, path, custody, key=None):
    if key is not None and should_redact_value(key, value):
        custody.note(path, "sensitive_name")
        return REDACTED
    out = redact_str(value)
    if out != value:
        custody.note(path, "sensitive_value")
    return out


def _fields(values, specs, path, custody):
    out: dict[str, Any] = {}
    for key, value in values.items():
        here = path + (key,)
        kind = specs[key].type if specs and key in specs else None
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, int) and kind is None:
            # An undeclared number under a sensitive name (otp_code, pin) may be
            # a secret; only the catalogue's declaration makes it a measurement.
            if should_redact_value(key, str(value)):
                custody.note(here, "sensitive_name")
                out[key] = REDACTED
            else:
                out[key] = value
        elif kind in SAFE_TYPES:
            out[key] = value
        elif isinstance(value, list):
            if should_redact_value(key, value):
                custody.note(here, "sensitive_name")
                out[key] = REDACTED
            else:
                out[key] = [
                    _text(item, here + (str(i),), custody)
                    for i, item in enumerate(value)
                ]
        else:
            out[key] = _text(value, here, custody, key)
    return out


def _walk(value, path, custody):
    if path in FIXED or path[:2] == ("privacy", "transformations"):
        return value
    if isinstance(value, dict):
        return {k: _walk(v, path + (k,), custody) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, path + (str(i),), custody) for i, v in enumerate(value)]
    if isinstance(value, str):
        return _text(value, path, custody, path[-1] if path else None)
    return value


def redact(document: dict[str, Any], spec: EventType | None):
    """Return the envelope as KEEN stores it, and the list of changes."""
    custody = Custody()
    out = {}
    for key, value in document.items():
        if key == "observation":
            out[key] = {
                k: (
                    _fields(v, spec.facts if spec else None, (key, k), custody)
                    if k == "facts"
                    else _walk(v, (key, k), custody)
                )
                for k, v in value.items()
            }
        elif key == "attributes":
            out[key] = _fields(
                value, spec.attributes if spec else None, (key,), custody
            )
        else:
            out[key] = _walk(value, (key,), custody)
    return out, custody.changes


def withhold_payload(document: dict[str, Any], changes: list, reason: str):
    """Remove an included payload KEEN must not keep."""
    evidence = document.get("evidence", {})
    if evidence.get("payload_included"):
        evidence = {k: v for k, v in evidence.items() if k != "payload"}
        document["evidence"] = evidence
        changes.append({"path": "evidence.payload", "reason": reason})
