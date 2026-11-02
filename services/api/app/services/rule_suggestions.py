"""Mapping rules suggested from unmapped events.

A background job reads the latest events of each source, keeps the events no
control of the default framework holds, and groups them by source and
activity. Each group with enough events is matched against the framework's
catalogue:

- the activity in words, such as "Pull Request Event" for
  ``PullRequestEvent``, weighs most;
- the system and source count half as much, and the group's commonest
  summaries a quarter;
- controls that saved rules map for the same activity, a related activity, the
  same system or the same source count as precedent.

The same activity from several sources becomes one rule for those sources. An
administrator reviews each suggestion: creating one saves an ordinary mapping
rule and maps past events in the background, and nothing is mapped before
then. A source and activity whose suggestion was created or dismissed is not
suggested again in that framework.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import delete, exists, func, or_, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, aliased

from app.core.datetime_utils import utc_now_naive
from app.core.source_meta import get_source_meta
from app.core.valkey import get_valkey
from app.db.models import (
    ControlItem,
    Event,
    Mapping,
    RuleSuggestion,
    RuleSuggestionRun,
    User,
)
from app.security.regex import compile_pattern
from app.services.control_inheritance import effective_framework_ids
from app.services.control_suggestions import activity_words, rank_controls
from app.services.errors import DomainError
from app.services.event_filters import statement_cancelled
from app.services.features import state as feature_state
from app.services.frameworks import list_frameworks
from app.services.managed_configurations import RuleInput, get_rules, put_rule
from app.services.rule_insights import combined_pattern
from app.services.rule_sources import distinct_sources, reads_source
from app.services.source_insights import summary_pattern

log = logging.getLogger(__name__)

# The latest events read from each source, within this many days.
SAMPLE_PER_SOURCE = 1000
WINDOW_DAYS = 30
MAX_SOURCES = 100
# Diary entries are personal notes; they are never read for suggestions.
EXCLUDED_SOURCES = frozenset({"diary"})
# A source and activity needs this many unmapped events to be suggested.
MIN_EVENTS = 5
MAX_OPEN = 50
# A suggested rule maps to the best control, and to others scoring at least
# this share of it, up to MAX_TARGETS; weaker matches are not suggested.
MAX_TARGETS = 3
SHARE_OF_BEST = 0.6
MIN_SCORE = 8.0
# Candidate controls kept with each suggestion, chosen or not. A control not
# chosen is offered when it scores at least this share of the best.
MAX_CANDIDATES = 6
ALTERNATIVE_SHARE = 0.4
# Points a control gains from the closest saved rule that maps it.
PRECEDENT_POINTS = 20.0
SAMPLES_KEPT = 5
LIST_LIMIT = 100
RUNS_KEPT = 50
# On PostgreSQL, each query of a run stops after this long.
QUERY_TIMEOUT_MS = 30000
# A request to check now within this long of the last one is not queued again.
RECHECK_SECONDS = 60
LOCK_KEY = 1262830948
_REQUESTED = "keen:rule-suggestions:requested"

# How closely a saved rule relates to a suggestion, and what that is worth. The
# same activity name from another source may be a generic name, such as
# "event", so it counts as much as a related activity.
_RELATIONS = {
    "activity": 1.0,
    "pattern": 0.7,
    "elsewhere": 0.7,
    "family": 0.7,
    "system": 0.7,
    "source": 0.4,
}
# A rule using only these conditions maps every event of the activity.
_ACTIVITY_KEYS = {"source", "source_regex", "action", "action_regex"}
_ORIGINS = {
    "mapping name": "activity",
    "context": "source or system",
    "sample": "summaries",
}
_TRANSITIONS = {("open", "dismissed"), ("dismissed", "open"), ("open", "applied")}


class SuggestionApplyInput(BaseModel):
    version: int
    # The candidate controls to map to; the suggested ones when omitted.
    refs: list[Annotated[str, Field(max_length=64)]] | None = Field(
        default=None, max_length=MAX_CANDIDATES
    )


class SuggestionStatusInput(BaseModel):
    status: Literal["open", "dismissed", "applied"]
    rule_id: str | None = Field(default=None, max_length=96)


@dataclass
class _Group:
    """Unmapped events of one source and activity."""

    source: str
    action: str
    count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    systems: Counter = field(default_factory=Counter)
    summaries: Counter = field(default_factory=Counter)
    examples: dict[str, str] = field(default_factory=dict)
    samples: list[dict] = field(default_factory=list)

    def add(self, row: Any) -> None:
        self.count += 1
        stamp = row.timestamp
        self.first_seen = min(self.first_seen or stamp, stamp)
        self.last_seen = max(self.last_seen or stamp, stamp)
        if row.system:
            self.systems[row.system] += 1
        if row.summary:
            pattern = summary_pattern(row.summary)
            self.summaries[pattern] += 1
            self.examples.setdefault(pattern, row.summary)
        # Rows arrive newest first, so the samples are the latest events.
        if len(self.samples) < SAMPLES_KEPT:
            self.samples.append({"id": str(row.id), "timestamp": stamp.isoformat()})


@dataclass
class _Proposal:
    """One suggested rule: an activity from one source, or from several."""

    groups: list[_Group]
    when: dict

    @property
    def action(self) -> str:
        return self.groups[0].action

    @property
    def sources(self) -> list[str]:
        return sorted(group.source for group in self.groups)

    @property
    def keys(self) -> list[list[str]]:
        return sorted([group.source, group.action] for group in self.groups)

    @property
    def signature(self) -> str:
        return hashlib.sha256(json.dumps(self.keys).encode()).hexdigest()

    @property
    def count(self) -> int:
        return sum(group.count for group in self.groups)

    def merged(self, name: str) -> Counter:
        total: Counter = Counter()
        for group in self.groups:
            total.update(getattr(group, name))
        return total

    @property
    def system(self) -> str:
        """The system most of these events share, or "" when they vary.

        Repository and host names vary within one activity; only a system that
        four in five of the events share describes the activity.
        """
        name, count = (self.merged("systems").most_common(1) or [("", 0)])[0]
        return name if count * 5 >= self.count * 4 else ""

    @property
    def samples(self) -> list[dict]:
        newest = sorted(
            (s for group in self.groups for s in group.samples),
            key=lambda s: s["timestamp"],
            reverse=True,
        )
        return newest[:SAMPLES_KEPT]


def _accepts(pattern: str, value: str) -> bool:
    try:
        return bool(compile_pattern(pattern).search(value))
    except ValueError:
        return False


def _reads(when: dict, source: str) -> bool:
    try:
        return reads_source(when, source)
    except ValueError:
        return False


def _family(action: str) -> str:
    """The activity family, such as ``posture.identity`` for ``posture.identity.mfa``."""
    head, dot, _ = action.rpartition(".")
    return head if dot else ""


def _framework_targets(rule: dict, framework: str) -> list[dict]:
    return [t for t in rule.get("map_to") or [] if t.get("framework") == framework]


def _covered(rules: list[dict], framework: str, source: str, action: str) -> bool:
    """Whether a saved rule, running or paused, maps every event of this activity.

    Such events were collected before the rule was saved or while it was
    paused; applying that rule to past evidence maps them.
    """
    for rule in rules:
        when = rule.get("when") or {}
        if set(when) - _ACTIVITY_KEYS or not _framework_targets(rule, framework):
            continue
        if not _reads(when, source):
            continue
        if when.get("action") and when["action"] != action:
            continue
        if when.get("action_regex") and not _accepts(when["action_regex"], action):
            continue
        return True
    return False


def _relation(when: dict, proposal: _Proposal) -> str | None:
    """How a saved rule's conditions relate to a suggestion's events."""
    action = proposal.action
    same_source = any(_reads(when, source) for source in proposal.sources)
    if action and when.get("action") == action:
        return "activity" if same_source else "elsewhere"
    if action and same_source and not when.get("action"):
        pattern = when.get("action_regex")
        if pattern and _accepts(pattern, action):
            return "pattern"
    family = _family(action)
    if family and _family(str(when.get("action") or "")) == family:
        return "family"
    if not same_source:
        return None
    if proposal.system and when.get("system") == proposal.system:
        return "system"
    return "source"


def _precedent(
    rules: list[dict], framework: str, proposal: _Proposal
) -> dict[str, tuple[str, dict]]:
    """For each control saved rules map, the closest such rule and its relation."""
    votes: dict[str, tuple[str, dict]] = {}
    for rule in rules:
        targets = _framework_targets(rule, framework)
        relation = _relation(rule.get("when") or {}, proposal) if targets else None
        if relation is None:
            continue
        for target in targets:
            held = votes.get(target["ref"])
            if held is None or _RELATIONS[relation] > _RELATIONS[held[0]]:
                votes[target["ref"]] = (relation, rule)
    return votes


def _precedent_reason(relation: str, rule: dict, proposal: _Proposal) -> str:
    name = f"“{rule.get('description') or rule['id']}”"
    when = rule.get("when") or {}
    return {
        "activity": f"Rule {name} maps the same activity to this control",
        "elsewhere": f"Rule {name} maps the same activity from another source to this control",
        "pattern": f"Rule {name} maps this activity's pattern to this control",
        "family": f"Rule {name} maps related activity ({when.get('action')}) to this control",
        "system": f"Rule {name} maps other events from {proposal.system} to this control",
        "source": f"Rule {name} maps other events from this source to this control",
    }[relation]


def _wording_reasons(item: dict) -> list[str]:
    reasons = []
    if item["matched_terms"]:
        reasons.append("Catalogue words: " + ", ".join(item["matched_terms"]))
    # A concept found in the activity and again in the summaries is named once.
    seen: set[tuple[str, str]] = set()
    for concept in item["matched_concepts"]:
        pair = (concept["query_term"], concept["catalogue_term"])
        if pair in seen or len(seen) == 3:
            continue
        seen.add(pair)
        reasons.append(
            f"Related concept: {pair[0]} → {pair[1]} "
            f"({_ORIGINS.get(concept['origin'], concept['origin'])})"
        )
    return reasons


def _catalogue(db: Session, framework: str) -> list[ControlItem]:
    """The in-scope controls a rule can map to; clauses only without controls.

    A reference shared by two controls cannot be a rule target, so neither is
    suggested.
    """
    rows = (
        db.query(ControlItem)
        .filter(ControlItem.framework_slug == framework, ControlItem.in_scope.is_(True))
        .order_by(ControlItem.ref, ControlItem.id)
        .limit(5000)
        .all()
    )
    controls = [row for row in rows if row.type != "clause"] or rows
    counts = Counter(row.ref for row in controls)
    return [row for row in controls if counts[row.ref] == 1]


def _rank(
    controls: list[ControlItem],
    rules: list[dict],
    framework: str,
    proposal: _Proposal,
) -> list[dict] | None:
    """Candidate controls, best first, with the chosen ones marked.

    Returns None when even the best candidate is too weak to suggest.
    """
    labels = [get_source_meta(source).label for source in proposal.sources]
    examples = {}
    for group in proposal.groups:
        examples.update(group.examples)
    common = " ".join(
        examples[p] for p, _ in proposal.merged("summaries").most_common(3)
    )
    # Without an activity name, the commonest summaries describe the events.
    description, sample = (
        (activity_words(proposal.action), common)
        if proposal.action
        else (common or " ".join(labels), "")
    )
    ranked = rank_controls(
        controls,
        description=description,
        context=" ".join([activity_words(proposal.system), *labels]),
        sample_summary=sample,
        limit=40,
    )
    by_ref = {control.ref: control for control in controls}
    rows: dict[str, dict] = {}
    for item in ranked:
        rows[item["ref"]] = {
            "ref": item["ref"],
            "title": item["title"],
            "wording": item["score"],
            "precedent": 0.0,
            "reasons": _wording_reasons(item),
        }
    for ref, (relation, rule) in _precedent(rules, framework, proposal).items():
        control = by_ref.get(ref)
        if control is None:
            continue
        row = rows.setdefault(
            ref,
            {
                "ref": ref,
                "title": control.title,
                "wording": 0.0,
                "precedent": 0.0,
                "reasons": [],
            },
        )
        row["precedent"] = _RELATIONS[relation] * PRECEDENT_POINTS
        row["reasons"].append(_precedent_reason(relation, rule, proposal))
    for row in rows.values():
        control = by_ref[row["ref"]]
        row["kind"] = "clause" if control.type == "clause" else "control"
        row["score"] = round(row["wording"] + row["precedent"], 3)
    ordered = sorted(rows.values(), key=lambda row: (-row["score"], row["ref"]))
    if not ordered or ordered[0]["score"] < MIN_SCORE:
        return None
    best = ordered[0]["score"]
    floor = max(MIN_SCORE, SHARE_OF_BEST * best)
    for position, row in enumerate(ordered):
        row["selected"] = position < MAX_TARGETS and row["score"] >= floor
    offered = max(MIN_SCORE, ALTERNATIVE_SHARE * best)
    return [row for row in ordered if row["selected"] or row["score"] >= offered][
        :MAX_CANDIDATES
    ]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", activity_words(value).lower()).strip("_")


def _unique_id(stem: str, taken: set[str]) -> str:
    candidate, suffix = stem, 2
    while candidate in taken:
        candidate = f"{stem}_{suffix}"
        suffix += 1
    return candidate


def _rule(
    proposal: _Proposal, framework: str, controls: list[dict], taken: set[str]
) -> dict:
    sources = proposal.sources
    reader = _slug(sources[0]) if len(sources) == 1 else "sources"
    stem = "_".join(p for p in ("suggested", reader, _slug(proposal.action)) if p)
    labels = ", ".join(get_source_meta(source).label for source in sources)
    return {
        "id": _unique_id(stem[:88], taken),
        "description": f"{labels}: {proposal.action or 'all events'}"[:200],
        "enabled": True,
        "when": proposal.when,
        "map_to": [
            {"framework": framework, "ref": row["ref"], "kind": row["kind"]}
            for row in controls
            if row["selected"]
        ],
    }


def _sample(
    db: Session, held_by: list, clock: datetime
) -> tuple[list[_Group], int, int]:
    """Group the unmapped events among each source's latest events.

    Events without an activity are grouped by source only where none of the
    source's events names an activity, since a rule for the whole source would
    also read every activity it has.
    """
    cutoff = clock - timedelta(days=WINDOW_DAYS)
    listed = distinct_sources(db, limit=MAX_SOURCES + len(EXCLUDED_SOURCES))
    sources = [s for s in listed if s not in EXCLUDED_SOURCES][:MAX_SOURCES]
    mapping = aliased(Mapping)
    groups: dict[tuple[str, str], _Group] = {}
    examined = unmapped = 0
    for source in sources:
        recent = (
            select(Event.id, Event.system, Event.action, Event.summary, Event.timestamp)
            .where(Event.source == source, Event.timestamp >= cutoff)
            .order_by(Event.timestamp.desc(), Event.id.desc())
            .limit(SAMPLE_PER_SOURCE)
            .subquery()
        )
        held = exists().where(
            mapping.event_id == recent.c.id, mapping.control_item_id.in_(held_by)
        )
        rows = db.execute(
            select(
                recent.c.id,
                recent.c.system,
                recent.c.action,
                func.substr(recent.c.summary, 1, 300).label("summary"),
                recent.c.timestamp,
                held.label("held"),
            ).order_by(recent.c.timestamp.desc(), recent.c.id.desc())
        ).all()
        examined += len(rows)
        named = any((row.action or "").strip() for row in rows)
        for row in rows:
            if row.held:
                continue
            unmapped += 1
            action = (row.action or "").strip()
            if action or not named:
                groups.setdefault((source, action), _Group(source, action)).add(row)
    return list(groups.values()), examined, unmapped


def _proposals(groups: list[_Group]) -> list[_Proposal]:
    by_action: dict[str, list[_Group]] = defaultdict(list)
    for group in groups:
        by_action[group.action].append(group)
    proposals = []
    for action, members in by_action.items():
        members.sort(key=lambda group: group.source)
        if action and len(members) > 1:
            pattern = combined_pattern([("exact", group.source) for group in members])
            if pattern:
                when = {"source_regex": pattern, "action": action}
                proposals.append(_Proposal(members, when))
                continue
        for group in members:
            when = {"source": group.source, **({"action": action} if action else {})}
            proposals.append(_Proposal([group], when))
    return proposals


def _decided(db: Session, framework: str) -> set[tuple[str, str]]:
    rows = db.scalars(
        select(RuleSuggestion.keys).where(
            RuleSuggestion.framework_slug == framework,
            RuleSuggestion.status != "open",
        )
    )
    return {(str(source), str(action)) for keys in rows for source, action in keys}


def _refresh(db: Session, framework: str, clock: datetime) -> dict:
    rules = get_rules(db=db)["items"]
    controls = _catalogue(db, framework)
    groups, examined, unmapped = _sample(
        db, effective_framework_ids(framework, db), clock
    )
    decided = _decided(db, framework)
    groups = [
        group
        for group in groups
        if group.count >= MIN_EVENTS
        and (group.source, group.action) not in decided
        and not _covered(rules, framework, group.source, group.action)
    ]
    taken = {rule["id"] for rule in rules}
    chosen: list[tuple[_Proposal, dict, list[dict]]] = []
    for proposal in sorted(_proposals(groups), key=lambda p: (-p.count, p.signature)):
        if len(chosen) >= MAX_OPEN:
            break
        ranked = _rank(controls, rules, framework, proposal) if controls else None
        if ranked is None:
            continue
        rule = _rule(proposal, framework, ranked, taken)
        taken.add(rule["id"])
        chosen.append((proposal, rule, ranked))
    existing = {
        row.signature: row
        for row in db.scalars(
            select(RuleSuggestion).where(RuleSuggestion.framework_slug == framework)
        )
    }
    kept = []
    for proposal, rule, ranked in chosen:
        row = existing.get(proposal.signature)
        # Decided while this run was working: the decision stands.
        if row is not None and row.status != "open":
            continue
        if row is None:
            row = RuleSuggestion(
                framework_slug=framework,
                signature=proposal.signature,
                status="open",
                created_at=clock,
            )
            db.add(row)
        row.keys = proposal.keys
        row.rule = rule
        row.controls = ranked
        row.samples = proposal.samples
        row.event_count = proposal.count
        row.first_seen = min(g.first_seen for g in proposal.groups if g.first_seen)
        row.last_seen = max(g.last_seen for g in proposal.groups if g.last_seen)
        row.updated_at = clock
        kept.append(proposal.signature)
    # Open suggestions no longer found, or for another framework, are withdrawn.
    # One restored while this run was working is left for the next run.
    db.execute(
        delete(RuleSuggestion).where(
            RuleSuggestion.status == "open",
            RuleSuggestion.updated_at <= clock,
            or_(
                RuleSuggestion.framework_slug != framework,
                RuleSuggestion.signature.not_in(kept),
            ),
        )
    )
    _record(db, framework, clock, "completed", examined, unmapped, len(kept))
    return {
        "status": "completed",
        "examined": examined,
        "unmapped": unmapped,
        "suggested": len(kept),
    }


def _record(
    db: Session,
    framework: str,
    started: datetime,
    status: str,
    examined: int = 0,
    unmapped: int = 0,
    suggested: int = 0,
    error: str | None = None,
) -> None:
    db.add(
        RuleSuggestionRun(
            framework_slug=framework,
            started_at=started,
            finished_at=utc_now_naive(),
            status=status,
            examined=examined,
            unmapped=unmapped,
            suggested=suggested,
            error=error,
        )
    )
    db.flush()
    older = (
        select(RuleSuggestionRun.id)
        .order_by(RuleSuggestionRun.finished_at.desc(), RuleSuggestionRun.id)
        .offset(RUNS_KEPT)
    )
    db.execute(delete(RuleSuggestionRun).where(RuleSuggestionRun.id.in_(older)))


def refresh_suggestions(db: Session, *, now: datetime | None = None) -> dict:
    """Work out suggestions afresh for the default framework.

    One run at a time: on PostgreSQL a run that finds another in progress
    returns at once. A failed run is recorded and changes no suggestion.
    """
    clock = now or utc_now_naive()
    if not feature_state(db).enabled("evidence"):
        return {"status": "disabled"}
    framework = list_frameworks(db=db)["default"]
    if not framework:
        return {"status": "no_framework"}
    if db.get_bind().dialect.name == "postgresql":
        if not db.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_KEY}
        ):
            return {"status": "busy"}
        db.execute(text(f"SET LOCAL statement_timeout = {int(QUERY_TIMEOUT_MS)}"))
    try:
        result = _refresh(db, framework, clock)
        db.commit()
    except Exception as exc:
        db.rollback()
        slow = isinstance(exc, OperationalError) and statement_cancelled(exc)
        log.error("Rule suggestions failed (%s)", type(exc).__name__)
        _record(
            db,
            framework,
            clock,
            "failed",
            error=(
                "A query took too long; the next run tries again"
                if slow
                else "Suggestions could not be worked out; check the worker log"
            ),
        )
        db.commit()
        return {"status": "failed"}
    return result


def _held(db: Session, framework: str, event_ids: list[uuid.UUID]) -> set[str]:
    """The events among ``event_ids`` that a control of the framework holds."""
    if not event_ids:
        return set()
    held_by = effective_framework_ids(framework, db)
    rows = db.scalars(
        select(Mapping.event_id)
        .where(Mapping.event_id.in_(event_ids), Mapping.control_item_id.in_(held_by))
        .distinct()
    )
    return {str(row) for row in rows}


def _sample_ids(rows: list[RuleSuggestion]) -> list[uuid.UUID]:
    ids = []
    for row in rows:
        for sample in row.samples or []:
            try:
                ids.append(uuid.UUID(str(sample.get("id"))))
            except (ValueError, AttributeError):
                continue
    return ids


def _current(db: Session, framework: str) -> list[RuleSuggestion]:
    """Open suggestions whose sample events are not all mapped since."""
    rows = list(
        db.scalars(
            select(RuleSuggestion)
            .where(
                RuleSuggestion.framework_slug == framework,
                RuleSuggestion.status == "open",
            )
            .order_by(
                RuleSuggestion.event_count.desc(),
                RuleSuggestion.created_at,
                RuleSuggestion.signature,
            )
        )
    )
    held = _held(db, framework, _sample_ids(rows))
    return [
        row
        for row in rows
        if not (ids := _sample_ids([row])) or not all(str(i) in held for i in ids)
    ]


def _default_framework(db: Session) -> tuple[str, str]:
    frameworks = list_frameworks(db=db)
    slug = frameworks["default"]
    name = next(
        (item.get("name") for item in frameworks["items"] if item["slug"] == slug),
        None,
    )
    return slug, name or slug


def suggestion_summary(*, db: Session) -> dict:
    """How many suggestions await review, for the bell and the home page."""
    if not feature_state(db).enabled("evidence"):
        return {"open_count": 0}
    slug, name = _default_framework(db)
    return {
        "open_count": len(_current(db, slug)) if slug else 0,
        "framework": slug,
        "framework_name": name,
    }


def _item(row: RuleSuggestion, events: dict, users: dict) -> dict:
    keys = row.keys or []
    sources = sorted({str(source) for source, _ in keys})
    return {
        "id": str(row.id),
        "status": row.status,
        "rule": row.rule,
        "sources": [
            {"source": source, "label": get_source_meta(source).label}
            for source in sources
        ],
        "action": str(keys[0][1]) if keys else "",
        "event_count": row.event_count,
        "first_seen": row.first_seen.isoformat(),
        "last_seen": row.last_seen.isoformat(),
        "controls": row.controls,
        "samples": [
            events[sample["id"]]
            for sample in row.samples or []
            if sample.get("id") in events
        ],
        "rule_id": row.rule_id,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decided_by": users.get(row.decided_by),
        "updated_at": row.updated_at.isoformat(),
    }


def _items(db: Session, rows: list[RuleSuggestion]) -> list[dict]:
    ids = _sample_ids(rows)
    events = (
        {
            str(event.id): {
                "id": str(event.id),
                "timestamp": event.timestamp.isoformat(),
                "summary": (event.summary or "")[:300],
            }
            for event in db.execute(
                select(Event.id, Event.timestamp, Event.summary).where(
                    Event.id.in_(ids)
                )
            )
        }
        if ids
        else {}
    )
    deciders = {row.decided_by for row in rows if row.decided_by}
    users: dict[uuid.UUID, str] = (
        {
            user_id: name
            for user_id, name in db.execute(
                select(User.id, User.username).where(User.id.in_(deciders))
            )
        }
        if deciders
        else {}
    )
    return [_item(row, events, users) for row in rows]


def list_suggestions(*, status: str = "open", db: Session) -> dict:
    if status not in ("open", "dismissed", "applied"):
        raise DomainError(400, "Status must be open, dismissed or applied")
    enabled = feature_state(db).enabled("evidence")
    slug, name = _default_framework(db)
    current = _current(db, slug)
    if status == "open":
        rows = current[:LIST_LIMIT]
    else:
        rows = list(
            db.scalars(
                select(RuleSuggestion)
                .where(
                    RuleSuggestion.framework_slug == slug,
                    RuleSuggestion.status == status,
                )
                .order_by(RuleSuggestion.decided_at.desc(), RuleSuggestion.id)
                .limit(LIST_LIMIT)
            )
        )
    run = db.scalars(
        select(RuleSuggestionRun)
        .order_by(RuleSuggestionRun.finished_at.desc(), RuleSuggestionRun.id)
        .limit(1)
    ).first()
    return {
        "enabled": enabled,
        "framework": slug,
        "framework_name": name,
        "open_count": len(current),
        "items": _items(db, rows),
        "last_run": (
            {
                "finished_at": run.finished_at.isoformat(),
                "status": run.status,
                "examined": run.examined,
                "unmapped": run.unmapped,
                "suggested": run.suggested,
                "error": run.error,
            }
            if run
            else None
        ),
        "sample_size": SAMPLE_PER_SOURCE,
        "window_days": WINDOW_DAYS,
        "min_events": MIN_EVENTS,
    }


def _locked(db: Session, suggestion_id: str) -> RuleSuggestion:
    try:
        key = uuid.UUID(str(suggestion_id))
    except ValueError:
        raise DomainError(404, "Suggestion not found") from None
    row = db.scalars(
        select(RuleSuggestion).where(RuleSuggestion.id == key).with_for_update()
    ).first()
    if row is None:
        raise DomainError(404, "Suggestion not found")
    return row


def apply_suggestion(
    *, suggestion_id: str, payload: SuggestionApplyInput, user: Any, db: Session
) -> dict:
    """Save the suggested rule and map past events in the background.

    ``refs`` chooses among the suggestion's candidate controls. The rule and
    the suggestion's new status are committed together.
    """
    row = _locked(db, suggestion_id)
    if row.status != "open":
        raise DomainError(409, "This suggestion has already been decided")
    rules = get_rules(db=db)["items"]
    rule = deepcopy(row.rule)
    if payload.refs is not None:
        candidates = {c["ref"]: c for c in row.controls or []}
        if not payload.refs or set(payload.refs) - set(candidates):
            raise DomainError(400, "Choose at least one of the suggested controls")
        rule["map_to"] = [
            {"framework": row.framework_slug, "ref": ref, "kind": control["kind"]}
            for ref, control in candidates.items()
            if ref in payload.refs
        ]
    rule["id"] = _unique_id(
        str(rule.get("id") or "suggested_rule"), {r["id"] for r in rules}
    )
    row.status = "applied"
    row.rule_id = rule["id"]
    row.decided_at = utc_now_naive()
    row.decided_by = user.id
    try:
        saved = put_rule(
            rule_id=rule["id"],
            payload=RuleInput(
                version=payload.version,
                rule=rule,
                create_only=True,
                apply_existing=True,
            ),
            user=user,
            db=db,
        )
    except Exception:
        db.rollback()
        raise
    return {
        "version": saved["version"],
        "rule": saved["item"],
        "backfill": saved["backfill"],
        "suggestion": _items(db, [row])[0],
    }


def set_suggestion_status(
    *, suggestion_id: str, payload: SuggestionStatusInput, user: Any, db: Session
) -> dict:
    """Dismiss a suggestion, restore a dismissed one, or record a rule saved for it."""
    row = _locked(db, suggestion_id)
    if (row.status, payload.status) not in _TRANSITIONS:
        raise DomainError(
            409, f"A suggestion that is {row.status} cannot become {payload.status}"
        )
    if payload.status == "applied":
        if payload.rule_id not in {rule["id"] for rule in get_rules(db=db)["items"]}:
            raise DomainError(
                409, "Save the rule before marking the suggestion as created"
            )
        row.rule_id = payload.rule_id
    row.status = payload.status
    if payload.status == "open":
        row.rule_id = None
        row.decided_at = None
        row.decided_by = None
    else:
        row.decided_at = utc_now_naive()
        row.decided_by = user.id
    db.commit()
    return _items(db, [row])[0]


def request_refresh(*, db: Session) -> dict:
    """Queue a check now, unless one ran or was asked for in the last minute."""
    # The worker's tasks import this module, so the task is imported here.
    from app.worker.tasks import rule_suggestions_task

    if not feature_state(db).enabled("evidence"):
        raise DomainError(404, "This feature is disabled by an administrator.")
    last = db.scalar(select(func.max(RuleSuggestionRun.finished_at)))
    if last and last > utc_now_naive() - timedelta(seconds=RECHECK_SECONDS):
        return {"status": "recent", "finished_at": last.isoformat()}
    try:
        if not get_valkey().set(_REQUESTED, "1", nx=True, ex=RECHECK_SECONDS):
            return {"status": "queued"}
    except Exception:
        # Without the cache, a repeated request may queue a second run; runs
        # still take turns.
        pass
    try:
        rule_suggestions_task.apply_async(expires=300)
    except Exception:
        try:
            get_valkey().delete(_REQUESTED)
        except Exception:
            pass
        raise DomainError(
            503, "The background worker could not be reached; try again shortly"
        ) from None
    return {"status": "queued"}
