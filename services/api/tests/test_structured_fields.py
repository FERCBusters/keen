import pytest
from app.agents.schema import Health
from app.mapping.fields import field_matches, validate_fields
from app.mapping.rules import evaluate_by_framework, parse_rules


def test_literal_dotted_keys_and_missing():
    c = {
        "path": ["fields", "service.name"],
        "operator": "equals",
        "value": "keen-agent.service",
    }
    validate_fields([c])
    assert field_matches({"fields": {"service.name": "keen-agent.service"}}, c)
    assert not field_matches({"fields": {"service": {"name": "keen-agent.service"}}}, c)
    assert not field_matches({}, c)


@pytest.mark.parametrize(
    "op,value,expected",
    [
        ("equals", "package", False),
        ("starts_with", "package", True),
        ("contains", "upgrade", True),
        ("exists", "", True),
    ],
)
def test_operators(op, value, expected):
    assert (
        field_matches(
            {"action": "package.upgrade"},
            {"path": ["action"], "operator": op, "value": value},
        )
        == expected
    )


def test_runtime_and_historical_payload_rule():
    rules = parse_rules(
        {
            "rules": [
                {
                    "id": "agent-service",
                    "when": {
                        "source": "keen-agent",
                        "fields": [
                            {
                                "path": ["fields", "_SYSTEMD_UNIT"],
                                "operator": "equals",
                                "value": "keen-agent.service",
                            }
                        ],
                    },
                    "map_to": [{"framework": "ISO27001:2022", "ref": "A.8.15"}],
                }
            ]
        }
    )
    event = {
        "source": "keen-agent",
        "normalized_payload": {"fields": {"_SYSTEMD_UNIT": "keen-agent.service"}},
    }
    assert evaluate_by_framework(event, rules)
    event["normalized_payload"] = {}
    assert not evaluate_by_framework(event, rules)


@pytest.mark.parametrize(
    "conditions",
    [
        [{"path": ["fields"], "operator": "eval", "value": "x"}],
        [{"path": "fields.name", "operator": "exists"}],
        [{"path": ["a"], "operator": "equals", "value": {}}],
    ],
)
def test_invalid_conditions_rejected(conditions):
    with pytest.raises(ValueError):
        validate_fields(conditions)


def test_health_accepts_delivery_counters():
    h = Health.model_validate(
        {
            "version": "0.1.1",
            "delivered_since_start": 2496,
            "delivery_batches_since_start": 39,
            "delivery_events_per_second_since_start": 42.99,
            "last_delivery_at": "2026-10-06T04:25:45Z",
        }
    )
    assert h.delivered_since_start == 2496
    with pytest.raises(ValueError):
        Health.model_validate({"delivery_events_per_second_since_start": float("nan")})
    with pytest.raises(ValueError):
        Health.model_validate({"unexpected": "x"})


@pytest.mark.parametrize(
    "ip,network,expected",
    [
        ("35.221.69.202", "35.221.69.0/24", True),
        ("2001:db8::1", "2001:db8::/32", True),
        ("2001:db8::1", "192.0.2.0/24", False),
        ("invalid", "192.0.2.0/24", False),
    ],
)
def test_ip_cidr(ip, network, expected):
    c = {"path": ["fields", "client.ip"], "operator": "in_cidr", "value": network}
    validate_fields([c])
    assert field_matches({"fields": {"client.ip": ip}}, c) == expected


def test_invalid_network_rejected():
    with pytest.raises(ValueError):
        validate_fields(
            [{"path": ["fields", "client.ip"], "operator": "in_cidr", "value": "bad"}]
        )


def test_filtered_health_counts():
    assert (
        Health.model_validate({"filtered_by_source": {"nginx": 5}}).filtered_by_source[
            "nginx"
        ]
        == 5
    )
    with pytest.raises(ValueError):
        Health.model_validate({"filtered_by_source": {"nginx": -1}})


def test_structured_regex_uses_literal_paths_and_search_semantics():
    condition = {
        "path": ["fields", "process.name"],
        "operator": "regex",
        "value": "^(?:gills|systemd)$",
    }
    validate_fields([condition])
    for name, expected in [
        ("gills", True),
        ("systemd", True),
        ("other", False),
        ("GILLS", False),
    ]:
        assert field_matches({"fields": {"process.name": name}}, condition) == expected
    for value in (
        {},
        {"fields": {"process.name": None}},
        {"fields": {"process.name": []}},
    ):
        assert not field_matches(value, condition)
    condition["value"] = "(?i)gills"
    assert field_matches({"fields": {"process.name": "GILLS.service"}}, condition)
    condition["operator"] = "contains"
    assert not field_matches({"fields": {"process.name": "GILLS.service"}}, condition)


@pytest.mark.parametrize("pattern", ["[", "(a+)+", "x" * 513, r"(a)\1"])
def test_structured_regex_rejects_unsafe_patterns_at_parse_and_runtime(pattern):
    condition = {"path": ["summary"], "operator": "regex", "value": pattern}
    with pytest.raises(ValueError):
        validate_fields([condition])
    with pytest.raises(ValueError):
        field_matches({"summary": "test"}, condition)
    with pytest.raises(ValueError):
        parse_rules(
            {
                "rules": [
                    {
                        "id": "bad",
                        "when": {"fields": [condition]},
                        "map_to": [{"framework": "A", "ref": "1"}],
                    }
                ]
            }
        )


def test_structured_regex_is_bounded_and_combines_with_summary_using_and():
    condition = {
        "path": ["fields", "process.name"],
        "operator": "regex",
        "value": "gills|systemd",
    }
    with pytest.raises(ValueError, match="65536"):
        field_matches({"fields": {"process.name": "x" * 65537}}, condition)
    rules = parse_rules(
        {
            "rules": [
                {
                    "id": "both",
                    "when": {"fields": [condition], "summary_regex": "gills"},
                    "map_to": [{"framework": "A", "ref": "1"}],
                }
            ]
        }
    )
    event = {
        "summary": '"checks": [',
        "normalized_payload": {"fields": {"process.name": "gills"}},
    }
    assert not evaluate_by_framework(event, rules)
    event["summary"] = "gills.service: Deactivated successfully."
    event["normalized_payload"]["fields"]["process.name"] = "systemd"
    assert evaluate_by_framework(event, rules)


@pytest.mark.parametrize(
    "operator,value,matched",
    [
        ("contains", "gills", True),
        ("regex", "^gills", True),
        ("equals", "gills", False),
    ],
)
def test_event_summary_conditions_use_canonical_summary(operator, value, matched):
    rule = {
        "id": "summary",
        "when": {
            "fields": [
                {
                    "scope": "event",
                    "path": ["summary"],
                    "operator": operator,
                    "value": value,
                }
            ]
        },
        "map_to": [{"framework": "A", "ref": "1"}],
    }
    parsed = parse_rules({"rules": [rule]})
    event = {
        "summary": "gills.service: stopped",
        "normalized_payload": {"summary": "different"},
    }
    assert bool(evaluate_by_framework(event, parsed)) == matched
    rule["when"]["fields"][0].pop("scope")
    assert not evaluate_by_framework(event, parse_rules({"rules": [rule]}))


@pytest.mark.parametrize(
    "scope,path", [("event", ["password"]), ("unknown", ["summary"]), ([], ["summary"])]
)
def test_event_scope_is_restricted(scope, path):
    with pytest.raises(ValueError):
        validate_fields([{"scope": scope, "path": path, "operator": "exists"}])


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"v": "gills.service"}, False),
        ({"v": "systemd"}, True),
        ({"v": "GILLS"}, True),
        ({"v": ""}, True),
        ({}, False),
        ({"v": None}, False),
        ({"v": []}, False),
        ({"v": {}}, False),
    ],
)
def test_not_contains_is_literal_and_requires_existing_scalar(payload, expected):
    condition = {"path": ["v"], "operator": "not_contains", "value": "gills"}
    validate_fields([condition])
    assert field_matches(payload, condition) == expected
    condition["value"] = ""
    assert not field_matches(payload, condition)


def test_not_contains_canonical_summary_and_literal_pattern():
    rule = {
        "id": "exclude",
        "when": {
            "fields": [
                {
                    "scope": "event",
                    "path": ["summary"],
                    "operator": "not_contains",
                    "value": "gills|systemd",
                }
            ]
        },
        "map_to": [{"framework": "A", "ref": "1"}],
    }
    parsed = parse_rules({"rules": [rule]})
    assert evaluate_by_framework({"summary": "gills"}, parsed)
    assert not evaluate_by_framework({"summary": "gills|systemd"}, parsed)
    assert not evaluate_by_framework({}, parsed)
