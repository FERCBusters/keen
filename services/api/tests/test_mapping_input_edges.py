"""Mapping rules and collection identities over unusual evidence shapes.

Evidence is untrusted: field names the explorer cannot show, label values that
are not text, labels nested beyond the walk's bound and payloads that cannot be
written as JSON. Rules come from a missing file when
no installation document exists.
"""

from datetime import datetime

from app.mapping.fields import observed_fields
from app.mapping.rules import evaluate_by_framework, load_rules, parse_rules
from app.security.json_shape import MAX_DEPTH


def _rule(**when):
    return {
        "id": "probe",
        "when": when,
        "map_to": [{"framework": "TEST", "ref": "A.1"}],
    }


def _matches(event, **when):
    return evaluate_by_framework(event, parse_rules({"rules": [_rule(**when)]}))


def test_field_names_the_explorer_cannot_show_are_left_out():
    payload = {"": "blank", "k" * 257: "long", 7: "numbered", "k" * 256: "widest"}
    assert observed_fields({**payload, "kept": {"": 1, "inner": True}}) == [
        {"path": ["k" * 256], "value": "widest"},
        {"path": ["kept", "inner"], "value": "true"},
    ]


def test_label_values_that_are_not_text_numbers_lists_or_mappings_give_no_label():
    event = {
        "source": "loki",
        "labels": {"team": "payments", "since": datetime(2026, 1, 1), "raw": b"x"},
    }
    assert _matches(event, label="team=payments") == {"TEST": ["A.1"]}
    for label in ("since", "raw", "b'x'"):
        assert _matches(event, label_regex=label) == {}, label


def _nested(levels):
    value = {"labels": {"team": "deep"}}
    for _ in range(levels):
        value = {"wrap": value}
    return value


def test_labels_nested_beyond_the_walk_bound_are_not_read():
    def labelled(levels):
        event = {"source": "loki", "normalized_payload": _nested(levels)}
        return _matches(event, label="team=deep")

    assert labelled(MAX_DEPTH - 1) == {"TEST": ["A.1"]}
    assert labelled(MAX_DEPTH) == {}


def test_a_payload_that_is_not_json_is_matched_by_its_text():
    event = {
        "source": "rss",
        "normalized_payload": {"published": datetime(2026, 10, 9, 12, 0)},
    }
    assert _matches(event, summary_regex=r"datetime\.datetime\(2026, 10, 9") == {
        "TEST": ["A.1"]
    }
    # A payload that is JSON is matched by its JSON text.
    assert _matches(
        {"source": "rss", "normalized_payload": {"title": "Patch"}},
        summary_regex='"title": "Patch"',
    ) == {"TEST": ["A.1"]}


def test_rules_from_a_missing_file_without_an_installation_document_are_none(
    contract_db, tmp_path
):
    assert load_rules(str(tmp_path / "rules.yml"), db=contract_db) == []
