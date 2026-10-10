"""Structured rule conditions must match the exact literal path and scalar type."""

import pytest
from app.mapping.fields import field_matches, observed_fields, validate_fields


@pytest.mark.parametrize(
    "condition",
    [
        {"path": [], "operator": "exists"},
        {"path": ["x"] * 9, "operator": "exists"},
        {"path": [""], "operator": "exists"},
        {"path": ["x" * 257], "operator": "exists"},
        {"path": [1], "operator": "exists"},
        {"path": None, "operator": "exists"},
        {"path": ["x"], "operator": "equals"},
        {"path": ["x"], "operator": "equals", "value": 1},
        {"path": ["x"], "operator": "equals", "value": "x" * 4097},
        {"path": ["x"], "operator": "exists", "unexpected": True},
    ],
)
def test_invalid_condition_boundaries(condition):
    with pytest.raises(ValueError):
        validate_fields([condition])


@pytest.mark.parametrize(
    "items", [None, {}, "x", [None], [{"path": ["a"], "operator": "exists"}] * 21]
)
def test_invalid_condition_lists(items):
    with pytest.raises(ValueError):
        validate_fields(items)


def test_inclusive_limits():
    item = {"path": ["x" * 256] * 8, "operator": "equals", "value": "v" * 4096}
    assert validate_fields([item] * 20) == [item] * 20


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, "true"),
        (False, "false"),
        (0, "0"),
        (2.5, "2.5"),
        ("001", "001"),
        ("", ""),
    ],
)
def test_scalar_equality_is_textual_and_preserves_zero(value, expected):
    assert field_matches(
        {"v": value}, {"path": ["v"], "operator": "equals", "value": expected}
    )
    assert not field_matches(
        {"v": value}, {"path": ["v"], "operator": "equals", "value": expected + "x"}
    )


@pytest.mark.parametrize("value", [None, [], {}, ["x"]])
def test_exists_can_match_present_non_scalar_but_comparisons_cannot(value):
    assert field_matches({"v": value}, {"path": ["v"], "operator": "exists"})
    assert not field_matches(
        {"v": value}, {"path": ["v"], "operator": "equals", "value": "x"}
    )


@pytest.mark.parametrize(
    "payload", [{}, {"a": None}, {"a": "text"}, {"a": []}, {"a": {"other": 1}}]
)
def test_missing_nested_field_never_matches(payload):
    assert not field_matches(payload, {"path": ["a", "b"], "operator": "exists"})


@pytest.mark.parametrize(
    "ip,network,expected",
    [
        ("192.0.2.0", "192.0.2.0/24", True),
        ("192.0.2.255", "192.0.2.0/24", True),
        ("192.0.3.0", "192.0.2.0/24", False),
        ("::1", "::1/128", True),
        ("fe80::1%eth0", "fe80::/10", False),
        ("192.0.2.1", "192.0.2.99/24", True),
    ],
)
def test_cidr_boundaries(ip, network, expected):
    assert (
        field_matches(
            {"ip": ip}, {"path": ["ip"], "operator": "in_cidr", "value": network}
        )
        is expected
    )


def test_field_discovery_preserves_literal_keys_and_omits_unmatchable_values():
    payload = {
        "fields": {
            "ossec.rule.id": "31101",
            "zero": 0,
            "flag": False,
            "empty": "",
            "null": None,
            "array": [1],
            "long": "x" * 4097,
        }
    }
    assert observed_fields(payload) == [
        {"path": ["fields", "ossec.rule.id"], "value": "31101"},
        {"path": ["fields", "zero"], "value": "0"},
        {"path": ["fields", "flag"], "value": "false"},
        {"path": ["fields", "empty"], "value": ""},
    ]
    assert len(observed_fields({str(i): i for i in range(200)})) == 100
