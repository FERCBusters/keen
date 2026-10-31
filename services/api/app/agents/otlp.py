"""Bounded OTLP/HTTP JSON log conversion. Attributes are data, never executable."""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.agents.schema import AgentEvent

NAMESPACE = uuid.UUID("bb62b969-9e11-4f40-aefe-784f8c2577e3")
# A request carries at most 256 UEU envelopes and at most 64 plain log records.
MAX_RECORDS = 256


def value(v, depth=0):
    if not isinstance(v, dict) or depth > 8:
        raise ValueError("Invalid AnyValue")
    if not v:
        return None
    if len(v) != 1:
        raise ValueError("Invalid AnyValue union")
    kind, x = next(iter(v.items()))
    if kind == "stringValue" and isinstance(x, str):
        return x
    if kind == "boolValue" and isinstance(x, bool):
        return x
    if kind == "intValue" and isinstance(x, (str, int)) and not isinstance(x, bool):
        return int(x)
    if (
        kind == "doubleValue"
        and isinstance(x, (float, int))
        and not isinstance(x, bool)
    ):
        return float(x)
    if kind == "bytesValue" and isinstance(x, str):
        return {"base64": x}
    if kind == "arrayValue" and isinstance(x, dict):
        items = x.get("values", [])
        if not isinstance(items, list) or len(items) > 128:
            raise ValueError("Array too large")
        return [value(i, depth + 1) for i in items]
    if kind == "kvlistValue" and isinstance(x, dict):
        return attributes(x.get("values", []), depth + 1)
    raise ValueError("Invalid AnyValue")


def attributes(items, depth=0):
    if not isinstance(items, list) or len(items) > 128:
        raise ValueError("Too many attributes")
    out = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Invalid attribute")
        k = item.get("key")
        v = item.get("value", {})
        if not isinstance(k, str) or len(k) > 128 or k in out:
            raise ValueError("Invalid or duplicate attribute key")
        out[k] = value(v, depth)
    return out


def string(x):
    return (
        x
        if isinstance(x, str)
        else json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )


@dataclass
class UeuRecord:
    body: str
    evidence_id: str
    event_name: str


@dataclass
class UeuResource:
    """One resource's UEU records and the batch attributes that sign them."""

    attributes: dict
    records: list = field(default_factory=list)


@dataclass
class Batch:
    events: list
    ueu: list

    @property
    def size(self):
        return len(self.events) + sum(len(r.records) for r in self.ueu)


def ueu_record(record, a):
    """A UEU record carries the canonical envelope as its string body."""
    body = record.get("body", {})
    if not isinstance(body, dict) or set(body) != {"stringValue"}:
        raise ValueError("UEU records carry the envelope as a string body")
    text = body["stringValue"]
    evidence_id, name = a.get("ueu.evidence_id"), a.get("event.name")
    if not isinstance(text, str) or not isinstance(evidence_id, str):
        raise ValueError("UEU records name their evidence")
    return UeuRecord(text, evidence_id, name if isinstance(name, str) else "")


def decode_batch(data, agent_id):
    """Split an OTLP/HTTP JSON logs request into UEU envelopes and log events."""
    document = json.loads(data)
    if not isinstance(document, dict):
        raise ValueError("Expected OTLP object")
    resources = document.get("resourceLogs", [])
    if not isinstance(resources, list) or len(resources) > 64:
        raise ValueError("Resource limit")
    out: list[AgentEvent] = []
    ueu = []
    count = 0
    for resource in resources:
        rattrs = attributes(resource.get("resource", {}).get("attributes", []))
        scopes = resource.get("scopeLogs", [])
        if not isinstance(scopes, list) or len(scopes) > 64:
            raise ValueError("Scope limit")
        signed = UeuResource(rattrs)
        for scope in scopes:
            records = scope.get("logRecords", [])
            if not isinstance(records, list):
                raise ValueError("Expected records")
            for record in records:
                count += 1
                if count > MAX_RECORDS:
                    raise ValueError("Batch exceeds the record limit")
                a = attributes(record.get("attributes", []))
                if "ueu.spec_version" in a:
                    signed.records.append(ueu_record(record, a))
                    continue
                if len(out) >= 64:
                    raise ValueError("Batch exceeds 64 records")
                raw = string(value(record.get("body", {})))
                ns = int(record.get("timeUnixNano") or 0) or int(
                    record.get("observedTimeUnixNano") or 0
                )
                if ns <= 0:
                    raise ValueError(
                        "Records require timeUnixNano or observedTimeUnixNano"
                    )
                ts = datetime.fromtimestamp(ns // 1000000000, timezone.utc).replace(
                    microsecond=(ns % 1000000000) // 1000
                )
                identity = json.dumps(
                    {
                        "resource": rattrs,
                        "scope": scope.get("scope", {}),
                        "record": {**record, "attributes": a},
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                record_id = str(
                    a.get("keen.event.id") or uuid.uuid5(NAMESPACE, agent_id + identity)
                )
                fields = {}
                for k, v in rattrs.items():
                    fields["resource." + k] = string(v)
                for k, v in a.items():
                    if k.startswith("keen.field."):
                        key = k[11:]
                        if key.startswith(("resource.", "otel.")):
                            raise ValueError("Reserved enrichment name")
                        fields[key] = string(v)
                    elif not k.startswith("keen."):
                        fields[k] = string(v)
                fields["otel.time_unix_nano"] = str(ns)
                for k in ("traceId", "spanId", "severityText", "observedTimeUnixNano"):
                    if k in record:
                        fields["otel." + k] = string(record[k])
                fields["otel.scope"] = string(scope.get("scope", {}))
                # Keep a canonical source digest so synthetic UUID conflicts never depend
                # on receive time or server normalization details.
                fields["otel.record_sha256"] = hashlib.sha256(
                    identity.encode()
                ).hexdigest()
                source = string(a.get("keen.source", "otlp"))
                event = AgentEvent(
                    id=record_id,
                    timestamp=ts,
                    source=source,
                    raw=raw,
                    action=string(a.get("keen.action", "log.record")),
                    outcome=string(a.get("keen.outcome", "info")),
                    actor=string(a["keen.actor"]) if a.get("keen.actor") else None,
                    severity=min(10, max(0, int(record.get("severityNumber", 0)) // 2)),
                    summary=string(a.get("keen.summary", raw or "OTLP log record"))[
                        :4096
                    ],
                    fields=fields,
                )
                out.append(event)
        if signed.records:
            ueu.append(signed)
        elif any(k.startswith("ueu.batch.") for k in rattrs):
            raise ValueError("A signed batch carries UEU records only")
    if len({e.id for e in out}) != len(out):
        # Identical retried records inside a request are accepted once.
        unique: dict[str, AgentEvent] = {}
        for e in out:
            key = str(e.id)
            if key in unique and unique[key] != e:
                raise ValueError("Conflicting record UUIDs")
            unique[key] = e
        out = list(unique.values())
    return Batch(out, ueu)
