"""Rule parsing and evaluation contracts across formats, filters and provenance."""

import pytest
from app.mapping.collector import (
    collector_id,
    collector_pointer_filter,
    event_collector,
    matches_collector,
)
from app.mapping.rules import evaluate, evaluate_by_framework, parse_rules


def rule(**kwargs):
    return {"id": "test", "framework": "A", "when": {}, "map_to": ["C1"], **kwargs}


@pytest.mark.parametrize(
    "wrap",
    [
        lambda r: [r],
        lambda r: {"rules": [r]},
        lambda r: {"frameworks": {"A": [r]}},
        lambda r: {"frameworks": {"A": {"rules": [r]}}},
        lambda r: {"A": [r]},
        lambda r: {"A": {"rules": [r]}},
    ],
)
def test_supported_rule_layouts_preserve_targets(wrap):
    parsed = parse_rules(wrap(rule()))
    assert evaluate_by_framework({}, parsed) == {"A": ["C1"]}


@pytest.mark.parametrize("field", ["source", "system", "actor", "action", "outcome"])
def test_exact_and_regex_conditions_are_combined(field):
    parsed = parse_rules(
        [rule(when={field: "package.upgrade", field + "_regex": r"^package\."})]
    )
    assert evaluate_by_framework({field: "package.upgrade"}, parsed) == {"A": ["C1"]}
    assert not evaluate_by_framework({field: "package.install"}, parsed)
    assert not evaluate_by_framework({}, parsed)
    parsed = parse_rules(
        [rule(when={field: "package.upgrade", field + "_regex": r"^other\."})]
    )
    assert not evaluate_by_framework({field: "package.upgrade"}, parsed)


def test_severity_zero_and_missing_are_distinct():
    parsed = parse_rules([rule(when={"severity": 0})])
    assert evaluate({"severity": 0}, parsed) == ["C1"]
    assert evaluate({}, parsed) == []
    assert evaluate({"severity": "not a number"}, parsed) == []


def test_nested_labels_and_summary_fallback():
    parsed = parse_rules([rule(when={"label": "app=sshd", "summary_regex": "failure"})])
    event = {"normalized_payload": {"labels": {"app": "sshd"}, "message": "failure"}}
    assert evaluate(event, parsed) == ["C1"]
    assert evaluate({**event, "summary": "success"}, parsed) == []


def test_disabled_rules_missing_targets_and_nonobjects_are_ignored():
    assert parse_rules([None, 42, rule(enabled=False), rule(map_to=[])]) == []


def test_multi_framework_and_duplicate_provenance_choose_highest_confidence():
    rules = parse_rules(
        [
            rule(id="low", frameworks=["A", "B"], map_to=["C1", "C1"], confidence=0.2),
            rule(id="high", confidence=0.9),
        ]
    )
    assert evaluate_by_framework({}, rules) == {"A": ["C1"], "B": ["C1"]}
    details = evaluate_by_framework({}, rules, details=True)
    assert (
        details["A"][0]["confidence"] == 0.9 and "high" in details["A"][0]["rationale"]
    )
    assert details["B"][0]["confidence"] == 0.2
    assert evaluate({}, rules) == ["C1"] and evaluate({}, rules, "missing") == []


@pytest.mark.parametrize(
    "roles,expected",
    [(None, False), (set(), False), ({"viewer"}, False), ({" ADMIN "}, True)],
)
def test_target_role_requirements(roles, expected):
    rules = parse_rules(
        [rule(map_to=[{"framework": "A", "ref": "C1", "roles_any": ["admin"]}])]
    )
    assert bool(evaluate({}, rules, active_roles=roles)) is expected


@pytest.mark.parametrize(
    "source,section,key,info",
    [
        ("loki", "queries", "q", {"query_name": "q"}),
        ("cloudwatch_logs", "queries", "q", {"name": "q"}),
        ("jenkins", "jobs", "folder/job", {"job": "folder/job"}),
        ("rss", "feeds", "https://feed", {"feed_url": "https://feed"}),
        ("forgejo", "feeds", "public", {"feed": "public"}),
        ("gitea", "feeds", "public", {"feed": "public"}),
        (
            "riskledger",
            "organizations",
            "org",
            {"org": "org", "section": "organizations"},
        ),
        ("redmine", "projects", "p", {"project_selector": "p", "section": "projects"}),
        ("gitlab", "projects", "p", {"section": "projects", "key": "p"}),
        ("taiga", "projects", 42, {"project_id": 42}),
        ("google_workspace", "streams", "login", {"stream": "login"}),
        ("bookstack", "selected_pages", 12, {"page_id": 12}),
        (
            "github",
            "repos",
            "owner/repo",
            {"owner": "owner", "repo": "repo", "endpoint": "repo_events"},
        ),
        ("github", "organizations", "org", {"org": "org"}),
        ("github", "feeds", "public", {"feed": "public"}),
    ],
)
def test_collector_identity_roundtrip_and_other_collection_rejection(
    source, section, key, info
):
    identity = collector_id(source, section, key)
    event = {"source": source, "raw_pointer": {source: info}}
    assert event_collector(event) == identity
    assert matches_collector(identity, event)
    assert not matches_collector(collector_id(source, section, "other"), event)
    pointer = collector_pointer_filter(identity)
    assert all(info[k] == v for k, v in pointer[source].items())


def test_webhook_and_owner_collection_identity():
    identity = collector_id("webhooks", "providers", "monitor")
    event = {
        "source": "webhook:monitor",
        "raw_pointer": {"webhook": {"provider": "monitor"}},
    }
    assert matches_collector(identity, event)
    assert collector_pointer_filter(identity) == event["raw_pointer"]
    for source in ["forgejo", "gitea"]:
        identity = collector_id(source, "organizations", "org")
        assert matches_collector(
            identity, {"source": source, "raw_pointer": {source: {"owner": "org"}}}
        )
        assert not matches_collector(
            identity, {"source": "other", "raw_pointer": {source: {"owner": "org"}}}
        )


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"source": "unknown"},
        {"source": "loki", "raw_pointer": []},
        {"source": "loki", "raw_pointer": {"loki": "bad"}},
    ],
)
def test_missing_provenance_is_not_an_identity(event):
    assert event_collector(event) is None


# --- BookStack page conditions ---------------------------------------------------

# A BookStack page event as the collector stores it: identifiers in the raw
# pointer, the page title in the normalised payload.
BOOKSTACK_EVENT = {
    "source": "bookstack",
    "raw_pointer": {
        "bookstack": {
            "page_id": 41,
            "book_id": 7,
            "page_slug": "access-control",
            "book_slug": "policies",
        }
    },
    "normalized_payload": {
        "bookstack": {
            "page_id": 41,
            "page_slug": "access-control",
            "page_title": "Access Control",
            "book_slug": "policies",
        }
    },
}


@pytest.mark.parametrize(
    "when,matches",
    [
        ({"bookstack_book_id": 7}, True),
        ({"bookstack_book_id": "7"}, True),
        ({"bookstack_book_id": 8}, False),
        ({"bookstack_book_slug": "POLICIES"}, True),
        ({"bookstack_book_slug": "procedures"}, False),
        ({"bookstack_page_id": 41}, True),
        ({"bookstack_page_id": 0}, False),
        ({"bookstack_page_slug": "Access-Control"}, True),
        ({"bookstack_page_slug": "policies-access-control"}, True),
        ({"bookstack_page_slug": "access"}, False),
        ({"bookstack_page_slug_regex": r"^policies-access"}, True),
        ({"bookstack_page_slug_regex": r"^procedures-"}, False),
        ({"bookstack_page_title": "access control"}, True),
        ({"bookstack_page_title": "Access"}, False),
        ({"bookstack_page_title_regex": r"Control$"}, True),
        ({"bookstack_page_title_regex": r"^Control"}, False),
        (
            {
                "bookstack_book_id": 7,
                "bookstack_page_slug": "access-control",
                "bookstack_page_title": "Access Control",
            },
            True,
        ),
        ({"bookstack_book_id": 7, "bookstack_page_title": "Clean desk"}, False),
    ],
)
def test_bookstack_conditions_select_books_and_pages(when, matches):
    parsed = parse_rules([rule(when={"source": "bookstack", **when})])

    assert bool(evaluate(BOOKSTACK_EVENT, parsed)) is matches


def test_bookstack_conditions_read_the_raw_pointer_before_the_payload():
    event = {
        "source": "bookstack",
        # The raw pointer names the book; its slug comes from the payload.
        "raw_pointer": {"bookstack": {"page_slug": "access-control", "book_id": 7}},
        "normalized_payload": {
            "bookstack": {
                "page_slug": "superseded",
                "book_id": 9,
                "book_slug": "policies",
            }
        },
    }

    def matches(**when):
        return bool(evaluate(event, parse_rules([rule(when=when)])))

    assert matches(bookstack_page_slug="access-control", bookstack_book_id=7)
    assert not matches(bookstack_page_slug="superseded")
    assert not matches(bookstack_book_id=9)
    assert matches(bookstack_page_slug="policies-access-control")
    # Without a book slug only the page's own slug is a candidate.
    alone = {"raw_pointer": {"bookstack": {"page_slug": "access-control"}}}
    slug = parse_rules([rule(when={"bookstack_page_slug_regex": "^access"})])
    qualified = parse_rules([rule(when={"bookstack_page_slug_regex": "-access"})])
    assert evaluate(alone, slug) == ["C1"]
    assert evaluate(alone, qualified) == []


@pytest.mark.parametrize(
    "event",
    [
        {"source": "bookstack"},
        {"source": "bookstack", "raw_pointer": {"bookstack": "page 41"}},
        {"source": "bookstack", "normalized_payload": {"bookstack": ["page 41"]}},
        # Malformed provenance refuses the event even beside a usable payload.
        {
            "source": "bookstack",
            "raw_pointer": {"bookstack": "page 41"},
            "normalized_payload": {"bookstack": {"page_id": 41}},
        },
        {"source": "loki", "raw_pointer": {"loki": {"page_id": 41}}},
    ],
    ids=[
        "no-page",
        "text-pointer",
        "list-payload",
        "text-beside-payload",
        "other-source",
    ],
)
def test_bookstack_conditions_never_match_events_without_page_details(event):
    parsed = parse_rules([rule(when={"bookstack_page_id": 41})])

    assert evaluate(event, parsed) == []


# --- Targets ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "map_to,expected",
    [
        ("A.1", {"A": ["A.1"]}),
        ([" A.1 ", "", None, "A.2"], {"A": ["A.1", "A.2"]}),
        ({"A": ["A.1"], "B": "B.1"}, {"A": ["A.1"], "B": ["B.1"]}),
        ({"*": ["W.1"]}, {"A": ["W.1"]}),
        ({"default": "D.1"}, {"A": ["D.1"]}),
        ({"*": "W.1", "default": "D.1"}, {"A": ["W.1"]}),
        ({"A": ["A.1"], "*": ["W.1"]}, {"A": ["A.1"]}),
        (
            {"targets": [{"framework": "B", "refs": ["B.1", "B.2"]}]},
            {"B": ["B.1", "B.2"]},
        ),
        (
            [{"ref": "A.1"}, {"frameworks": ["B", "C"], "ref": "X.1"}],
            {"A": ["A.1"], "B": ["X.1"], "C": ["X.1"]},
        ),
        ([{"framework_slug": "B", "ref": "B.1"}], {"B": ["B.1"]}),
        ([{"framework": "B", "ref": "B.1", "kind": "policy"}], {}),
    ],
    ids=[
        "scalar",
        "list",
        "framework-map",
        "wildcard",
        "default",
        "wildcard-before-default",
        "own-bucket-first",
        "targets",
        "objects",
        "framework-slug",
        "unknown-kind",
    ],
)
def test_every_target_form_maps_to_explicit_framework_references(map_to, expected):
    parsed = parse_rules([rule(map_to=map_to)])

    assert evaluate_by_framework({}, parsed) == expected


def test_targets_fall_back_to_controls_then_the_default_framework():
    from app.core.config import settings

    assert evaluate_by_framework(
        {}, parse_rules([rule(map_to=None, controls=["C9"])])
    ) == {"A": ["C9"]}
    bare = {"id": "bare", "when": {}, "map_to": ["C1"]}
    assert evaluate_by_framework({}, parse_rules([bare])) == {
        settings.default_framework_slug: ["C1"]
    }
    # An empty framework list leaves the single framework in charge.
    listed = rule(frameworks=[" "], framework="B")
    assert evaluate_by_framework({}, parse_rules([listed])) == {"B": ["C1"]}


def test_an_unreadable_confidence_uses_the_default():
    parsed = parse_rules([rule(confidence="high")])

    details = evaluate_by_framework({}, parsed, details=True)
    assert details == {
        "A": [
            {
                "ref": "C1",
                "kind": None,
                "confidence": 0.8,
                "rationale": "auto by rule test (A)",
            }
        ]
    }


# --- Labels ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "when,matches",
    [
        ({"label": "prod"}, True),
        ({"label": "team=ops"}, True),
        ({"label": "ops"}, True),
        ({"label": "job=sshd"}, True),
        ({"label": "port=22"}, True),
        ({"label": "edge"}, True),
        ({"label": "True"}, True),
        ({"label_regex": "^job=ss"}, True),
        ({"label": "tags=edge"}, False),
        ({"label": "staging"}, False),
        ({"label_regex": "^staging"}, False),
    ],
)
def test_labels_come_from_lists_nested_payloads_and_the_raw_pointer(when, matches):
    event = {
        "labels": ["prod", {"team": "ops"}],
        "raw_pointer": {
            "stream": {"labels": {"job": "sshd", "port": 22, "tags": ["edge"]}}
        },
        "normalized_payload": {"items": [{"label": True}]},
    }

    assert bool(evaluate(event, parse_rules([rule(when=when)]))) is matches
