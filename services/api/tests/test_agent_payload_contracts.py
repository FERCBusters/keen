"""Agent/OTLP validation, deterministic identity, enrichment and batch bounds."""

import copy
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.agents.otlp import attributes, decode_batch, value
from app.agents.schema import AgentEvent, Health, fingerprint
from pydantic import ValidationError


def decode(data, agent_id):
    return decode_batch(data, agent_id).events


def payload(**overrides):
    return {
        "id": str(uuid.uuid4()),
        "timestamp": "2025-01-01T00:00:00Z",
        "source": "ossec",
        "action": "ossec.alert",
        "summary": "Alert",
        **overrides,
    }


def validation_errors(error):
    """Each refusal as (type, dotted location, message)."""
    return [(e["type"], ".".join(map(str, e["loc"])), e["msg"]) for e in error.errors()]


@pytest.mark.parametrize(
    "change, error",
    [
        (
            {"source": ""},
            ("string_too_short", "source", "String should have at least 1 character"),
        ),
        (
            {"source": "space name"},
            (
                "string_pattern_mismatch",
                "source",
                "String should match pattern '^[a-zA-Z0-9_.-]+$'",
            ),
        ),
        (
            {"source": "x" * 65},
            ("string_too_long", "source", "String should have at most 64 characters"),
        ),
        (
            {"action": ""},
            ("string_too_short", "action", "String should have at least 1 character"),
        ),
        (
            {"action": "x" * 129},
            ("string_too_long", "action", "String should have at most 128 characters"),
        ),
        (
            {"summary": ""},
            ("string_too_short", "summary", "String should have at least 1 character"),
        ),
        (
            {"summary": "x" * 4097},
            (
                "string_too_long",
                "summary",
                "String should have at most 4096 characters",
            ),
        ),
        (
            {"raw": "x" * 65537},
            ("string_too_long", "raw", "String should have at most 65536 characters"),
        ),
        (
            {"severity": -1},
            (
                "greater_than_equal",
                "severity",
                "Input should be greater than or equal to 0",
            ),
        ),
        (
            {"severity": 11},
            ("less_than_equal", "severity", "Input should be less than or equal to 10"),
        ),
        (
            {"timestamp": "2025-01-01T00:00:00"},
            (
                "value_error",
                "timestamp",
                "Value error, Expected a timezone-aware event timestamp between 2000 and now + 5 minutes",
            ),
        ),
        (
            {"timestamp": "1999-12-31T23:59:59Z"},
            (
                "value_error",
                "timestamp",
                "Value error, Expected a timezone-aware event timestamp between 2000 and now + 5 minutes",
            ),
        ),
        (
            {"timestamp": datetime.now(timezone.utc) + timedelta(days=1)},
            (
                "value_error",
                "timestamp",
                "Value error, Expected a timezone-aware event timestamp between 2000 and now + 5 minutes",
            ),
        ),
        (
            {"fields": {"key": "x" * 4097}},
            ("value_error", "fields", "Value error, Fields exceed limits"),
        ),
        (
            {"fields": {"x" * 257: "ok"}},
            ("value_error", "fields", "Value error, Fields exceed limits"),
        ),
        (
            {"fields": {str(i): "x" for i in range(129)}},
            (
                "too_long",
                "fields",
                "Dictionary should have at most 128 items after validation, not 129",
            ),
        ),
        (
            {"fields": {str(i): "x" * 4096 for i in range(9)}},
            ("value_error", "fields", "Value error, Fields exceed limits"),
        ),
        (
            {"unexpected": "field"},
            ("extra_forbidden", "unexpected", "Extra inputs are not permitted"),
        ),
        (
            {"id": "bad"},
            (
                "uuid_parsing",
                "id",
                "Input should be a valid UUID, invalid length: found 3",
            ),
        ),
    ],
)
def test_invalid_event_rejected(change, error):
    with pytest.raises(ValidationError) as refused:
        AgentEvent.model_validate(payload(**change))
    assert validation_errors(refused.value) == [error]


@pytest.mark.parametrize("severity", [0, 5, 10])
def test_valid_event_boundaries_and_offsets(severity):
    event = AgentEvent.model_validate(
        payload(
            severity=severity,
            timestamp="2025-01-01T11:00:00+11:00",
            fields={"ossec.rule.id": "31101", "flag": "false"},
            summary="x" * 4096,
        )
    )
    assert event.timestamp.astimezone(timezone.utc).hour == 0
    assert event.fields["ossec.rule.id"] == "31101"


def test_fingerprint_independent_of_field_order_but_sensitive_to_content():
    data = payload(fields={"a": "1", "b": "2"})
    a = AgentEvent.model_validate(data)
    b = AgentEvent.model_validate({**data, "fields": {"b": "2", "a": "1"}})
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint(b.model_copy(update={"summary": "changed"}))


@pytest.mark.parametrize(
    "field",
    [
        "queued",
        "queue_bytes",
        "rejected",
        "delivered_since_start",
        "delivery_batches_since_start",
    ],
)
def test_negative_health_counters_rejected(field):
    with pytest.raises(ValidationError) as refused:
        Health.model_validate({field: -1})
    assert validation_errors(refused.value) == [
        ("greater_than_equal", field, "Input should be greater than or equal to 0")
    ]


@pytest.mark.parametrize(
    "item,expected",
    [
        ({}, None),
        ({"stringValue": "hi"}, "hi"),
        ({"boolValue": False}, False),
        ({"intValue": "42"}, 42),
        ({"doubleValue": 1.5}, 1.5),
        ({"bytesValue": "YQ=="}, {"base64": "YQ=="}),
        (
            {"arrayValue": {"values": [{"intValue": "1"}, {"stringValue": "x"}]}},
            [1, "x"],
        ),
        (
            {"kvlistValue": {"values": [{"key": "x", "value": {"boolValue": True}}]}},
            {"x": True},
        ),
    ],
)
def test_otlp_anyvalue_conversion(item, expected):
    assert value(item) == expected


@pytest.mark.parametrize(
    "item, message",
    [
        ([], "Invalid AnyValue"),
        (None, "Invalid AnyValue"),
        ({"unknown": 1}, "Invalid AnyValue"),
        ({"intValue": True}, "Invalid AnyValue"),
        ({"doubleValue": True}, "Invalid AnyValue"),
        ({"stringValue": 1}, "Invalid AnyValue"),
        ({"boolValue": "false"}, "Invalid AnyValue"),
        (
            {"intValue": "not integer"},
            "invalid literal for int() with base 10: 'not integer'",
        ),
        ({"stringValue": "x", "boolValue": True}, "Invalid AnyValue union"),
        ({"arrayValue": {"values": "bad"}}, "Array too large"),
        ({"arrayValue": {"values": [{}] * 129}}, "Array too large"),
    ],
)
def test_invalid_otlp_values(item, message):
    with pytest.raises(ValueError, match="^" + re.escape(message) + "$"):
        value(item)


def test_otlp_depth_and_attribute_limits():
    nested = {"stringValue": "x"}
    for _ in range(10):
        nested = {"arrayValue": {"values": [nested]}}
    with pytest.raises(ValueError, match="^Invalid AnyValue$"):
        value(nested)
    for data, message in [
        ([{"key": "a"}, {"key": "a"}], "Invalid or duplicate attribute key"),
        ([{"key": "x" * 129}], "Invalid or duplicate attribute key"),
        ([{"key": 1}], "Invalid or duplicate attribute key"),
        ([None], "Invalid attribute"),
        ([{}] * 129, "Too many attributes"),
        ({}, "Too many attributes"),
    ]:
        with pytest.raises(ValueError, match="^" + re.escape(message) + "$"):
            attributes(data)


def document(record=None):
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "host.name", "value": {"stringValue": "server"}}
                    ]
                },
                "scopeLogs": [
                    {
                        "scope": {"name": "agent"},
                        "logRecords": [
                            record
                            or {
                                "timeUnixNano": "1735689600123456789",
                                "severityNumber": 10,
                                "body": {"stringValue": "alert"},
                                "attributes": [
                                    {
                                        "key": "keen.source",
                                        "value": {"stringValue": "ossec"},
                                    },
                                    {
                                        "key": "keen.field.ossec.rule.id",
                                        "value": {"stringValue": "31101"},
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }


def test_otlp_identity_and_enrichment_are_stable_and_agent_scoped():
    raw = json.dumps(document())
    a = decode(raw, "a")[0]
    b = decode(raw, "a")[0]
    other = decode(raw, "b")[0]
    assert a == b and a.id != other.id
    assert a.fields["ossec.rule.id"] == "31101"
    assert a.fields["resource.host.name"] == "server"
    assert a.fields["otel.time_unix_nano"] == "1735689600123456789"
    assert a.timestamp.microsecond == 123456 and a.severity == 5
    assert a.source == "ossec" and a.action == "log.record"


def test_otlp_duplicate_retry_collapses_but_uuid_conflicts_fail():
    doc = document()
    records = doc["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    records.append(copy.deepcopy(records[0]))
    assert len(decode(json.dumps(doc), "a")) == 1
    eid = str(uuid.uuid4())
    for record in records:
        record["attributes"].append(
            {"key": "keen.event.id", "value": {"stringValue": eid}}
        )
    records[1]["body"] = {"stringValue": "different"}
    with pytest.raises(ValueError, match="Conflicting"):
        decode(json.dumps(doc), "a")


@pytest.mark.parametrize("key", ["keen.field.resource.host", "keen.field.otel.scope"])
def test_enrichment_cannot_overwrite_reserved_provenance(key):
    doc = document()
    doc["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"].append(
        {"key": key, "value": {"stringValue": "spoof"}}
    )
    with pytest.raises(ValueError, match="Reserved"):
        decode(json.dumps(doc), "a")


def test_otlp_batch_bound_is_checked_before_duplicate_collapse():
    doc = document()
    records = doc["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    records.extend(copy.deepcopy(records[0]) for _ in range(64))
    with pytest.raises(ValueError, match="64"):
        decode(json.dumps(doc), "a")


@pytest.mark.parametrize(
    "doc, message",
    [
        ([], "Expected OTLP object"),
        ({"resourceLogs": {}}, "Resource limit"),
        ({"resourceLogs": [{}] * 65}, "Resource limit"),
        ({"resourceLogs": [{"scopeLogs": [{}] * 65}]}, "Scope limit"),
        (
            {"resourceLogs": [{"scopeLogs": [{"logRecords": "bad"}]}]},
            "Expected records",
        ),
    ],
)
def test_otlp_invalid_container_shapes(doc, message):
    with pytest.raises(ValueError, match="^" + re.escape(message) + "$"):
        decode(json.dumps(doc), "a")
