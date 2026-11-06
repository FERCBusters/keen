import pytest
from app.agents.schema import Health
from app.mapping.fields import field_matches, validate_fields
from app.mapping.rules import evaluate_by_framework, parse_rules
from pydantic import ValidationError


def test_literal_dotted_keys_and_missing():
    c = {
        "path": ["fields", "service.name"],
        "operator": "equals",
        "value": "keen-agent.service",
    }
    assert validate_fields([c]) == [c]
    assert field_matches({"fields": {"service.name": "keen-agent.service"}}, c) is True
    assert (
        field_matches({"fields": {"service": {"name": "keen-agent.service"}}}, c)
        is False
    )
    assert field_matches({}, c) is False


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
    assert evaluate_by_framework(event, rules) == {"ISO27001:2022": ["A.8.15"]}
    event["normalized_payload"] = {}
    assert evaluate_by_framework(event, rules) == {}


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
    for health, problem in (
        (
            {"delivery_events_per_second_since_start": float("nan")},
            ("finite_number", ("delivery_events_per_second_since_start",)),
        ),
        ({"unexpected": "x"}, ("extra_forbidden", ("unexpected",))),
    ):
        with pytest.raises(ValidationError) as refused:
            Health.model_validate(health)
        assert [(e["type"], e["loc"]) for e in refused.value.errors()] == [problem]


def test_filtered_health_counts():
    assert (
        Health.model_validate({"filtered_by_source": {"nginx": 5}}).filtered_by_source[
            "nginx"
        ]
        == 5
    )
    with pytest.raises(ValidationError) as refused:
        Health.model_validate({"filtered_by_source": {"nginx": -1}})
    assert [(e["type"], e["msg"]) for e in refused.value.errors()] == [
        ("value_error", "Value error, Invalid filtered counts")
    ]


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
        assert field_matches(value, condition) is False
    condition["value"] = "(?i)gills"
    assert (
        field_matches({"fields": {"process.name": "GILLS.service"}}, condition) is True
    )
    condition["operator"] = "contains"
    assert (
        field_matches({"fields": {"process.name": "GILLS.service"}}, condition) is False
    )


@pytest.mark.parametrize(
    "pattern,problem",
    [
        ("[", "Invalid pattern: unterminated character set at position 0"),
        ("(a+)+", "Nested repetition can stall ingestion"),
        ("x" * 513, "Pattern must be at most 512 characters"),
        (r"(a)\1", "Backreferences are unsupported"),
    ],
)
def test_structured_regex_rejects_unsafe_patterns_at_parse_and_runtime(
    pattern, problem
):
    condition = {"path": ["summary"], "operator": "regex", "value": pattern}
    for check in (
        lambda: validate_fields([condition]),
        lambda: field_matches({"summary": "test"}, condition),
    ):
        with pytest.raises(ValueError) as refused:
            check()
        assert str(refused.value) == problem
    # A rules document with such a rule still loads; that rule alone is skipped.
    rules = parse_rules(
        {
            "rules": [
                {
                    "id": "bad",
                    "when": {"fields": [condition]},
                    "map_to": [{"framework": "A", "ref": "1"}],
                },
                {"id": "good", "when": {}, "map_to": [{"framework": "A", "ref": "2"}]},
            ]
        }
    )
    assert [rule.id for rule in rules] == ["good"]


def test_structured_regex_is_bounded_and_combines_with_summary_using_and():
    condition = {
        "path": ["fields", "process.name"],
        "operator": "regex",
        "value": "gills|systemd",
    }
    # A long value is read up to the match window; it never stops the run.
    assert (
        field_matches({"fields": {"process.name": "systemd" + "x" * 65537}}, condition)
        is True
    )
    assert (
        field_matches({"fields": {"process.name": "x" * 65537 + "systemd"}}, condition)
        is False
    )
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
    assert evaluate_by_framework(event, rules) == {}
    event["summary"] = "gills.service: Deactivated successfully."
    event["normalized_payload"]["fields"]["process.name"] = "systemd"
    assert evaluate_by_framework(event, rules) == {"A": ["1"]}


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
    assert evaluate_by_framework(event, parsed) == ({"A": ["1"]} if matched else {})
    rule["when"]["fields"][0].pop("scope")
    assert evaluate_by_framework(event, parse_rules({"rules": [rule]})) == {}


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
    assert field_matches(payload, condition) is False


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
    assert evaluate_by_framework({"summary": "gills"}, parsed) == {"A": ["1"]}
    assert evaluate_by_framework({"summary": "gills|systemd"}, parsed) == {}
    assert not evaluate_by_framework({}, parsed)
