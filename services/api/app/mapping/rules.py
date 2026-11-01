from __future__ import annotations

import hashlib
import json
import logging
import pickle
import re
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.mapping.collector import matches_collector
from app.mapping.fields import field_matches, validate_fields

log = logging.getLogger(__name__)

# A rule target names a control or a clause. Framework clauses are also kept in
# the control catalogue, as rows of type "clause", so a clause and a control can
# share a reference.
CONTROL = "control"
CLAUSE = "clause"
TARGET_KINDS = (CONTROL, CLAUSE)


# Parsed rules are shared between the events that use them, so they are frozen.
@dataclass(frozen=True)
class Condition:
    fields: list[dict] | None = None
    connection_id: str | None = None
    collector: str | None = None
    source: str | None = None
    source_regex: str | None = None
    system: str | None = None
    system_regex: str | None = None
    actor: str | None = None
    actor_regex: str | None = None
    action: str | None = None
    action_regex: str | None = None
    outcome: str | None = None
    outcome_regex: str | None = None
    severity: int | None = None
    label: str | None = None
    label_regex: str | None = None
    summary_regex: str | None = None
    bookstack_book_id: int | None = None
    bookstack_book_slug: str | None = None
    bookstack_page_id: int | None = None
    bookstack_page_slug: str | None = None
    bookstack_page_slug_regex: str | None = None
    bookstack_page_title: str | None = None
    bookstack_page_title_regex: str | None = None


@dataclass(frozen=True)
class RuleTarget:
    framework_slug: str
    ref: str
    # "control" or "clause". None leaves the catalogue to say which it names.
    kind: str | None = None


@dataclass(frozen=True)
class Rule:
    id: str
    when: Condition
    targets: list[RuleTarget]
    confidence: float = 0.8


def rule_rationale(rule_id: str, framework_slug: str) -> str:
    """The rationale a rule mapping records; it names the rule and the framework."""
    return f"auto by rule {rule_id} ({framework_slug})"


def _match_regex(pat: str | None, val: str | None) -> bool:
    if not pat:
        return True
    from app.security.regex import compile_pattern

    return bool(compile_pattern(pat).search(val or ""))


def _match_any_regex(pat: str | None, values: list[str]) -> bool:
    if not pat:
        return True
    return any(_match_regex(pat, v) for v in values)


def _match_any_exact(expected: str | None, values: list[str]) -> bool:
    if expected is None:
        return True
    expected = str(expected)
    return any(str(v) == expected for v in values)


def _add_label_value(out: list[str], value: Any) -> None:
    if value is None:
        return
    if isinstance(value, (str, int, float, bool)):
        txt = str(value).strip()
        if txt:
            out.append(txt)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _add_label_value(out, item)
        return
    if isinstance(value, dict):
        for k, v in value.items():
            # Loki-style labels are stored as a dict. Keep both the value and
            # a key=value representation so rules can target either form.
            _add_label_value(out, v)
            if isinstance(v, (str, int, float, bool)):
                txt = f"{k}={v}".strip()
                if txt:
                    out.append(txt)


def _extract_label_values_from_obj(obj: Any, out: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in {"label", "labels"}:
                _add_label_value(out, v)
            if isinstance(v, (dict, list, tuple, set)):
                _extract_label_values_from_obj(v, out)
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            _extract_label_values_from_obj(item, out)


def _event_label_values(event: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("label", "labels"):
        _add_label_value(values, event.get(key))
    _extract_label_values_from_obj(event.get("normalized_payload"), values)
    _extract_label_values_from_obj(event.get("raw_pointer"), values)

    # De-duplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


# A rule entry and a target object may carry only these keys; anything else is
# taken for a typo, so the rule is skipped rather than read differently.
_RULE_KEYS = {
    "id",
    "description",
    "when",
    "map_to",
    "confidence",
    "enabled",
    "framework",
    "frameworks",
}
_TARGET_KEYS = {"framework", "frameworks", "ref", "refs", "kind"}
_KNOWN_WHEN_KEYS = {
    "fields",
    "bookstack_book_id",
    "bookstack_book_slug",
    "bookstack_page_id",
    "bookstack_page_slug",
    "bookstack_page_slug_regex",
    "bookstack_page_title",
    "bookstack_page_title_regex",
    "connection_id",
    "collector",
    "source",
    "source_regex",
    "system",
    "system_regex",
    "actor",
    "actor_regex",
    "action",
    "action_regex",
    "outcome",
    "outcome_regex",
    "severity",
    "label",
    "label_regex",
    "summary_regex",
}
# Whole-number conditions and their largest values, as the rule editor accepts.
_INTEGER_CONDITIONS = {
    "severity": 1000,
    "bookstack_book_id": 2_147_483_647,
    "bookstack_page_id": 2_147_483_647,
}
_TEXT_LIMIT = 512
_REF_LIMIT = 64
_FRAMEWORK = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_WARNED: set[tuple[str, str]] = set()
# The rules this process last read: the default framework they were read with,
# a digest of their document, and the parsed rules. load_rules reuses them until
# either changes.
_LAST_LOADED: dict[str, tuple[str, bytes, tuple[Rule, ...]]] = {}


class _Invalid(ValueError):
    """A rule entry Keen cannot read as it is written."""


def _skip(rule_id: str, problem: str) -> None:
    # Each new rules document, editor check and preview rereads the rules, so a
    # problem is logged once.
    if (rule_id, problem) not in _WARNED:
        if len(_WARNED) >= 1024:
            _WARNED.clear()
        _WARNED.add((rule_id, problem))
        log.warning("Skipping mapping rule %s: %s", rule_id, problem)


def _as_rule_entries(raw: Any) -> list[tuple[str | None, dict[str, Any]]]:
    """Return (framework, rule) pairs from a rules document.

    A document is ``{rules: [...]}``, optionally naming the framework its rules
    map to when they name none themselves.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("rules"), list):
        return []
    framework = raw.get("framework")
    inherited = (framework.strip() or None) if isinstance(framework, str) else None
    return [(inherited, item) for item in raw["rules"] if isinstance(item, dict)]


def _collector(value: str) -> None:
    try:
        identity = json.loads(value)
    except ValueError:
        identity = None
    if not (
        isinstance(identity, list)
        and len(identity) == 3
        and all(isinstance(part, str) and part for part in identity)
    ):
        raise _Invalid("collector must be a collection identity")


def _condition(when: Any) -> Condition:
    from app.security.regex import validate_pattern

    if not isinstance(when, dict):
        raise _Invalid("when must be an object of conditions")
    unknown = sorted(str(key) for key in when if key not in _KNOWN_WHEN_KEYS)
    if unknown:
        raise _Invalid("unsupported condition " + ", ".join(unknown))
    values: dict[str, Any] = {}
    for key, value in when.items():
        if key == "fields":
            try:
                values[key] = validate_fields(value)
            except ValueError as exc:
                raise _Invalid(f"fields: {exc}") from None
        elif key in _INTEGER_CONDITIONS:
            largest = _INTEGER_CONDITIONS[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= largest
            ):
                raise _Invalid(f"{key} must be a whole number from 0 to {largest}")
            values[key] = value
        else:
            if not isinstance(value, str) or len(value) > _TEXT_LIMIT:
                raise _Invalid(f"{key} must be text of up to {_TEXT_LIMIT} characters")
            if key.endswith("_regex"):
                try:
                    validate_pattern(value)
                except ValueError as exc:
                    raise _Invalid(f"{key}: {exc}") from None
            if key == "collector":
                _collector(value)
            values[key] = value.strip() or None
    return Condition(**values)


def _framework_names(value: Any, label: str) -> list[str]:
    names = value if isinstance(value, list) else [value]
    if not names or not all(
        isinstance(name, str) and _FRAMEWORK.fullmatch(name.strip()) for name in names
    ):
        raise _Invalid(f"{label} must name frameworks by their identifiers")
    return [name.strip() for name in names]


def _frameworks(item: dict[str, Any], inherited: list[str]) -> list[str]:
    if "framework" in item and "frameworks" in item:
        raise _Invalid("give framework or frameworks, as alternatives")
    if "frameworks" in item:
        if not isinstance(item["frameworks"], list):
            raise _Invalid("frameworks must be a list")
        return _framework_names(item["frameworks"], "frameworks")
    if "framework" in item:
        return _framework_names(item["framework"], "framework")
    return inherited


def _refs(value: Any) -> list[str]:
    refs = value if isinstance(value, list) else [value]
    if not refs or not all(
        isinstance(ref, str) and ref.strip() and len(ref.strip()) <= _REF_LIMIT
        for ref in refs
    ):
        raise _Invalid(
            f"map_to references must be text of up to {_REF_LIMIT} characters"
        )
    return [ref.strip() for ref in refs]


def _target_objects(values: list[dict[str, Any]], frameworks: list[str]):
    """Read target objects such as ``{framework, ref}`` or ``{refs, kind}``.

    ``kind`` says whether the reference names a control or a clause, so
    evidence attaches only to the item the rule names.
    """
    for item in values:
        unknown = sorted(str(key) for key in item if key not in _TARGET_KEYS)
        if unknown:
            raise _Invalid("unsupported target key " + ", ".join(unknown))
        if ("ref" in item) == ("refs" in item):
            raise _Invalid("each target needs ref or refs")
        refs = _refs(item["ref"] if "ref" in item else item["refs"])
        kind = item.get("kind")
        if kind is not None:
            kind = kind.strip().lower() if isinstance(kind, str) else kind
            if kind not in TARGET_KINDS:
                raise _Invalid("a target's kind must be control or clause")
        for framework in _frameworks(item, frameworks):
            for ref in refs:
                yield RuleTarget(framework_slug=framework, ref=ref, kind=kind)


def _targets(raw_map: Any, frameworks: list[str]) -> list[RuleTarget]:
    """Turn ``map_to`` into one target per framework and reference.

    ``map_to`` is a reference or a list of references for the rule's
    frameworks, a map of framework identifiers to references, or a list of
    target objects.
    """
    if raw_map is None:
        raise _Invalid("map_to is required")
    if raw_map in ({}, []):
        raise _Invalid("map_to lists no targets")
    if isinstance(raw_map, dict):
        return [
            RuleTarget(framework_slug=framework, ref=ref)
            for key, refs in raw_map.items()
            for framework in _framework_names(key, "map_to")
            for ref in _refs(refs)
        ]
    if (
        isinstance(raw_map, list)
        and raw_map
        and all(isinstance(item, dict) for item in raw_map)
    ):
        return list(_target_objects(raw_map, frameworks))
    return [
        RuleTarget(framework_slug=framework, ref=ref)
        for framework in frameworks
        for ref in _refs(raw_map)
    ]


def _parse_rule(rid: str, inherited: str | None, item: dict[str, Any]) -> Rule | None:
    unknown = sorted(str(key) for key in item if key not in _RULE_KEYS)
    if unknown:
        raise _Invalid("unsupported key " + ", ".join(unknown))
    enabled = item.get("enabled", True)
    if not isinstance(enabled, bool):
        raise _Invalid("enabled must be true or false")
    if not enabled:
        return None
    if "when" not in item:
        raise _Invalid("when is required")
    condition = _condition(item["when"])
    confidence = item.get("confidence", 0.8)
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise _Invalid("confidence must be a number from 0 to 1")
    frameworks = _frameworks(item, [inherited or settings.default_framework_slug])
    targets: list[RuleTarget] = []
    for target in _targets(item.get("map_to"), frameworks):
        if target not in targets:
            targets.append(target)
    return Rule(id=rid, when=condition, targets=targets, confidence=float(confidence))


def load_rules(path: str, *, db: Any = None) -> list[Rule]:
    """Load rules from the managed document, or the shipped YAML.

    Each collected event loads the rules, so a document is parsed once and its
    rules are reused until the document changes. The digest covers each value's
    type as well as its content, so a document whose values compare equal but
    parse differently, such as ``enabled: 1`` and ``enabled: true``, is parsed
    afresh. The pickled document is only hashed; nothing is unpickled.
    """

    from app.core.managed_configuration import load_document

    try:
        raw = load_document("rules", path, db=db)
    except FileNotFoundError:
        return []
    digest = hashlib.sha256(pickle.dumps(raw, pickle.HIGHEST_PROTOCOL)).digest()
    framework = settings.default_framework_slug
    last = _LAST_LOADED.get("rules")
    if last is None or last[:2] != (framework, digest):
        last = (framework, digest, tuple(parse_rules(raw)))
        _LAST_LOADED["rules"] = last
    return list(last[2])


def rule_problem(item: dict[str, Any]) -> str | None:
    """Describe why a rule entry would be skipped, or return None."""
    try:
        _parse_rule(str(item.get("id") or ""), None, item)
    except _Invalid as problem:
        return str(problem)
    return None


def parse_rules(raw: Any) -> list[Rule]:
    """Read every usable rule; skip and log each entry that cannot be read.

    Runtime matching, the rule editor, previews and backfills share this parser.
    """
    rules: list[Rule] = []
    listed = raw.get("rules") if isinstance(raw, dict) else None
    for position, item in enumerate(listed if isinstance(listed, list) else [], 1):
        if not isinstance(item, dict):
            _skip(f"#{position}", "a rule must be an object")
    for index, (inherited, item) in enumerate(_as_rule_entries(raw)):
        rid = str(item.get("id") or f"r{index}")
        try:
            rule = _parse_rule(rid, inherited, item)
        except _Invalid as problem:
            _skip(rid, str(problem))
            continue
        if rule is not None:
            rules.append(rule)
    return rules


def evaluate_by_framework(
    event: dict[str, Any],
    rules: list[Rule],
    *,
    details: bool = False,
) -> dict[str, list[str]] | dict[str, list[dict[str, Any]]]:
    """Return matched refs, or provenance and confidence for persisted mappings."""

    source = event.get("source") or ""
    system = event.get("system") or ""
    actor = event.get("actor") or ""
    action = event.get("action") or ""
    outcome = event.get("outcome") or ""
    severity_raw = event.get("severity")
    try:
        severity = int(severity_raw) if severity_raw is not None else None
    except Exception:
        severity = None

    summary = event.get("summary") or ""
    if not summary:
        payload = event.get("normalized_payload")
        if isinstance(payload, dict):
            try:
                summary = json.dumps(payload, ensure_ascii=False)
            except Exception:
                summary = str(payload)

    labels = _event_label_values(event)

    hits: dict[str, list[Any]] = {}

    for r in rules:
        c = r.when
        if not all(
            field_matches(
                event
                if item.get("scope") == "event"
                else event.get("normalized_payload") or {},
                item,
            )
            for item in (c.fields or [])
        ):
            continue
        if c.connection_id and c.connection_id != event.get("connection_id"):
            continue
        if c.collector and not matches_collector(c.collector, event):
            continue
        if any(
            (
                c.bookstack_book_id is not None,
                c.bookstack_book_slug,
                c.bookstack_page_id is not None,
                c.bookstack_page_slug,
                c.bookstack_page_slug_regex,
                c.bookstack_page_title,
                c.bookstack_page_title_regex,
            )
        ):
            page = (event.get("raw_pointer") or {}).get("bookstack") or {}
            normal = (event.get("normalized_payload") or {}).get("bookstack") or {}
            if not isinstance(page, dict) or not isinstance(normal, dict):
                continue

            def val(name):
                return (
                    page.get(name) if page.get(name) is not None else normal.get(name)
                )

            slug = str(val("page_slug") or "")
            book_slug = str(val("book_slug") or "")
            candidates = (
                (slug, f"{book_slug}-{slug}") if book_slug and slug else (slug,)
            )
            title = str(normal.get("page_title") or "")
            if c.bookstack_book_id is not None and str(val("book_id")) != str(
                c.bookstack_book_id
            ):
                continue
            if (
                c.bookstack_book_slug
                and c.bookstack_book_slug.casefold() != book_slug.casefold()
            ):
                continue
            if c.bookstack_page_id is not None and str(val("page_id")) != str(
                c.bookstack_page_id
            ):
                continue
            if c.bookstack_page_slug and not any(
                c.bookstack_page_slug.casefold() == s.casefold() for s in candidates
            ):
                continue
            if c.bookstack_page_slug_regex and not any(
                _match_regex(c.bookstack_page_slug_regex, s) for s in candidates
            ):
                continue
            if (
                c.bookstack_page_title
                and c.bookstack_page_title.casefold() != title.casefold()
            ):
                continue
            if c.bookstack_page_title_regex and not _match_regex(
                c.bookstack_page_title_regex, title
            ):
                continue
        if c.source and str(c.source) != str(source):
            continue
        if not _match_regex(c.source_regex, source):
            continue
        if c.system and str(c.system) != str(system):
            continue
        if not _match_regex(c.system_regex, system):
            continue
        if c.actor and str(c.actor) != str(actor):
            continue
        if not _match_regex(c.actor_regex, actor):
            continue
        if c.action and str(c.action) != str(action):
            continue
        if not _match_regex(c.action_regex, action):
            continue
        if c.outcome and str(c.outcome) != str(outcome):
            continue
        if not _match_regex(c.outcome_regex, outcome):
            continue
        if c.severity is not None and c.severity != severity:
            continue
        if not _match_any_exact(c.label, labels):
            continue
        if not _match_any_regex(c.label_regex, labels):
            continue
        if not _match_regex(c.summary_regex, summary):
            continue

        for t in r.targets:
            bucket = hits.setdefault(t.framework_slug, [])
            bucket.append(
                {
                    "ref": t.ref,
                    "kind": t.kind,
                    "confidence": r.confidence,
                    "rationale": rule_rationale(r.id, t.framework_slug),
                }
                if details
                else t.ref
            )

    # De-duplicate refs while preserving order; with details, keep the most
    # confident matching rule for each framework, reference and kind.
    out: dict[str, list[Any]] = {}
    for fw, entries in hits.items():
        chosen: dict[Any, Any] = {}
        for entry in entries:
            key = (entry["ref"], entry["kind"]) if details else entry
            if key not in chosen or (
                details and entry["confidence"] > chosen[key]["confidence"]
            ):
                chosen[key] = entry
        out[fw] = list(chosen.values())
    return out
