"""Activity and consolidation insights for mapping rules.

A rule's activity comes from the rule mappings it made for events in the
window, judged against its own rhythm with the quiet test Sources use: a rule
is quiet once its current silence is more than twice its usual longest gap
between matches, and more than an hour. Its source is checked as well, so a
rule that stopped matching while its source kept sending events is told apart
from one whose source went quiet.

Consolidation suggestions compare the saved rules with each other, and the
events they matched with each other. They are suggestions for a person to
review; nothing here changes a rule or a mapping.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from datetime import datetime, timedelta
from itertools import accumulate
from typing import Any

from sqlalchemy import BigInteger, cast, extract, func, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, aliased

from app.core.cache import cached_json
from app.core.datetime_utils import utc_now_naive
from app.db.models import Event, Mapping
from app.security.regex import validate_pattern
from app.services.errors import DomainError
from app.services.event_filters import statement_cancelled
from app.services.managed_configurations import get_rules
from app.services.mapping_provenance import rule_of_rationale
from app.services.source_insights import (
    DEFAULT_DAYS,
    MIN_EVENTS_FOR_BASELINE,
    QUIET_FLOOR_SECONDS,
    clean_days,
    humanise_seconds,
)

# Two rules overlap when at least this share of the events the less active
# rule matched were matched by both, and at least this many events.
OVERLAP_SHARE = 0.8
OVERLAP_MIN_EVENTS = 10
SUGGESTION_LIMIT = 50
# On PostgreSQL, each insights query stops after this long.
QUERY_TIMEOUT_MS = 15000

# Characters a pattern reads as syntax outside a character class; any other
# character matches itself, so exact values escape only these and stay legible.
_SYNTAX = re.compile(r"([\\.^$*+?{}\[\]|()])")

# Conditions that hold one value, in exact and pattern forms, and that two
# rules can share as one pattern. The exact form compares the whole value.
# A pattern stops a rule's run on input over 65,536 characters or past its time
# budget, where an exact comparison never fails, so exact values join a pattern
# only for the event columns that hold at most 128 characters.
_BOUNDED = {"system", "actor", "action", "outcome"}
_COMBINABLE = {
    "system": "System",
    "actor": "Person/account",
    "action": "Activity",
    "outcome": "Result",
    "label": "Label",
}


def rule_insights(
    *, days: int = DEFAULT_DAYS, db: Session, now: datetime | None = None
):
    """Each rule's recent activity, and the rules that could be consolidated."""
    window_days = clean_days(days)
    clock = now or utc_now_naive()
    rules = get_rules(db=db)
    parts = {
        "version": rules["version"],
        "days": window_days,
        # Fresh enough for a page load, shared across reloads within the bucket.
        "bucket": clock.strftime("%Y%m%d%H%M")[:-1],
    }
    return cached_json(
        "mapping-rules:insights",
        parts,
        lambda: _compute(rules, window_days, clock, db),
    )


def _compute(rules: dict, window_days: int, clock: datetime, db: Session) -> dict:
    if db.get_bind().dialect.name == "postgresql":
        # The budget ends with the request's transaction.
        db.execute(text(f"SET LOCAL statement_timeout = {int(QUERY_TIMEOUT_MS)}"))
    try:
        return _insights(rules, window_days, clock, db)
    except OperationalError as exc:
        if not statement_cancelled(exc):
            raise
        db.rollback()
        raise DomainError(
            503, "Rule insights took too long to work out; try a shorter window"
        ) from None


def _insights(rules: dict, window_days: int, clock: datetime, db: Session) -> dict:
    items = rules["items"]
    start = clock - timedelta(days=window_days)
    ids = {rule["id"] for rule in items}
    activity = _rule_activity(db, ids, start, clock)
    sources = _source_activity(db, items, start, clock)
    enabled = [rule for rule in items if rule["enabled"]]
    consolidation = _consolidation(enabled)
    outranked = _outranked(enabled)
    statuses = [
        _status(
            rule,
            activity.get(rule["id"]),
            sources,
            clock,
            window_days,
            outranked.get(rule["id"]),
        )
        for rule in items
    ]
    # Rules already suggested for consolidation are not paired again.
    paired = {
        frozenset(pair) for item in consolidation for pair in _pairs(item["rules"])
    }
    return {
        "version": rules["version"],
        "days": window_days,
        "window_start": start.isoformat(),
        "as_of": clock.isoformat(),
        "rules": statuses,
        "consolidation": consolidation[:SUGGESTION_LIMIT],
        "overlaps": _overlaps(db, enabled, activity, start, clock, paired, window_days)[
            :SUGGESTION_LIMIT
        ],
    }


def _names(rules: list[dict]) -> dict[str, str]:
    """How a message names each rule: by its description, or else its ID."""
    return {rule["id"]: f"“{rule.get('description') or rule['id']}”" for rule in rules}


def _outranked(rules: list[dict]) -> dict[str, str]:
    """For each rule, how messages name a rule that records their shared controls first.

    An event records each control once, under the most confident matching rule
    or, among equally confident rules, the first; a rule that shares a control
    with one that outranks it can match events without recording them.
    """
    position = {id(rule): index for index, rule in enumerate(rules)}
    ranked = sorted(rules, key=lambda rule: (-rule["confidence"], position[id(rule)]))
    rank = {id(rule): index for index, rule in enumerate(ranked)}
    targets = {id(rule): _targets(rule) for rule in rules}
    name = _names(rules)
    found: dict[str, str] = {}
    for rule in rules:
        source = rule["when"].get("source")
        first = next(
            (
                other
                for other in ranked[: rank[id(rule)]]
                if (source is None or other["when"].get("source") in (None, source))
                and targets[id(other)] & targets[id(rule)]
            ),
            None,
        )
        if first is not None:
            found[rule["id"]] = name[first["id"]]
    return found


def _pairs(ids: list[str]):
    return ((a, b) for index, a in enumerate(ids) for b in ids[index + 1 :])


# --- Activity ----------------------------------------------------------------------


def _rule_activity(db: Session, ids: set[str], start, clock) -> dict[str, dict]:
    """Matched events per hour and the latest match, for each rule.

    Hours are counted from the Unix epoch. A rule maps an event once for each
    framework it targets, so each hour counts the events of the rule's busiest
    framework in that hour.
    """
    hour = cast(extract("epoch", Event.timestamp), BigInteger) // 3600
    rows = (
        db.query(
            Mapping.rationale,
            hour,
            func.count(func.distinct(Mapping.event_id)),
            func.max(Event.timestamp),
        )
        .join(Event, Event.id == Mapping.event_id)
        .filter(
            Mapping.method == "rule",
            Mapping.mapped_by == "system",
            Event.timestamp >= start,
            Event.timestamp <= clock,
        )
        .group_by(Mapping.rationale, hour)
    )
    activity: dict[str, dict] = {}
    for rationale, bucket, count, last in rows:
        rule_id = rule_of_rationale(rationale)
        if rule_id not in ids:
            continue
        entry = activity.setdefault(rule_id, {"hours": {}, "last": last})
        entry["hours"][bucket] = max(entry["hours"].get(bucket, 0), int(count))
        entry["last"] = max(entry["last"], last)
    for entry in activity.values():
        entry["events"] = sum(entry["hours"].values())
    return activity


def _source_activity(db: Session, rules: list[dict], start, clock) -> dict:
    """The latest event and the event count per source and connection."""
    names = sorted(
        {rule["when"]["source"] for rule in rules if rule["when"].get("source")}
    )
    if not names:
        return {}
    rows = (
        db.query(
            Event.source,
            Event.connection_id,
            func.max(Event.timestamp),
            func.count(Event.id),
        )
        .filter(
            Event.source.in_(names), Event.timestamp >= start, Event.timestamp <= clock
        )
        .group_by(Event.source, Event.connection_id)
    )
    found: dict[str, dict] = {}
    for source, connection_id, last, count in rows:
        found.setdefault(source, {})[connection_id] = (last, int(count))
    return found


def _usual_longest_gap(hours: dict[int, int]) -> float:
    """The 90th percentile of the gaps between matches, estimated from hourly counts.

    Matches within an hour are taken as evenly spaced through it, and the gap
    between two busy hours as the distance between them.
    """
    ordered = sorted(hours.items())
    gaps = [(3600 / count, count - 1) for _, count in ordered if count > 1]
    gaps += [
        ((later - earlier) * 3600, 1)
        for (earlier, _), (later, _) in zip(ordered, ordered[1:])
    ]
    gaps.sort()
    # The position the Sources percentile reads, counting each gap by its weight.
    counted = list(accumulate(weight for _, weight in gaps))
    return gaps[bisect_right(counted, round(0.9 * (counted[-1] - 1)))][0]


def _status(rule, activity, sources, clock, window_days, outranked_by) -> dict:
    when = rule["when"]
    source = when.get("source")
    connections = (sources.get(source) or {}) if source else {}
    if when.get("connection_id"):
        seen = (
            [connections[when["connection_id"]]]
            if when["connection_id"] in connections
            else []
        )
    else:
        seen = list(connections.values())
    source_last = max((last for last, _ in seen), default=None)
    source_events = sum(count for _, count in seen)
    result: dict[str, Any] = {
        "id": rule["id"],
        "enabled": rule["enabled"],
        "source": source,
        "connection_id": when.get("connection_id"),
        "window_matches": activity["events"] if activity else 0,
        "last_matched": activity["last"].isoformat() if activity else None,
        "since_last_seconds": None,
        "usual_longest_gap_seconds": None,
        "quiet_threshold_seconds": None,
        "source_last_seen": source_last.isoformat() if source_last else None,
        "source_window_events": source_events,
    }
    period = f"the last {window_days} days"
    recorded_elsewhere = (
        f" Where both rules match an event, {outranked_by} records the controls "
        "they share, so matches of this rule may go unrecorded."
        if outranked_by
        else ""
    )
    if not rule["enabled"]:
        return {
            **result,
            "status": "paused",
            "message": "Paused. Keen skips this rule while collecting evidence.",
        }
    if activity is None:
        if not source:
            message = f"No matches in {period}."
        elif source_events:
            message = (
                f"No matches in {period}, while {source} sent {source_events:,} "
                f"event{'' if source_events == 1 else 's'}. Preview the rule to "
                "compare its conditions with recent events."
            )
        else:
            message = f"No matches in {period}; {source} sent no events in that time."
        return {**result, "status": "silent", "message": message + recorded_elsewhere}
    since_last = max(0.0, (clock - activity["last"]).total_seconds())
    result["since_last_seconds"] = round(since_last)
    if activity["events"] < MIN_EVENTS_FOR_BASELINE:
        return {
            **result,
            "status": "insufficient",
            "message": (
                f"Last matched {humanise_seconds(since_last)} ago. Fewer than "
                f"{MIN_EVENTS_FOR_BASELINE} matches in {period}, so there is no "
                "baseline to compare against yet."
            ),
        }
    usual = _usual_longest_gap(activity["hours"])
    threshold = max(2 * usual, QUIET_FLOOR_SECONDS)
    result["usual_longest_gap_seconds"] = round(usual)
    result["quiet_threshold_seconds"] = round(threshold)
    if since_last <= threshold:
        return {
            **result,
            "status": "active",
            "message": f"Last matched {humanise_seconds(since_last)} ago.",
        }
    quiet = (
        f"No matches for {humanise_seconds(since_last)}; 90% of the gaps between "
        f"its matches are at most about {humanise_seconds(usual)}."
    )
    if not source:
        return {**result, "status": "quiet", "message": quiet + recorded_elsewhere}
    source_silence = (
        (clock - source_last).total_seconds() if source_last is not None else None
    )
    if (
        source_silence is not None
        and source_last > activity["last"]
        and source_silence <= threshold
    ):
        return {
            **result,
            "status": "quiet",
            "message": (
                f"{quiet} {source} is still sending events, the latest "
                f"{humanise_seconds(source_silence)} ago, so the rule may no longer "
                "describe them." + recorded_elsewhere
            ),
        }
    silent_source = (
        f"{source} sent no events in {period}"
        if source_silence is None
        else f"{source} has sent no events for {humanise_seconds(source_silence)} either"
    )
    return {
        **result,
        "status": "source_quiet",
        "message": f"{quiet} {silent_source}; review the source before the rule.",
    }


# --- Consolidation ------------------------------------------------------------------


def _atoms(when: dict) -> frozenset:
    """A rule's conditions as a set; every condition must hold, so fewer match more."""
    atoms: set[tuple[Any, ...]] = set()
    for key, value in when.items():
        if key == "fields":
            atoms.update(
                (
                    "fields",
                    item.get("scope") or "payload",
                    tuple(item["path"]),
                    item["operator"],
                    # An existence check reads no value.
                    item.get("value") if item["operator"] != "exists" else None,
                )
                for item in value or []
            )
        else:
            atoms.add((key, value))
    return frozenset(atoms)


def _targets(rule: dict) -> frozenset:
    return frozenset(
        (target["framework"], target["ref"], target.get("kind"))
        for target in rule["map_to"]
    )


def _consolidation(rules: list[dict]) -> list[dict]:
    atoms = {rule["id"]: _atoms(rule["when"]) for rule in rules}
    targets = {rule["id"]: _targets(rule) for rule in rules}
    confidence = {rule["id"]: rule["confidence"] for rule in rules}
    name = _names(rules)
    suggestions: list[dict] = []

    same_conditions: dict[frozenset, list[str]] = {}
    for rule in rules:
        same_conditions.setdefault(atoms[rule["id"]], []).append(rule["id"])
    for group in same_conditions.values():
        if len(group) < 2:
            continue
        by_targets: dict[frozenset, list[str]] = {}
        for rule_id in group:
            by_targets.setdefault(targets[rule_id], []).append(rule_id)
        for copies in by_targets.values():
            if len(copies) > 1:
                keep = max(copies, key=lambda rule_id: confidence[rule_id])
                suggestions.append(
                    {
                        "kind": "duplicate",
                        "rules": copies,
                        "keep": keep,
                        "message": (
                            "These rules have the same conditions and map the same "
                            f"controls, so {name[keep]} alone records the same evidence."
                        ),
                    }
                )
        if len(by_targets) > 1:
            suggestions.append(
                {
                    "kind": "same_conditions",
                    "rules": group,
                    "message": (
                        "These rules have the same conditions and map different "
                        "controls. One rule can map all of their controls"
                        + (
                            "; they use different confidences, so choose one."
                            if len({confidence[r] for r in group}) > 1
                            else "."
                        )
                    ),
                }
            )

    # A broader rule reads the narrower rule's source, or names no source.
    by_source: dict[Any, list[str]] = {}
    for rule in rules:
        by_source.setdefault(rule["when"].get("source"), []).append(rule["id"])
    for narrow in rules:
        n = narrow["id"]
        source = narrow["when"].get("source")
        for b in by_source.get(source, []) + (
            by_source.get(None, []) if source else []
        ):
            if (
                atoms[b] < atoms[n]
                and targets[n] <= targets[b]
                and confidence[n] <= confidence[b]
            ):
                suggestions.append(
                    {
                        "kind": "covered",
                        "rules": [n, b],
                        "message": (
                            f"Every event {name[n]} matches also matches "
                            f"{name[b]}, which maps every control {name[n]} maps "
                            f"with at least the same confidence, so {name[n]} adds "
                            "no evidence."
                        ),
                    }
                )

    explained = {
        frozenset(pair) for item in suggestions for pair in _pairs(item["rules"])
    }
    same_controls: dict[tuple, list[str]] = {}
    for rule in rules:
        if rule["when"].get("source"):
            key = (targets[rule["id"]], rule["when"]["source"])
            same_controls.setdefault(key, []).append(rule["id"])
    for group in same_controls.values():
        # A group whose rules are already explained as copies or as covered by
        # another has nothing further to suggest.
        if all(frozenset(pair) in explained for pair in _pairs(group)):
            continue
        combine = _combinations(group, atoms, confidence)
        suggestions.append(
            {
                "kind": "same_controls",
                "rules": group,
                "combine": combine,
                "message": (
                    "These rules map the same controls from the same source with "
                    "different conditions."
                    + (
                        " Rules that differ in one condition can share a pattern."
                        if combine
                        else " Their conditions differ in more than one way, so "
                        "review them together before combining any."
                    )
                ),
            }
        )
    order = ("duplicate", "covered", "same_conditions", "same_controls")
    suggestions.sort(key=lambda item: order.index(item["kind"]))
    return suggestions


def _candidates(atoms: frozenset):
    """Each single condition a rule could share with another rule as one pattern."""
    for name in _COMBINABLE:
        exact = [atom for atom in atoms if atom[0] == name]
        pattern = [atom for atom in atoms if atom[0] == f"{name}_regex"]
        if len(exact) + len(pattern) == 1 and (pattern or name in _BOUNDED):
            atom = (exact or pattern)[0]
            yield (name,), ("exact" if exact else "regex", atom[1]), atoms - {atom}
    for atom in atoms:
        if atom[0] == "summary_regex":
            yield ("summary_regex",), ("regex", atom[1]), atoms - {atom}
    fields: dict[tuple, list[tuple]] = {}
    for atom in atoms:
        if atom[0] == "fields":
            fields.setdefault((atom[1], atom[2]), []).append(atom)
    for (scope, path), found in fields.items():
        if len(found) == 1 and found[0][3] == "regex":
            yield ("fields", scope, path), ("regex", found[0][4]), atoms - {found[0]}


def _combinations(group: list[str], atoms: dict, confidence: dict) -> list[dict]:
    clusters: dict[tuple, list[tuple[str, tuple]]] = {}
    for rule_id in group:
        for condition, value, rest in _candidates(atoms[rule_id]):
            key = (condition, rest, confidence[rule_id])
            clusters.setdefault(key, []).append((rule_id, value))
    combined = []
    for (condition, _, _), members in clusters.items():
        values = list(dict.fromkeys(value for _, value in members))
        if len(members) < 2 or len(values) < 2:
            continue
        pattern = combined_pattern(values)
        if pattern is None:
            continue
        combined.append(
            {
                "rules": [rule_id for rule_id, _ in members],
                "condition": _condition_label(condition),
                "replace": _replacement(condition, pattern),
                "values": [{"form": form, "value": text} for form, text in values],
                "pattern": pattern,
            }
        )
    return combined


def combined_pattern(values: list[tuple[str, str]]) -> str | None:
    """One pattern matching what each exact value or pattern matches, and nothing more.

    Exact values compare the whole value, so they are anchored at both ends.
    Returns None when the result would not pass the rule editor's pattern checks.
    """
    exact = [_SYNTAX.sub(r"\\\1", text) for form, text in values if form == "exact"]
    branches = [f"\\A(?:{'|'.join(exact)})\\Z"] if exact else []
    branches += [f"(?:{text})" for form, text in values if form == "regex"]
    pattern = "|".join(branches)
    try:
        validate_pattern(pattern)
    except ValueError:
        return None
    return pattern


def _condition_label(condition: tuple) -> str:
    if condition[0] == "fields":
        scope = "Event" if condition[1] == "event" else "Payload"
        return f"{scope} field {'.'.join(condition[2])}"
    if condition[0] == "summary_regex":
        return "Summary"
    return _COMBINABLE[condition[0]]


def _replacement(condition: tuple, pattern: str) -> dict:
    if condition[0] == "fields":
        item = {"path": list(condition[2]), "operator": "regex", "value": pattern}
        if condition[1] == "event":
            item["scope"] = "event"
        return {"fields": [item]}
    if condition[0] == "summary_regex":
        return {"summary_regex": pattern}
    return {f"{condition[0]}_regex": pattern}


# --- Overlapping matches --------------------------------------------------------------


def _overlaps(db, rules, activity, start, clock, paired, window_days) -> list[dict]:
    """Pairs of rules that matched mostly the same events in the window."""
    active = {rule["id"] for rule in rules if rule["id"] in activity}
    if len(active) < 2:
        return []
    name = _names(rules)
    first, second = aliased(Mapping), aliased(Mapping)
    rows = (
        db.query(
            first.rationale,
            second.rationale,
            func.count(func.distinct(first.event_id)),
        )
        .join(
            second,
            (second.event_id == first.event_id) & (first.rationale < second.rationale),
        )
        .join(Event, Event.id == first.event_id)
        .filter(
            first.method == "rule",
            first.mapped_by == "system",
            second.method == "rule",
            second.mapped_by == "system",
            Event.timestamp >= start,
            Event.timestamp <= clock,
        )
        .group_by(first.rationale, second.rationale)
    )
    shared: dict[tuple[str, str], int] = {}
    for one, other, count in rows:
        a, b = rule_of_rationale(one), rule_of_rationale(other)
        if a is None or b is None or a == b or not {a, b} <= active:
            continue
        pair = (a, b) if a < b else (b, a)
        # The busiest pair of frameworks counts the events both rules matched.
        shared[pair] = max(shared.get(pair, 0), int(count))
    found = []
    for (a, b), both in shared.items():
        if frozenset((a, b)) in paired:
            continue
        smaller = min((a, b), key=lambda rule_id: activity[rule_id]["events"])
        other = b if smaller == a else a
        least = activity[smaller]["events"]
        both = min(both, least)
        if both < OVERLAP_MIN_EVENTS or both < OVERLAP_SHARE * least:
            continue
        found.append(
            {
                "rules": [smaller, other],
                "shared_events": both,
                "events": {rule_id: activity[rule_id]["events"] for rule_id in (a, b)},
                "share_pct": round(both * 100 / least),
                "message": (
                    f"{name[other]} also matched {both:,} of the {least:,} events "
                    f"{name[smaller]} "
                    f"matched in the last {window_days} days. Check whether one rule "
                    "could map both sets of controls."
                ),
            }
        )
    found.sort(
        key=lambda item: (-item["share_pct"], -item["shared_events"], item["rules"])
    )
    return found
