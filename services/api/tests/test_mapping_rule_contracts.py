"""Rule parsing and evaluation contracts: layout, conditions, targets and provenance.

Rules are read strictly. An entry Keen cannot read exactly as written, such as
a misspelt key, a value of the wrong type, an unsafe pattern or a malformed
target, is skipped with one logged warning, so a typo never widens what a rule
maps.
"""

import logging

import pytest
from app.core.config import settings
from app.mapping import rules as rule_parser
from app.mapping.collector import (
    collector_id,
    collector_pointer_filter,
    event_collector,
    matches_collector,
)
from app.mapping.rules import evaluate_by_framework, parse_rules, rule_problem


def rule(**kwargs):
    return {"id": "test", "framework": "A", "when": {}, "map_to": ["C1"], **kwargs}


def parse(*entries, **document):
    return parse_rules({**document, "rules": list(entries)})


def matched(event, *entries):
    return evaluate_by_framework(event, parse(*entries))


@pytest.fixture(autouse=True)
def fresh_warnings(monkeypatch):
    monkeypatch.setattr(rule_parser, "_WARNED", set())


# --- Documents -----------------------------------------------------------------------


def test_a_document_lists_rules_and_may_name_their_framework():
    bare = {"id": "bare", "when": {}, "map_to": ["C1"]}

    assert evaluate_by_framework({}, parse(rule())) == {"A": ["C1"]}
    assert evaluate_by_framework({}, parse(bare, framework="B")) == {"B": ["C1"]}
    # A rule's own framework comes before the document's.
    assert evaluate_by_framework({}, parse(rule(), framework="B")) == {"A": ["C1"]}
    assert evaluate_by_framework({}, parse(bare)) == {
        settings.default_framework_slug: ["C1"]
    }


@pytest.mark.parametrize(
    "document",
    [
        [rule()],
        {"frameworks": {"A": [rule()]}},
        {"A": [rule()]},
        {"rules": {"test": rule()}},
        None,
    ],
    ids=["list", "framework-buckets", "framework-keys", "rules-object", "none"],
)
def test_other_document_shapes_hold_no_rules(document):
    assert parse_rules(document) == []


def test_entries_that_are_not_objects_are_skipped_and_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="app.mapping.rules"):
        rules = parse_rules({"rules": ["test", 42, rule()]})

    assert [r.id for r in rules] == ["test"]
    assert [record.getMessage() for record in caplog.records] == [
        "Skipping mapping rule #1: a rule must be an object",
        "Skipping mapping rule #2: a rule must be an object",
    ]


def test_disabled_rules_are_left_out():
    assert parse(rule(enabled=False)) == []
    assert [r.id for r in parse(rule(enabled=True))] == ["test"]


# --- Entries Keen cannot read --------------------------------------------------------


INVALID = [
    ("misspelt-key", rule(wen={"source": "loki"}), "unsupported key wen"),
    (
        "controls-key",
        {"id": "test", "when": {}, "controls": ["C1"]},
        "unsupported key controls",
    ),
    (
        "misspelt-condition",
        rule(when={"sourse": "loki"}),
        "unsupported condition sourse",
    ),
    (
        "text-when",
        rule(when="source == loki"),
        "when must be an object of conditions",
    ),
    ("no-when", {"id": "test", "map_to": ["C1"]}, "when is required"),
    (
        "boolean-text",
        rule(when={"outcome": True}),
        "outcome must be text of up to 512 characters",
    ),
    (
        "blank-value",
        rule(when={"action": None}),
        "action must be text of up to 512 characters",
    ),
    (
        "long-text",
        rule(when={"system": "x" * 513}),
        "system must be text of up to 512 characters",
    ),
    (
        "text-severity",
        rule(when={"severity": "high"}),
        "severity must be a whole number from 0 to 1000",
    ),
    (
        "boolean-severity",
        rule(when={"severity": True}),
        "severity must be a whole number from 0 to 1000",
    ),
    (
        "large-severity",
        rule(when={"severity": 1001}),
        "severity must be a whole number from 0 to 1000",
    ),
    (
        "text-book-id",
        rule(when={"bookstack_book_id": "7"}),
        "bookstack_book_id must be a whole number from 0 to 2147483647",
    ),
    (
        "unreadable-collector",
        rule(when={"collector": "loki"}),
        "collector must be a collection identity",
    ),
    (
        "short-collector",
        rule(when={"collector": '["loki", "queries"]'}),
        "collector must be a collection identity",
    ),
    (
        "text-confidence",
        rule(confidence="high"),
        "confidence must be a number from 0 to 1",
    ),
    (
        "percentage-confidence",
        rule(confidence=80),
        "confidence must be a number from 0 to 1",
    ),
    (
        "boolean-confidence",
        rule(confidence=True),
        "confidence must be a number from 0 to 1",
    ),
    ("text-enabled", rule(enabled="false"), "enabled must be true or false"),
    ("no-targets", {"id": "test", "when": {}}, "map_to is required"),
    ("empty-targets", rule(map_to=[]), "map_to lists no targets"),
    ("empty-map", rule(map_to={}), "map_to lists no targets"),
    (
        "blank-reference",
        rule(map_to=["A.1", " "]),
        "map_to references must be text of up to 64 characters",
    ),
    (
        "number-reference",
        rule(map_to=[5.1]),
        "map_to references must be text of up to 64 characters",
    ),
    (
        # Catalogue references are stored in 64 characters.
        "long-reference",
        rule(map_to=["A" * 65]),
        "map_to references must be text of up to 64 characters",
    ),
    (
        "mixed-targets",
        rule(map_to=["A.1", {"ref": "A.2"}]),
        "map_to references must be text of up to 64 characters",
    ),
    (
        "wildcard-framework",
        rule(map_to={"*": ["W.1"]}),
        "map_to must name frameworks by their identifiers",
    ),
    (
        "targets-container",
        rule(map_to={"targets": [{"framework": "B", "ref": "B.1"}]}),
        "map_to references must be text of up to 64 characters",
    ),
    (
        "role-gate",
        rule(map_to=[{"ref": "C1", "roles_any": ["ops"]}]),
        "unsupported target key roles_any",
    ),
    (
        "framework-slug",
        rule(map_to=[{"framework_slug": "B", "ref": "B.1"}]),
        "unsupported target key framework_slug",
    ),
    (
        "ref-and-refs",
        rule(map_to=[{"ref": "C1", "refs": ["C2"]}]),
        "each target needs ref or refs",
    ),
    (
        "no-ref",
        rule(map_to=[{"framework": "B"}]),
        "each target needs ref or refs",
    ),
    (
        "unknown-kind",
        rule(map_to=[{"ref": "C1", "kind": "policy"}]),
        "a target's kind must be control or clause",
    ),
    (
        "both-frameworks",
        rule(frameworks=["B"]),
        "give framework or frameworks, as alternatives",
    ),
    (
        "blank-frameworks",
        {"id": "test", "frameworks": [" "], "when": {}, "map_to": ["C1"]},
        "frameworks must name frameworks by their identifiers",
    ),
    (
        "text-frameworks",
        {"id": "test", "frameworks": "A", "when": {}, "map_to": ["C1"]},
        "frameworks must be a list",
    ),
]


@pytest.mark.parametrize(
    "entry,problem",
    [(entry, problem) for _, entry, problem in INVALID],
    ids=[name for name, _, _ in INVALID],
)
def test_an_entry_keen_cannot_read_is_skipped_and_logged_once(caplog, entry, problem):
    with caplog.at_level(logging.WARNING, logger="app.mapping.rules"):
        first = parse(entry, rule(id="kept"))
        again = parse(entry, rule(id="kept"))

    assert [r.id for r in first] == [r.id for r in again] == ["kept"]
    assert [record.getMessage() for record in caplog.records] == [
        f"Skipping mapping rule test: {problem}"
    ]
    # The rule editor reports the same problem.
    assert rule_problem(entry) == problem


@pytest.mark.parametrize(
    "condition",
    [
        {"action_regex": "(a+)+"},
        {"fields": [{"path": ["summary"], "operator": "regex", "value": "("}]},
    ],
    ids=["unsafe-pattern", "unsafe-field-pattern"],
)
def test_unsafe_patterns_skip_their_rule(caplog, condition):
    with caplog.at_level(logging.WARNING, logger="app.mapping.rules"):
        assert parse(rule(when=condition)) == []

    (record,) = caplog.records
    key = next(iter(condition))
    assert record.getMessage().startswith(f"Skipping mapping rule test: {key}: ")


def test_the_record_of_logged_problems_stays_bounded(monkeypatch, caplog):
    # Each rule revision can bring new problems, so the record of those already
    # logged starts again once it is full.
    logged = {(f"old-{n}", "a problem") for n in range(1024)}
    monkeypatch.setattr(rule_parser, "_WARNED", logged)
    with caplog.at_level(logging.WARNING, logger="app.mapping.rules"):
        parse({**rule(id="new"), "wen": {}})

    assert logged == {("new", "unsupported key wen")}
    assert [record.getMessage() for record in caplog.records] == [
        "Skipping mapping rule new: unsupported key wen"
    ]


def test_a_reference_as_long_as_the_catalogue_allows_is_read():
    (parsed,) = parse(rule(map_to=[" " + "A" * 64 + " "]))
    assert [target.ref for target in parsed.targets] == ["A" * 64]


def test_a_readable_rule_has_no_problem():
    assert rule_problem(rule(when={"source": "loki", "severity": 3})) is None


# --- Conditions ----------------------------------------------------------------------


@pytest.mark.parametrize("field", ["source", "system", "actor", "action", "outcome"])
def test_exact_and_regex_conditions_are_combined(field):
    entry = rule(when={field: "package.upgrade", field + "_regex": r"^package\."})

    assert matched({field: "package.upgrade"}, entry) == {"A": ["C1"]}
    assert not matched({field: "package.install"}, entry)
    assert not matched({}, entry)
    other = rule(when={field: "package.upgrade", field + "_regex": r"^other\."})
    assert not matched({field: "package.upgrade"}, other)


def test_severity_zero_and_missing_are_distinct():
    entry = rule(when={"severity": 0})

    assert matched({"severity": 0}, entry) == {"A": ["C1"]}
    assert matched({}, entry) == {}
    assert matched({"severity": "not a number"}, entry) == {}


def test_nested_labels_and_summary_fallback():
    entry = rule(when={"label": "app=sshd", "summary_regex": "failure"})
    event = {"normalized_payload": {"labels": {"app": "sshd"}, "message": "failure"}}

    assert matched(event, entry) == {"A": ["C1"]}
    assert matched({**event, "summary": "success"}, entry) == {}


def test_blank_text_conditions_ask_for_nothing():
    assert matched({"action": "login"}, rule(when={"action": "  "})) == {"A": ["C1"]}


# --- Targets and provenance -----------------------------------------------------------


@pytest.mark.parametrize(
    "map_to,expected",
    [
        ("A.1", {"A": ["A.1"]}),
        ([" A.1 ", "A.2", "A.1"], {"A": ["A.1", "A.2"]}),
        ({"A": ["A.1"], "B": "B.1"}, {"A": ["A.1"], "B": ["B.1"]}),
        (
            [{"ref": "A.1"}, {"frameworks": ["B", "C"], "ref": "X.1"}],
            {"A": ["A.1"], "B": ["X.1"], "C": ["X.1"]},
        ),
        ([{"framework": "B", "refs": ["B.1", "B.2"]}], {"B": ["B.1", "B.2"]}),
        ([{"framework": "B", "ref": "9.2", "kind": " Clause "}], {"B": ["9.2"]}),
    ],
    ids=["reference", "references", "framework-map", "objects", "refs", "kind"],
)
def test_every_target_form_maps_to_explicit_framework_references(map_to, expected):
    assert matched({}, rule(map_to=map_to)) == expected


def test_a_target_kind_is_recorded_for_its_mappings():
    entry = rule(map_to=[{"ref": "9.2", "kind": "CLAUSE"}, {"ref": "9.2"}])

    details = evaluate_by_framework({}, parse(entry), details=True)

    assert [(hit["ref"], hit["kind"]) for hit in details["A"]] == [
        ("9.2", "clause"),
        ("9.2", None),
    ]


def test_multi_framework_and_duplicate_provenance_choose_highest_confidence():
    rules = parse(
        {
            "id": "low",
            "frameworks": ["A", "B"],
            "when": {},
            "map_to": ["C1", "C1"],
            "confidence": 0.2,
        },
        rule(id="high", confidence=0.9),
    )

    assert evaluate_by_framework({}, rules) == {"A": ["C1"], "B": ["C1"]}
    details = evaluate_by_framework({}, rules, details=True)
    assert details["A"] == [
        {
            "ref": "C1",
            "kind": None,
            "confidence": 0.9,
            "rationale": "auto by rule high (A)",
        }
    ]
    assert details["B"][0]["confidence"] == 0.2


def test_confidence_defaults_and_whole_numbers_are_read_as_fractions():
    (default,) = parse(rule())
    (whole,) = parse(rule(confidence=1))

    assert (default.confidence, whole.confidence) == (0.8, 1.0)


# --- Collections -----------------------------------------------------------------------


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


def test_a_rule_can_require_one_collection():
    identity = collector_id("loki", "queries", "ssh")
    entry = rule(when={"collector": identity})

    assert matched(
        {"source": "loki", "raw_pointer": {"loki": {"query_name": "ssh"}}}, entry
    )
    assert not matched(
        {"source": "loki", "raw_pointer": {"loki": {"query_name": "sudo"}}}, entry
    )


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
    entry = rule(when={"source": "bookstack", **when})

    assert bool(matched(BOOKSTACK_EVENT, entry)) is matches


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
        return bool(matched(event, rule(when=when)))

    assert matches(bookstack_page_slug="access-control", bookstack_book_id=7)
    assert not matches(bookstack_page_slug="superseded")
    assert not matches(bookstack_book_id=9)
    assert matches(bookstack_page_slug="policies-access-control")
    # Without a book slug only the page's own slug is a candidate.
    alone = {"raw_pointer": {"bookstack": {"page_slug": "access-control"}}}
    assert matched(alone, rule(when={"bookstack_page_slug_regex": "^access"}))
    assert not matched(alone, rule(when={"bookstack_page_slug_regex": "-access"}))


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
    assert matched(event, rule(when={"bookstack_page_id": 41})) == {}


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

    assert bool(matched(event, rule(when=when))) is matches
