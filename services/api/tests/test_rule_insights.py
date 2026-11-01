"""Mapping rule insights: quiet rules, their sources and consolidation suggestions.

Each test runs on SQLite and, when the contract database is configured, on
PostgreSQL, since the hourly activity is counted in SQL. The administrator
route is covered through the assembled application in test_rule_lifecycle_api.
"""

import os
import re
import uuid
from datetime import datetime, timedelta

import pytest
from app.core import cache
from app.db.models import ControlItem, Event, Framework, ManagedConfiguration, Mapping
from app.mapping.fields import field_matches
from app.mapping.rules import evaluate_by_framework, parse_rules, rule_rationale
from app.security.regex import compile_pattern
from app.services import rule_insights as insights
from app.services.errors import DomainError
from app.services.mapping_provenance import rule_of_rationale
from psycopg2 import errors
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tests.db_helpers import create_sqlite_schema

NOW = datetime(2026, 10, 10, 12, 0, 0)
FW = "fw-rule-insights"
OTHER_FW = "fw-rule-insights-other"
REFS = ("A.1", "A.2", "A.3")


@pytest.fixture(autouse=True)
def uncached(monkeypatch):
    monkeypatch.setattr(cache.settings, "aggregate_cache_ttl_seconds", 0)


@pytest.fixture(params=["sqlite", "postgresql"])
def db(request):
    if request.param == "postgresql":
        if not os.getenv("KEEN_CONTRACT_TEST_DATABASE_URL"):
            pytest.skip("Set KEEN_CONTRACT_TEST_DATABASE_URL to run on PostgreSQL")
        session = request.getfixturevalue("contract_db")
        _catalogue(session)
        yield session
        return
    engine = create_engine("sqlite://")
    create_sqlite_schema(engine)
    with Session(engine) as session:
        _catalogue(session)
        yield session
    engine.dispose()


def _catalogue(session):
    for slug in (FW, OTHER_FW):
        session.add(Framework(slug=slug))
        for ref in REFS:
            session.add(
                ControlItem(
                    framework_slug=slug, type="annex_control", ref=ref, title=ref
                )
            )
    session.flush()


def rule(rule_id, *, refs=("A.1",), confidence=0.8, enabled=True, **when):
    return {
        "id": rule_id,
        "when": {"source": "ci", **when},
        "map_to": [{"framework": FW, "ref": ref, "kind": "control"} for ref in refs],
        "confidence": confidence,
        "enabled": enabled,
    }


def save(db, *rules):
    db.add(ManagedConfiguration(name="rules", document={"rules": list(rules)}))
    db.flush()


def events(db, timestamps, *, source="ci", connection_id=None, **columns):
    rows = [
        Event(
            id=uuid.uuid4(),
            source=source,
            connection_id=connection_id,
            timestamp=timestamp,
            summary=columns.pop("summary", "deploy finished"),
            action=columns.get("action", "deploy"),
            system=columns.get("system", "build"),
            outcome="success",
            raw_pointer={},
            normalized_payload={},
            external_id=uuid.uuid4().hex,
        )
        for timestamp in timestamps
    ]
    db.add_all(rows)
    db.flush()
    return rows


def matched(db, rows, rule_id, *, refs=("A.1",), framework=FW, method="rule"):
    controls = {
        control.ref: control.id
        for control in db.query(ControlItem).filter_by(framework_slug=framework)
    }
    for row in rows:
        for ref in refs:
            db.add(
                Mapping(
                    event_id=row.id,
                    control_item_id=controls[ref],
                    method=method,
                    mapped_by="system" if method == "rule" else "auditor",
                    confidence=0.8,
                    rationale=rule_rationale(rule_id, framework),
                )
            )
    db.flush()


def hourly(count, *, end, step=timedelta(hours=1)):
    return [end - step * i for i in range(count)]


def run(db, **kwargs):
    return insights.rule_insights(db=db, now=NOW, **kwargs)


def status_of(result, rule_id):
    return next(item for item in result["rules"] if item["id"] == rule_id)


# --- Activity -------------------------------------------------------------------------


def test_a_rule_matching_at_its_usual_rhythm_is_active(db):
    save(db, rule("deploys"))
    matched(db, events(db, hourly(48, end=NOW - timedelta(minutes=30))), "deploys")

    item = status_of(run(db), "deploys")

    assert item["status"] == "active"
    assert item["window_matches"] == 48
    assert item["usual_longest_gap_seconds"] == 3600
    assert item["quiet_threshold_seconds"] == 7200
    assert item["since_last_seconds"] == 1800
    assert item["last_matched"] == (NOW - timedelta(minutes=30)).isoformat()
    assert item["message"] == "Last matched 30 minutes ago."


def test_a_rule_that_stops_while_its_source_keeps_sending_is_quiet(db):
    save(db, rule("deploys"))
    stopped = NOW - timedelta(days=2)
    matched(db, events(db, hourly(24, end=stopped)), "deploys")
    # The source keeps delivering events the rule no longer matches.
    events(db, hourly(47, end=NOW - timedelta(minutes=10)), action="release")

    item = status_of(run(db), "deploys")

    assert item["status"] == "quiet"
    assert item["source_last_seen"] == (NOW - timedelta(minutes=10)).isoformat()
    assert item["source_window_events"] == 71
    assert item["message"] == (
        "No matches for 2 days; 90% of the gaps between its matches are at most "
        "about 1 hour. ci is still sending events, the latest 10 minutes ago, so "
        "the rule may no longer describe them."
    )


def test_a_rule_whose_source_went_quiet_points_at_the_source(db):
    save(db, rule("deploys"), rule("releases", action="release"))
    stopped = NOW - timedelta(days=2)
    matched(db, events(db, hourly(24, end=stopped)), "deploys")
    # Events after the rule's last match that are themselves long past.
    later = events(db, hourly(3, end=stopped + timedelta(hours=6)), action="release")
    matched(db, later, "releases")

    result = run(db)

    assert status_of(result, "deploys")["status"] == "source_quiet"
    assert status_of(result, "deploys")["message"] == (
        "No matches for 2 days; 90% of the gaps between its matches are at most "
        "about 1 hour. ci has sent no events for 1 day 18 hours either; review the "
        "source before the rule."
    )
    assert status_of(result, "releases")["status"] == "source_quiet"


def test_a_rule_names_only_its_connection_when_judging_the_source(db):
    save(db, rule("first", connection_id="c1"), rule("other", connection_id="c2"))
    stopped = NOW - timedelta(days=2)
    matched(db, events(db, hourly(24, end=stopped), connection_id="c1"), "first")
    matched(db, events(db, hourly(24, end=stopped), connection_id="c2"), "other")
    events(db, hourly(5, end=NOW - timedelta(minutes=5)), connection_id="c2")

    result = run(db)

    first, other = status_of(result, "first"), status_of(result, "other")
    assert (first["status"], first["source_window_events"]) == ("source_quiet", 24)
    assert (other["status"], other["source_window_events"]) == ("quiet", 29)
    assert first["connection_id"] == "c1"


def test_rules_without_matches_say_whether_their_source_sent_anything(db):
    save(db, rule("unmatched"), rule("elsewhere", source="cd"))
    events(db, [NOW - timedelta(days=1)])

    result = run(db, days=30)

    assert status_of(result, "unmatched") | {"message": None} == {
        "id": "unmatched",
        "enabled": True,
        "source": "ci",
        "connection_id": None,
        "status": "silent",
        "window_matches": 0,
        "last_matched": None,
        "since_last_seconds": None,
        "usual_longest_gap_seconds": None,
        "quiet_threshold_seconds": None,
        "source_last_seen": (NOW - timedelta(days=1)).isoformat(),
        "source_window_events": 1,
        "message": None,
    }
    assert status_of(result, "unmatched")["message"] == (
        "No matches in the last 30 days, while ci sent 1 event. Preview the rule "
        "to compare its conditions with recent events."
    )
    assert status_of(result, "elsewhere")["message"] == (
        "No matches in the last 30 days; cd sent no events in that time."
    )


def test_a_rule_with_few_matches_has_no_baseline(db):
    save(db, rule("rare"))
    matched(db, events(db, [NOW - timedelta(days=3), NOW - timedelta(days=9)]), "rare")

    item = status_of(run(db), "rare")

    assert item["status"] == "insufficient"
    assert item["usual_longest_gap_seconds"] is None
    assert item["message"] == (
        "Last matched 3 days ago. Fewer than 3 matches in the last 90 days, so there "
        "is no baseline to compare against yet."
    )


def test_a_paused_rule_is_reported_as_paused_whatever_it_matched(db):
    save(db, rule("paused", enabled=False))
    matched(db, events(db, hourly(5, end=NOW - timedelta(days=4))), "paused")

    item = status_of(run(db), "paused")

    assert item["status"] == "paused"
    assert item["window_matches"] == 5
    assert item["message"] == "Paused. Keen skips this rule while collecting evidence."


def test_a_file_managed_rule_without_a_source_is_judged_on_its_matches_alone(db):
    save(
        db,
        {"id": "anywhere", "when": {}, "map_to": [{"framework": FW, "ref": "A.1"}]},
        {"id": "nowhere", "when": {"action": "x"}, "map_to": ["A.1"]},
    )
    matched(db, events(db, hourly(5, end=NOW - timedelta(days=3))), "anywhere")

    result = run(db)

    assert status_of(result, "anywhere")["status"] == "quiet"
    assert status_of(result, "anywhere")["message"] == (
        "No matches for 3 days; 90% of the gaps between its matches are at most "
        "about 1 hour."
    )
    assert status_of(result, "nowhere")["message"] == "No matches in the last 90 days."


def test_an_event_counts_once_however_many_controls_and_frameworks_it_maps(db):
    save(db, rule("deploys", refs=REFS))
    rows = events(db, hourly(6, end=NOW - timedelta(minutes=20)))
    matched(db, rows, "deploys", refs=REFS)
    matched(db, rows[:4], "deploys", refs=("A.1",), framework=OTHER_FW)

    assert status_of(run(db), "deploys")["window_matches"] == 6


def test_only_this_rules_mappings_in_the_window_count(db):
    save(db, rule("deploys"))
    inside = events(db, hourly(3, end=NOW - timedelta(hours=2)))
    matched(db, inside, "deploys")
    # Another rule, a reviewer, a removed rule's lookalike and the edges of the window.
    matched(db, events(db, [NOW - timedelta(hours=1)]), "deploys-old")
    matched(db, events(db, [NOW - timedelta(minutes=30)]), "deploys", method="manual")
    matched(db, events(db, [NOW - timedelta(days=91)]), "deploys")
    matched(db, events(db, [NOW + timedelta(minutes=5)]), "deploys")

    item = status_of(run(db), "deploys")

    assert item["window_matches"] == 3
    assert item["last_matched"] == (NOW - timedelta(hours=2)).isoformat()


def test_the_window_must_be_between_seven_and_365_days(db):
    save(db, rule("deploys"))
    for days in (6, 366, "many"):
        with pytest.raises(DomainError) as refused:
            run(db, days=days)
        assert refused.value.status_code == 400
    assert run(db, days=7)["days"] == 7


@pytest.mark.parametrize(
    "hours,expected",
    [
        # An hourly job: each gap is an hour.
        ({0: 1, 1: 1, 2: 1, 3: 1}, 3600),
        # Ten matches an hour: most gaps are a few minutes.
        ({0: 10, 1: 10}, 360),
        # A daily job.
        ({0: 1, 24: 1, 48: 1, 72: 1}, 86400),
        # Bursts a day apart: the 90th percentile is the day between them.
        ({0: 3, 24: 3, 48: 3}, 86400),
    ],
)
def test_the_usual_longest_gap_is_estimated_from_hourly_counts(hours, expected):
    assert insights._usual_longest_gap(hours) == expected


@pytest.mark.parametrize(
    "rationale,expected",
    [
        ("auto by rule deploys (iso_27001_2022)", "deploys"),
        ("auto by rule webhook:hook-af (KEEN-AF:1.0)", "webhook:hook-af"),
        ("auto by rule odd (name) (fw)", "odd (name)"),
        ("auto by rule  (fw)", None),
        ("auto by rule deploys", None),
        ("auto by rule deploys ()", None),
        ("Reviewed by an auditor", None),
    ],
)
def test_a_rationale_names_its_rule(rationale, expected):
    assert rule_of_rationale(rationale) == expected


# --- Consolidation ----------------------------------------------------------------------


def suggestions(db, *rules):
    save(db, *rules)
    return run(db)["consolidation"]


def test_rules_with_the_same_conditions_and_controls_are_duplicates(db):
    found = suggestions(
        db,
        rule("first", confidence=0.6, action="deploy"),
        rule("second", confidence=0.9, action="deploy"),
        rule("paused", enabled=False, action="deploy"),
    )

    assert found == [
        {
            "kind": "duplicate",
            "rules": ["first", "second"],
            "keep": "second",
            "message": (
                "These rules have the same conditions and map the same controls, so "
                "“second” alone records the same evidence."
            ),
        }
    ]


def test_rules_with_the_same_conditions_and_other_controls_could_be_one(db):
    found = suggestions(
        db,
        rule("iso", refs=("A.1",), action="deploy"),
        rule("other", refs=("A.2",), action="deploy"),
        rule("third", refs=("A.3",), confidence=0.5, action="deploy"),
    )

    assert [item["kind"] for item in found] == ["same_conditions"]
    assert found[0]["rules"] == ["iso", "other", "third"]
    assert found[0]["message"] == (
        "These rules have the same conditions and map different controls. One rule "
        "can map all of their controls; they use different confidences, so choose one."
    )


def test_rules_with_one_confidence_need_no_choice_when_combined(db):
    found = suggestions(db, rule("iso", refs=("A.1",)), rule("other", refs=("A.2",)))

    assert [item["kind"] for item in found] == ["same_conditions"]
    assert found[0]["message"].endswith("One rule can map all of their controls.")


def test_a_narrower_rule_adding_no_controls_or_confidence_is_covered(db):
    found = suggestions(
        db,
        rule("broad", refs=("A.1", "A.2"), confidence=0.8),
        rule("narrow", refs=("A.1",), confidence=0.7, action="deploy"),
        # A narrower rule that adds a control, or raises the confidence, stays useful.
        rule("adds-control", refs=("A.3",), action="deploy"),
        rule("more-confident", refs=("A.1",), confidence=0.9, action="deploy"),
    )

    covered = [item for item in found if item["kind"] == "covered"]
    assert covered == [
        {
            "kind": "covered",
            "rules": ["narrow", "broad"],
            "message": (
                "Every event “narrow” matches also matches “broad”, which maps every "
                "control “narrow” maps with at least the same confidence, so "
                "“narrow” adds no evidence."
            ),
        }
    ]


def test_a_file_managed_rule_naming_no_source_can_cover_any_rule(db):
    anywhere = {**rule("anywhere"), "when": {}}
    found = suggestions(db, anywhere, rule("narrow", action="deploy"))

    assert [(item["kind"], item["rules"]) for item in found] == [
        ("covered", ["narrow", "anywhere"])
    ]


def test_a_paused_broader_rule_covers_nothing(db):
    found = suggestions(
        db,
        rule("broad", enabled=False),
        rule("narrow", action="deploy"),
    )

    assert found == []


def test_field_conditions_compare_as_a_set(db):
    payload = {"path": ["user", "role"], "operator": "equals", "value": "admin"}
    scoped = {
        "path": ["summary"],
        "operator": "regex",
        "value": "^ok",
        "scope": "event",
    }
    found = suggestions(
        db,
        rule("broad", fields=[payload]),
        rule("narrow", refs=("A.1",), confidence=0.5, fields=[scoped, payload]),
    )

    assert [(item["kind"], item["rules"]) for item in found] == [
        ("covered", ["narrow", "broad"])
    ]


def same_controls(found):
    return [item for item in found if item["kind"] == "same_controls"]


def test_rules_differing_in_one_value_could_share_one_pattern(db):
    found = suggestions(
        db,
        rule("login", action="login"),
        rule("logout", action="logout"),
        rule("sso", action_regex="^sso-"),
        # Another source needs its own rule, whatever its conditions.
        rule("elsewhere", source="cd", action="login"),
    )

    [group] = same_controls(found)
    assert group["rules"] == ["login", "logout", "sso"]
    assert group["message"] == (
        "These rules map the same controls from the same source with different "
        "conditions. Rules that differ in one condition can share a pattern."
    )
    assert group["combine"] == [
        {
            "rules": ["login", "logout", "sso"],
            "condition": "Activity",
            "replace": {"action_regex": "\\A(?:login|logout)\\Z|(?:^sso-)"},
            "values": [
                {"form": "exact", "value": "login"},
                {"form": "exact", "value": "logout"},
                {"form": "regex", "value": "^sso-"},
            ],
            "pattern": "\\A(?:login|logout)\\Z|(?:^sso-)",
        }
    ]


def _matches_any(rules, event):
    return bool(evaluate_by_framework(event, parse_rules({"rules": rules})))


@pytest.mark.parametrize(
    "originals,condition",
    [
        (
            [
                {"action": "log.in"},
                {"action": "log+out"},
                {"action_regex": "^sso-[a-z]+$"},
            ],
            "action",
        ),
        ([{"system": "build (1)"}, {"system": "*star*"}], "system"),
        # Characters with meaning only in a class, a verbose pattern or another flavour.
        ([{"system": "x{2}[a]^$"}, {"system": "a-b #c &d ~e"}], "system"),
        ([{"outcome": "ok\\no"}, {"outcome": "a|b"}], "outcome"),
        ([{"actor": "alice"}, {"actor_regex": "^bot-"}], "actor"),
        ([{"label_regex": "^env=prod$"}, {"label_regex": "^tier="}], "label"),
        ([{"summary_regex": "deployed$"}, {"summary_regex": "^rollback"}], "summary"),
    ],
)
def test_a_combined_pattern_matches_exactly_what_its_rules_matched(
    db, originals, condition
):
    rules = [rule(f"r{n}", **when) for n, when in enumerate(originals)]
    [group] = same_controls(suggestions(db, *rules))
    [combined] = group["combine"]
    merged = {**rule("merged"), "when": {"source": "ci", **combined["replace"]}}

    values = [
        "log.in", "log+out", "sso-abc", "sso-", "logXin", "log.inx", "xlog.in",
        "log.in\n", "build (1)", "build 1", "*star*", "star", "ok\\no", "ok\no",
        "a|b", "a", "b", "alice", "alice2", "bot-1", "env=prod", "env=prod2",
        "tier=web", "rolled back", "rollback done", "service deployed", "",
        "x{2}[a]^$", "xx[a]", "xxa", "x{2}a", "a-b #c &d ~e", "a-b#c&d~e", "ab",
    ]  # fmt: skip
    for value in values:
        event = {
            "source": "ci",
            condition: value,
            "summary": value,
            "labels": [value],
            "raw_pointer": {"labels": [value]},
        }
        assert _matches_any([merged], event) == _matches_any(rules, event), value
    compile_pattern(combined["pattern"])


def test_payload_field_patterns_can_share_one_pattern(db):
    path = ["user", "role"]
    rules = [
        rule(f"r{n}", fields=[{"path": path, "operator": "regex", "value": value}])
        for n, value in enumerate(("^a\\.b$", "^c\\+d$"))
    ]
    [group] = same_controls(suggestions(db, *rules))
    [combined] = group["combine"]

    assert combined["condition"] == "Payload field user.role"
    [item] = combined["replace"]["fields"]
    assert item == {
        "path": path,
        "operator": "regex",
        "value": "(?:^a\\.b$)|(?:^c\\+d$)",
    }
    for value in ("a.b", "c+d", "aXb", "a.bc", "c+dd", "cd", ""):
        payload = {"user": {"role": value}}
        assert field_matches(payload, item) == (value in ("a.b", "c+d"))


@pytest.mark.parametrize(
    "conditions",
    [
        [{"label": "env=prod"}, {"label": "tier=web"}],
        [{"label": "env=prod"}, {"label_regex": "^tier="}],
        [
            {"fields": [{"path": ["role"], "operator": "equals", "value": "a"}]},
            {"fields": [{"path": ["role"], "operator": "equals", "value": "b"}]},
        ],
    ],
    ids=["labels", "label-and-pattern", "payload-fields"],
)
def test_exact_values_of_unbounded_conditions_stay_exact(db, conditions):
    # A pattern stops on long input where an exact comparison never fails.
    rules = [rule(f"r{n}", **when) for n, when in enumerate(conditions)]

    [group] = same_controls(suggestions(db, *rules))

    assert group["combine"] == []


def test_the_event_summary_field_keeps_its_scope_in_a_combined_condition(db):
    rules = [
        rule(f"r{n}", fields=[{"path": ["summary"], "operator": "regex", "value": value, "scope": "event"}])
        for n, value in enumerate(("^deployed", "^released"))
    ]  # fmt: skip
    [group] = same_controls(suggestions(db, *rules))
    [combined] = group["combine"]

    assert combined["condition"] == "Event field summary"
    assert combined["replace"] == {
        "fields": [
            {
                "path": ["summary"],
                "operator": "regex",
                "value": "(?:^deployed)|(?:^released)",
                "scope": "event",
            }
        ]
    }


def test_rules_whose_patterns_cannot_be_joined_get_no_combined_pattern(db):
    long = "x" * 300
    found = suggestions(
        db, rule("first", summary_regex=long), rule("second", summary_regex=long + "y")
    )

    assert [group["combine"] for group in same_controls(found)] == [[]]


def test_rules_differing_in_several_ways_are_listed_without_a_pattern(db):
    found = suggestions(
        db,
        rule("first", action="login", outcome="success"),
        rule("second", action="logout", outcome="failure"),
        rule("both-at-once", action="login", outcome="success", actor="alice"),
    )

    groups = same_controls(found)
    assert [(group["rules"], group["combine"]) for group in groups] == [
        (["first", "second", "both-at-once"], [])
    ]
    assert groups[0]["message"].endswith(
        "Their conditions differ in more than one way, so review them together "
        "before combining any."
    )


def test_conditions_that_do_not_combine_are_left_alone(db):
    found = suggestions(
        db,
        # A rule with both forms of one condition, and an operator without a pattern form.
        rule("both-forms", action="login", action_regex="^log"),
        rule("other", action="logout"),
        rule(
            "contains", fields=[{"path": ["a"], "operator": "contains", "value": "x"}]
        ),
        rule(
            "contains-2", fields=[{"path": ["a"], "operator": "contains", "value": "y"}]
        ),
    )

    assert [group["combine"] for group in same_controls(found)] == [[]]


def test_a_combined_pattern_the_editor_would_refuse_is_not_suggested():
    long = "x" * 300
    assert insights.combined_pattern([("regex", long), ("regex", long + "y")]) is None
    assert (
        insights.combined_pattern([("exact", "a"), ("exact", "b")]) == "\\A(?:a|b)\\Z"
    )


def test_rules_already_explained_are_not_listed_again_as_sharing_controls(db):
    found = suggestions(
        db,
        rule("broad", refs=("A.1",)),
        rule("narrow", refs=("A.1",), action="deploy"),
    )

    assert [item["kind"] for item in found] == ["covered"]


# --- Overlapping matches ----------------------------------------------------------------


def test_rules_matching_mostly_the_same_events_are_paired(db):
    save(
        db,
        rule("by-action", refs=("A.1",), action="deploy"),
        rule("by-system", refs=("A.2",), system="build"),
        rule("by-outcome", refs=("A.3",), outcome="success"),
    )
    rows = events(db, hourly(13, end=NOW - timedelta(minutes=5)))
    matched(db, rows, "by-action", refs=("A.1",))
    matched(db, rows[:12], "by-system", refs=("A.2",))
    # A second framework of the same rule counts the same events.
    matched(db, rows[:12], "by-system", refs=("A.1",), framework=OTHER_FW)
    # Nine shared events are too few to call an overlap.
    matched(db, rows[:9], "by-outcome", refs=("A.3",))

    overlaps = run(db)["overlaps"]

    assert overlaps == [
        {
            "rules": ["by-system", "by-action"],
            "shared_events": 12,
            "events": {"by-action": 13, "by-system": 12},
            "share_pct": 100,
            "message": (
                "“by-action” also matched 12 of the 12 events “by-system” matched in "
                "the last 90 days. Check whether one rule could map both sets of "
                "controls."
            ),
        }
    ]


def test_an_overlap_covers_most_of_the_less_active_rules_matches(db):
    save(
        db,
        rule("a", refs=("A.1",), action="deploy"),
        rule("b", refs=("A.2",), system="build"),
        rule("c", refs=("A.3",), outcome="success"),
    )
    rows = events(db, hourly(30, end=NOW - timedelta(minutes=5)))
    matched(db, rows[:15], "a", refs=("A.1",))
    matched(db, rows[:15], "b", refs=("A.2",))
    # Ten of a's fifteen events is below the share that counts as an overlap.
    matched(db, rows[5:25], "c", refs=("A.3",))

    assert [item["rules"] for item in run(db)["overlaps"]] == [["a", "b"]]


def test_rules_known_to_share_conditions_are_not_paired_again(db):
    save(db, rule("iso", refs=("A.1",)), rule("other", refs=("A.2",)))
    rows = events(db, hourly(12, end=NOW - timedelta(minutes=5)))
    matched(db, rows, "iso", refs=("A.1",))
    matched(db, rows, "other", refs=("A.2",))

    result = run(db)

    assert [item["kind"] for item in result["consolidation"]] == ["same_conditions"]
    assert result["overlaps"] == []


def test_overlaps_need_enough_shared_events_and_active_rules(db):
    save(
        db,
        rule("a", refs=("A.1",), action="deploy"),
        rule("b", refs=("A.2",), system="build"),
        rule("c", refs=("A.3",), enabled=False, outcome="success"),
    )
    rows = events(db, hourly(9, end=NOW - timedelta(minutes=5)))
    for rule_id, ref in (("a", "A.1"), ("b", "A.2"), ("c", "A.3")):
        matched(db, rows, rule_id, refs=(ref,))
    many = events(db, hourly(20, end=NOW - timedelta(minutes=5)))
    matched(db, many, "c", refs=("A.3",))
    matched(db, many, "a", refs=("A.1",))

    # a and b share nine events; c is paused, so its overlap with a is left out.
    assert run(db)["overlaps"] == []


OUTRANKED = (
    " Where both rules match an event, “{}” records the controls they share, so "
    "matches of this rule may go unrecorded."
)


def test_a_rule_another_rule_outranks_is_told_where_its_matches_went(db):
    save(
        db,
        rule("broad", refs=("A.1",)),
        rule("narrow", refs=("A.1",), action="deploy"),
    )
    # Every event records A.1 once, under the first of two equally confident rules.
    matched(db, events(db, hourly(5, end=NOW - timedelta(minutes=5))), "broad")
    events(db, hourly(3, end=NOW - timedelta(minutes=2)))

    result = run(db)

    assert status_of(result, "broad")["status"] == "active"
    assert status_of(result, "narrow")["message"] == (
        "No matches in the last 90 days, while ci sent 8 events. Preview the rule "
        "to compare its conditions with recent events." + OUTRANKED.format("broad")
    )


def test_a_quiet_copy_is_told_where_its_matches_went(db):
    save(db, rule("first"), rule("copy", confidence=0.5))
    stopped = NOW - timedelta(days=2)
    matched(db, events(db, hourly(24, end=stopped)), "copy", refs=("A.2",))
    events(db, hourly(3, end=NOW - timedelta(minutes=2)))

    item = status_of(run(db), "copy")

    assert item["status"] == "quiet"
    assert item["message"].endswith(
        "so the rule may no longer describe them." + OUTRANKED.format("first")
    )


def test_rules_sharing_a_control_are_told_which_rule_comes_first(db):
    save(
        db,
        rule("logins", refs=("A.1", "A.2"), action="login"),
        rule("alice", refs=("A.1",), actor="alice"),
        # A later, more confident rule comes first.
        rule("confident", refs=("A.2",), confidence=0.9, outcome="success"),
        rule("elsewhere", refs=("A.1",), source="cd"),
        rule("own-control", refs=("A.3",), actor="bob"),
        rule("paused", refs=("A.1",), confidence=1.0, enabled=False),
    )

    result = run(db)

    def first_rule(item):
        found = re.search(
            r"Where both rules match an event, “(.+)” records", item["message"]
        )
        return found and found.group(1)

    assert {item["id"]: first_rule(item) for item in result["rules"]} == {
        "logins": "confident",
        "alice": "logins",
        "confident": None,
        "elsewhere": None,
        "own-control": None,
        "paused": None,
    }


def test_an_existence_check_with_a_stray_value_is_compared_without_it(db):
    exists = {"path": ["host"], "operator": "exists", "value": ["ignored"]}
    found = suggestions(
        db,
        rule("first", fields=[exists]),
        rule("second", fields=[{**exists, "value": "other"}]),
    )

    assert [item["kind"] for item in found] == ["duplicate"]


def test_slow_insights_stop_and_say_so(db, monkeypatch):
    save(db, rule("deploys"))
    postgres = db.get_bind().dialect.name == "postgresql"
    monkeypatch.setattr(insights, "QUERY_TIMEOUT_MS", 1)

    def slow(db, *args):
        if postgres:
            db.execute(text("SELECT pg_sleep(0.2)"))
        raise OperationalError(
            "SELECT 1", {}, errors.QueryCanceled("canceling statement")
        )

    monkeypatch.setattr(insights, "_rule_activity", slow)
    with pytest.raises(DomainError) as stopped:
        run(db)

    assert (stopped.value.status_code, stopped.value.detail) == (
        503,
        "Rule insights took too long to work out; try a shorter window",
    )
    # The session is usable again, and the budget ended with the transaction.
    assert db.execute(text("SELECT 1")).scalar() == 1
    if postgres:
        assert db.execute(text("SHOW statement_timeout")).scalar() == "0"


def test_other_database_failures_are_not_reported_as_slowness(db, monkeypatch):
    save(db, rule("deploys"))
    failure = OperationalError("SELECT 1", {}, errors.AdminShutdown("terminating"))

    def failing(*args):
        raise failure

    monkeypatch.setattr(insights, "_rule_activity", failing)
    with pytest.raises(OperationalError) as raised:
        run(db)
    assert raised.value is failure


def test_insights_report_the_rules_version_and_window(db):
    save(db, rule("deploys"))
    db.get(ManagedConfiguration, "rules").version = 4
    db.flush()

    result = run(db, days=14)

    assert {
        key: result[key] for key in ("version", "days", "window_start", "as_of")
    } == {
        "version": 4,
        "days": 14,
        "window_start": (NOW - timedelta(days=14)).isoformat(),
        "as_of": NOW.isoformat(),
    }
