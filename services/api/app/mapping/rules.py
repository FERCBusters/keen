from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import yaml

from app.core.config import settings

log = logging.getLogger(__name__)


@dataclass
class Condition:
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


@dataclass
class RuleTarget:
    framework_slug: str
    ref: str
    roles_any: set[str] | None = None


@dataclass
class Rule:
    id: str
    when: Condition
    targets: list[RuleTarget]
    confidence: float = 0.8


_RE_CACHE: dict[str, re.Pattern] = {}


def _match_regex(pat: str | None, val: str | None) -> bool:
    if not pat:
        return True
    val = val or ""
    key = f"{pat}"
    rx = _RE_CACHE.get(key)
    if not rx:
        rx = re.compile(pat)
        _RE_CACHE[key] = rx
    return bool(rx.search(val))


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


def _clean_slug(v: Any) -> str | None:
    s = str(v or "").strip()
    return s or None


def _clean_ref(v: Any) -> str | None:
    s = str(v or "").strip()
    return s or None


def _clean_roles(v: Any) -> set[str] | None:
    if not isinstance(v, list):
        return None
    out = {str(x).strip().lower() for x in v if str(x).strip()}
    return out or None


def _clean_optional_str(v: Any) -> str | None:
    s = str(v or "").strip()
    return s or None


def _clean_optional_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except Exception:
        return None


_KNOWN_WHEN_KEYS = {
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
_WARNED_UNKNOWN_WHEN: set[tuple[str, tuple[str, ...]]] = set()


def _warn_unknown_when_keys(rule_id: str, when: dict[str, Any]) -> None:
    unknown = tuple(
        sorted(str(k) for k in when.keys() if str(k) not in _KNOWN_WHEN_KEYS)
    )
    if unknown and (rule_id, unknown) not in _WARNED_UNKNOWN_WHEN:
        _WARNED_UNKNOWN_WHEN.add((rule_id, unknown))
        log.warning(
            "Ignoring unsupported rules.yml condition key(s) on rule %s: %s",
            rule_id,
            ", ".join(unknown),
        )


def _as_rule_entries(raw: Any) -> list[tuple[str | None, dict[str, Any]]]:
    """Return (framework_hint, rule_dict) tuples from multiple YAML layouts.

    Supported layouts:
      1) Legacy list of rules
         - [{...}, {...}]
      2) Legacy dict with `rules`
         - {rules: [{...}]}
      3) Framework bucket map
         - {frameworks: {"ISO27001:2022": [{...}], "UK-DVSTF:1.0": [{...}]}}
         - {"ISO27001:2022": [{...}], "UK-DVSTF:1.0": [{...}]}

    Rule-level keys (`framework` / `frameworks`) override the inherited bucket.
    """

    out: list[tuple[str | None, dict[str, Any]]] = []

    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                out.append((None, item))
        return out

    if not isinstance(raw, dict):
        return out

    if isinstance(raw.get("rules"), list):
        inherited = _clean_slug(raw.get("framework"))
        for item in raw.get("rules") or []:
            if isinstance(item, dict):
                out.append((inherited, item))

    fw_map = raw.get("frameworks")
    if isinstance(fw_map, dict):
        for fw, bucket in fw_map.items():
            fw_slug = _clean_slug(fw)
            if isinstance(bucket, dict):
                bucket_rules = bucket.get("rules")
            else:
                bucket_rules = bucket
            if not isinstance(bucket_rules, list):
                continue
            for item in bucket_rules:
                if isinstance(item, dict):
                    out.append((fw_slug, item))

    # Treat top-level mapping as a framework map when no explicit keys are present.
    if "rules" not in raw and "frameworks" not in raw:
        for fw, bucket in raw.items():
            fw_slug = _clean_slug(fw)
            if isinstance(bucket, dict):
                bucket_rules = bucket.get("rules")
            else:
                bucket_rules = bucket
            if not isinstance(bucket_rules, list):
                continue
            for item in bucket_rules:
                if isinstance(item, dict):
                    out.append((fw_slug, item))

    return out


def _normalize_framework_targets(
    rule: dict[str, Any], inherited_fw: str | None
) -> list[str]:
    explicit_many = rule.get("frameworks")
    if isinstance(explicit_many, list):
        vals = [str(x).strip() for x in explicit_many if str(x).strip()]
        if vals:
            return vals

    explicit_one = rule.get("framework")
    if isinstance(explicit_one, str) and explicit_one.strip():
        return [explicit_one.strip()]

    if inherited_fw and inherited_fw.strip():
        return [inherited_fw.strip()]

    return [settings.default_framework_slug]


def _targets_from_list_scalars(
    values: list[Any], frameworks: list[str]
) -> list[RuleTarget]:
    refs = [_clean_ref(v) for v in values]
    refs = [r for r in refs if r]
    out: list[RuleTarget] = []
    for fw in frameworks:
        for ref in refs:
            out.append(RuleTarget(framework_slug=fw, ref=ref))
    return out


def _targets_from_dict_map(
    values: dict[str, Any], frameworks: list[str]
) -> list[RuleTarget]:
    """Parse legacy map_to dict where keys are framework slugs.

    Example:
      map_to:
        ISO27001:2022: [A.8.16]
        UK-DVSTF:1.0: [11.8.1.a]
        "*": [fallback_ref]
    """

    out: list[RuleTarget] = []

    wildcard = values.get("*")
    default_bucket = values.get("default")

    for fw in frameworks:
        bucket = values.get(fw)
        if bucket is None:
            bucket = wildcard if wildcard is not None else default_bucket
        if bucket is None:
            continue
        refs = bucket if isinstance(bucket, list) else [bucket]
        for ref in refs:
            r = _clean_ref(ref)
            if r:
                out.append(RuleTarget(framework_slug=fw, ref=r))

    # Also allow explicit framework keys beyond inherited/default frameworks.
    for fw, bucket in values.items():
        if fw in {"*", "default"}:
            continue
        fw_slug = _clean_slug(fw)
        if not fw_slug:
            continue
        refs = bucket if isinstance(bucket, list) else [bucket]
        for ref in refs:
            r = _clean_ref(ref)
            if r:
                out.append(RuleTarget(framework_slug=fw_slug, ref=r))

    return out


def _targets_from_list_objects(
    values: list[dict[str, Any]], frameworks: list[str]
) -> list[RuleTarget]:
    """Parse object targets.

    Supported forms:
      - {framework: "ISO27001:2022", ref: "A.8.16"}
      - {framework: "UK-DVSTF:1.0", refs: ["11.8.1.a", "11.8.2.b"]}
      - {ref: "A.8.16"}  # uses inherited/default framework(s)
      - {ref: "11.8.1.a", framework: "UK-DVSTF:1.0", roles_any: ["admin","ops"]}
    """

    out: list[RuleTarget] = []

    for item in values:
        if not isinstance(item, dict):
            continue

        fw_list: list[str] = []
        one_fw = _clean_slug(item.get("framework") or item.get("framework_slug"))
        many_fw = item.get("frameworks")
        if isinstance(many_fw, list):
            fw_list = [str(x).strip() for x in many_fw if str(x).strip()]
        elif one_fw:
            fw_list = [one_fw]
        else:
            fw_list = list(frameworks)

        refs_raw = item.get("refs")
        if refs_raw is None:
            refs_raw = [item.get("ref")]
        if not isinstance(refs_raw, list):
            refs_raw = [refs_raw]

        roles_any = _clean_roles(item.get("roles_any"))

        refs = [_clean_ref(r) for r in refs_raw]
        refs = [r for r in refs if r]
        for fw in fw_list:
            for ref in refs:
                out.append(RuleTarget(framework_slug=fw, ref=ref, roles_any=roles_any))

    return out


def _normalize_rule_targets(raw_map: Any, frameworks: list[str]) -> list[RuleTarget]:
    """Normalize map_to/controls into explicit per-framework targets."""

    if isinstance(raw_map, list):
        if raw_map and all(isinstance(x, dict) for x in raw_map):
            return _targets_from_list_objects(raw_map, frameworks)
        return _targets_from_list_scalars(raw_map, frameworks)

    if isinstance(raw_map, dict):
        # Explicit target-object container form:
        # map_to: {targets: [{framework, ref}, ...]}
        targets = (
            raw_map.get("targets") if isinstance(raw_map.get("targets"), list) else None
        )
        if targets and all(isinstance(x, dict) for x in targets):
            return _targets_from_list_objects(targets, frameworks)

        return _targets_from_dict_map(raw_map, frameworks)

    # Scalar ref.
    one = _clean_ref(raw_map)
    if not one:
        return []
    return [RuleTarget(framework_slug=fw, ref=one) for fw in frameworks]


def load_rules(path: str) -> list[Rule]:
    """Load rules from YAML.

    Backward compatible with the original single-framework schema while allowing
    multi-framework rules and object-style per-target mappings.
    """

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return []

    rules: list[Rule] = []
    entries = _as_rule_entries(raw)

    for idx, (inherited_fw, it) in enumerate(entries):
        rid = str(it.get("id") or f"r{idx}")
        w = it.get("when") or {}
        if not isinstance(w, dict):
            log.warning("Ignoring non-object rules.yml condition on rule %s", rid)
            w = {}
        _warn_unknown_when_keys(rid, w)

        severity = _clean_optional_int(w.get("severity"))
        if w.get("severity") is not None and severity is None:
            log.warning(
                "Ignoring invalid rules.yml severity condition on rule %s: %r",
                rid,
                w.get("severity"),
            )

        cond = Condition(
            source=_clean_optional_str(w.get("source")),
            source_regex=_clean_optional_str(w.get("source_regex")),
            system=_clean_optional_str(w.get("system")),
            system_regex=_clean_optional_str(w.get("system_regex")),
            actor=_clean_optional_str(w.get("actor")),
            actor_regex=_clean_optional_str(w.get("actor_regex")),
            action=_clean_optional_str(w.get("action")),
            action_regex=_clean_optional_str(w.get("action_regex")),
            outcome=_clean_optional_str(w.get("outcome")),
            outcome_regex=_clean_optional_str(w.get("outcome_regex")),
            severity=severity,
            label=_clean_optional_str(w.get("label")),
            label_regex=_clean_optional_str(w.get("label_regex")),
            summary_regex=_clean_optional_str(w.get("summary_regex")),
        )

        conf_raw = it.get("confidence", 0.8)
        try:
            conf = float(conf_raw)
        except Exception:
            conf = 0.8

        frameworks = _normalize_framework_targets(it, inherited_fw)
        raw_map = it.get("map_to")
        if raw_map is None:
            raw_map = it.get("controls")

        targets = _normalize_rule_targets(raw_map, frameworks)
        if not targets:
            continue

        # Deduplicate targets while preserving order.
        seen: set[tuple[str, str, tuple[str, ...] | None]] = set()
        uniq: list[RuleTarget] = []
        for t in targets:
            roles_key = tuple(sorted(t.roles_any)) if t.roles_any else None
            k = (t.framework_slug, t.ref, roles_key)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(t)

        rules.append(Rule(id=rid, when=cond, targets=uniq, confidence=conf))

    return rules


def evaluate_by_framework(
    event: dict[str, Any],
    rules: list[Rule],
    *,
    active_roles: set[str] | None = None,
) -> dict[str, list[str]]:
    """Return matched control refs grouped by framework slug."""

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

    roles = {str(r).strip().lower() for r in (active_roles or set()) if str(r).strip()}

    hits: dict[str, list[str]] = {}

    for r in rules:
        c = r.when
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
            if t.roles_any is not None and roles and t.roles_any.isdisjoint(roles):
                continue
            # If rule target has role gating and caller provided no roles,
            # treat as not satisfied.
            if t.roles_any is not None and not roles:
                continue
            bucket = hits.setdefault(t.framework_slug, [])
            bucket.append(t.ref)

    # De-duplicate refs while preserving order.
    out: dict[str, list[str]] = {}
    for fw, refs in hits.items():
        seen: set[str] = set()
        uniq: list[str] = []
        for ref in refs:
            if ref in seen:
                continue
            seen.add(ref)
            uniq.append(ref)
        out[fw] = uniq

    return out


def evaluate(
    event: dict[str, Any],
    rules: list[Rule],
    framework: str | None = None,
    *,
    active_roles: set[str] | None = None,
) -> list[str]:
    """Backward-compatible matcher.

    - framework provided: refs for that framework only
    - framework omitted: flattened refs across all frameworks
    """

    by_fw = evaluate_by_framework(event, rules, active_roles=active_roles)
    if framework:
        return list(by_fw.get(framework, []))

    merged: list[str] = []
    seen: set[str] = set()
    for refs in by_fw.values():
        for ref in refs:
            if ref in seen:
                continue
            seen.add(ref)
            merged.append(ref)
    return merged
