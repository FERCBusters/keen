"""Rule limits and rule scope through the assembled application.

A saved rule names a source, conditions KEEN evaluates within fixed bounds and
framework targets. Rules outside those limits are refused before anything is
saved. Previews and event samples examine only the rule's connection and
collection item. Requests pass through the production middleware, password
sign-in, CSRF and the administrator check.
"""

import json
from urllib.parse import quote

import pytest

# Imported fixtures: the assembled application and a rule workspace.
from tests.test_assembled_authority_regressions import keen, unsafe  # noqa: F401
from tests.test_rule_lifecycle_api import FW, _rule, lab  # noqa: F401


def _collector(adapter, section, key):
    return json.dumps([adapter, section, key], separators=(",", ":"))


def _put(lab, rule, *, url_id=None, create_only=False):  # noqa: F811 - fixture imported above
    return lab.admin.put(
        f"/v1/admin/mapping-rules/{quote(url_id or rule['id'])}",
        json={
            "version": lab.version(),
            "rule": rule,
            "create_only": create_only,
            "apply_existing": False,
        },
        headers=unsafe(lab.admin),
    )


TARGET = {"framework": FW, "ref": "A.1"}
REFUSED = [
    (
        "spaced-id",
        {**_rule("bad id")},
        "Rule ID must be 1–96 letters, numbers, underscores or hyphens",
    ),
    (
        "long-id",
        {**_rule("r" * 97)},
        "Rule ID must be 1–96 letters, numbers, underscores or hyphens",
    ),
    (
        "no-source",
        {"id": "r", "when": {"action": "deploy"}, "map_to": [TARGET]},
        "Choose a source for every rule",
    ),
    ("blank-source", _rule("r", source="  "), "Choose a source for every rule"),
    (
        "severity-high",
        _rule("r", severity=1001),
        "severity must be an integer from 0 to 1000",
    ),
    (
        "severity-flag",
        _rule("r", severity=True),
        "severity must be an integer from 0 to 1000",
    ),
    (
        "page-negative",
        _rule("r", bookstack_page_id=-1),
        "bookstack_page_id must be an integer from 0 to 2147483647",
    ),
    ("number-text", _rule("r", system=5), "system must be text under 512 characters"),
    (
        "long-text",
        _rule("r", actor="x" * 513),
        "actor must be text under 512 characters",
    ),
    (
        "nested-repeat",
        _rule("r", action_regex="(a+)+$"),
        "action_regex: Nested repetition can stall ingestion",
    ),
    (
        "bad-cidr",
        _rule(
            "r",
            fields=[{"path": ["ip"], "operator": "in_cidr", "value": "10.0.0.300/8"}],
        ),
        "Enter a valid IPv4 or IPv6 CIDR",
    ),
    (
        "no-targets",
        {**_rule("r"), "map_to": []},
        "Select at least one framework control or clause",
    ),
    (
        "many-targets",
        {**_rule("r"), "map_to": [TARGET] * 101},
        "Select at least one framework control or clause",
    ),
    (
        "target-no-ref",
        {**_rule("r"), "map_to": [{"framework": FW}]},
        "Each target needs a framework and reference",
    ),
    (
        "target-long-ref",
        {**_rule("r"), "map_to": [{"framework": FW, "ref": "A" * 65}]},
        "Each target needs a framework and reference",
    ),
    (
        "collector-text",
        _rule("r", source="jenkins", collector="deploy"),
        "Invalid collection identity or source",
    ),
    (
        "collector-other-source",
        _rule("r", source="loki", collector=_collector("jenkins", "jobs", "deploy")),
        "Invalid collection identity or source",
    ),
    (
        "collector-other-section",
        _rule(
            "r", source="jenkins", collector=_collector("jenkins", "queries", "deploy")
        ),
        "Invalid collection identity or source",
    ),
    (
        "collector-webhook-mismatch",
        _rule(
            "r",
            source="webhook:github",
            collector=_collector("webhooks", "providers", "gitlab"),
        ),
        "Invalid collection identity or source",
    ),
]


@pytest.mark.parametrize(
    "rule,detail",
    [(rule, detail) for _, rule, detail in REFUSED],
    ids=[name for name, _, _ in REFUSED],
)
def test_rules_outside_the_supported_limits_are_refused(lab, rule, detail):  # noqa: F811 - fixture imported above
    before = lab.version()

    refused = _put(lab, rule)

    assert (refused.status_code, refused.json()["detail"]) == (400, detail)
    assert lab.version() == before and lab.rule_ids() == []


def test_rules_at_the_limits_are_saved(lab):  # noqa: F811 - fixture imported above
    rule = _rule(
        "r" * 96,
        source="webhook:github",
        collector=_collector("webhooks", "providers", "github"),
        severity=1000,
        bookstack_page_id=2147483647,
        actor="x" * 512,
    )

    saved = _put(lab, {**rule, "map_to": [TARGET, TARGET]})

    assert saved.status_code == 200, saved.text
    # Repeated targets are saved once, with the kind they name.
    assert saved.json()["item"]["map_to"] == [{**TARGET, "kind": "control"}]
    assert lab.rule_ids() == ["r" * 96]


def test_a_rule_is_saved_under_its_own_id_and_new_ids_stay_new(lab):  # noqa: F811 - fixture imported above
    mismatched = _put(lab, _rule("two"), url_id="one")
    assert (mismatched.status_code, mismatched.json()["detail"]) == (
        400,
        "Rule ID does not match the URL",
    )

    assert _put(lab, _rule("one"), create_only=True).status_code == 200
    taken = _put(lab, _rule("one", action="other"), create_only=True)
    assert (taken.status_code, taken.json()["detail"]) == (
        409,
        "A rule with this ID already exists. Choose a different ID.",
    )
    # Editing an existing rule replaces it in place.
    edited = _put(lab, _rule("one", action="edited"))
    assert edited.status_code == 200
    rules = lab.admin.get("/v1/admin/mapping-rules").json()["items"]
    assert [(r["id"], r["when"].get("action")) for r in rules] == [("one", "edited")]


def _preview(lab, rule):  # noqa: F811 - fixture imported above
    response = lab.admin.post(
        "/v1/admin/mapping-rules/preview",
        json={"version": lab.version(), "rule": rule},
        headers=unsafe(lab.admin),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_previews_examine_only_the_rules_connection_and_collection(lab):  # noqa: F811 - fixture imported above
    def job(name, connection="ci-one"):
        return lab.event(
            "build",
            source="jenkins",
            connection_id=connection,
            raw_pointer={"jenkins": {"job": name}},
        )

    deploy = job("deploy")
    job("nightly")
    other_connection = job("deploy", connection="ci-two")
    deploys = _collector("jenkins", "jobs", "deploy")

    scoped = _preview(
        lab,
        _rule("r", source="jenkins", connection_id="ci-one", collector=deploys),
    )
    assert scoped["examined"] == 1
    assert [item["event_id"] for item in scoped["examples"]] == [str(deploy.id)]

    every_connection = _preview(lab, _rule("r", source="jenkins", collector=deploys))
    assert {item["event_id"] for item in every_connection["examples"]} == {
        str(deploy.id),
        str(other_connection.id),
    }


def test_event_samples_are_scoped_like_the_rule_being_written(lab):  # noqa: F811 - fixture imported above
    lab.event(
        "build",
        source="jenkins",
        connection_id="ci-one",
        raw_pointer={"jenkins": {"job": "deploy"}},
        summary="deploy on one",
    )
    lab.event(
        "build",
        source="jenkins",
        connection_id="ci-two",
        raw_pointer={"jenkins": {"job": "deploy"}},
        summary="deploy on two",
    )
    lab.event(
        "build",
        source="jenkins",
        connection_id="ci-one",
        raw_pointer={"jenkins": {"job": "nightly"}},
        summary="nightly on one",
    )

    def samples(**params):
        response = lab.admin.get(
            "/v1/admin/evidence-event-samples", params={"source": "jenkins", **params}
        )
        return response

    def summaries(**params):
        response = samples(**params)
        assert response.status_code == 200, response.text
        return sorted(item["summary"] for item in response.json()["items"])

    deploys = _collector("jenkins", "jobs", "deploy")
    assert summaries(collector=deploys) == ["deploy on one", "deploy on two"]
    assert summaries(collector=deploys, connection_id="ci-one") == ["deploy on one"]
    assert summaries(connection_id="ci-one") == ["deploy on one", "nightly on one"]
    for collector in ("deploy", _collector("loki", "queries", "deploy"), "[1]"):
        refused = samples(collector=collector)
        assert (refused.status_code, refused.json()["detail"]) == (
            400,
            "Invalid collection item",
        ), collector
