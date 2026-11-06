"""Structured rule conditions must match the exact literal path and scalar type."""

import pytest
from app.mapping.fields import field_matches, observed_fields, validate_fields

PATH = "Field path must contain 1-8 literal JSON keys"
VALUE = "Field match value must be text up to 4096 characters"
LIST = "Use at most 20 structured field conditions"
SHAPE = "Invalid structured field condition"


@pytest.mark.parametrize(
    "conditions,message",
    [
        ([{"path": [], "operator": "exists"}], PATH),
        ([{"path": ["x"] * 9, "operator": "exists"}], PATH),
        ([{"path": [""], "operator": "exists"}], PATH),
        ([{"path": ["x" * 257], "operator": "exists"}], PATH),
        ([{"path": [1], "operator": "exists"}], PATH),
        ([{"path": None, "operator": "exists"}], PATH),
        # A dotted path is one key's text, never a path.
        ([{"path": "fields.name", "operator": "exists"}], PATH),
        ([{"path": ["x"], "operator": "equals"}], VALUE),
        ([{"path": ["x"], "operator": "equals", "value": 1}], VALUE),
        ([{"path": ["x"], "operator": "equals", "value": "x" * 4097}], VALUE),
        ([{"path": ["a"], "operator": "equals", "value": {}}], VALUE),
        ([{"path": ["x"], "operator": "exists", "unexpected": True}], SHAPE),
        # An operator outside the fixed set is never evaluated.
        (
            [{"path": ["fields"], "operator": "eval", "value": "x"}],
            "Unsupported structured field operator",
        ),
        (
            [{"path": ["fields", "client.ip"], "operator": "in_cidr", "value": "bad"}],
            "Enter a valid IPv4 or IPv6 CIDR",
        ),
        # Event scope reaches the summary alone, never another stored column.
        (
            [{"scope": "event", "path": ["password"], "operator": "exists"}],
            "Only Summary is supported as an event field",
        ),
        (
            [{"scope": "unknown", "path": ["summary"], "operator": "exists"}],
            "Unsupported field scope",
        ),
        (
            [{"scope": [], "path": ["summary"], "operator": "exists"}],
            "Unsupported field scope",
        ),
        (None, LIST),
        ({}, LIST),
        ("x", LIST),
        ([None], SHAPE),
        ([{"path": ["a"], "operator": "exists"}] * 21, LIST),
    ],
)
def test_invalid_conditions_are_refused_with_their_reason(conditions, message):
    with pytest.raises(ValueError) as refused:
        validate_fields(conditions)
    assert str(refused.value) == message


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
        ("35.221.69.202", "35.221.69.0/24", True),
        ("2001:db8::1", "2001:db8::/32", True),
        ("2001:db8::1", "192.0.2.0/24", False),
        ("invalid", "192.0.2.0/24", False),
        ("192.0.2.0", "192.0.2.0/24", True),
        ("192.0.2.255", "192.0.2.0/24", True),
        ("192.0.3.0", "192.0.2.0/24", False),
        ("::1", "::1/128", True),
        ("fe80::1%eth0", "fe80::/10", False),
        ("192.0.2.1", "192.0.2.99/24", True),
    ],
)
def test_cidr_boundaries(ip, network, expected):
    condition = {"path": ["ip"], "operator": "in_cidr", "value": network}
    assert validate_fields([condition]) == [condition]
    assert field_matches({"ip": ip}, condition) is expected


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
