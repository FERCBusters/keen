"""Event listing filters: how a request's filter values become one SQL predicate.

The event list, its facets, its exports and its charts take the same filters.
Each is parsed once into an ``EventFilters`` value, so every surface applies
the same semantics:

- ``source``, ``control``, ``clause`` and ``rule`` take comma-separated lists
  and match events that satisfy any value in the list.
- ``q`` is a free-text search across the event display fields.
- text conditions, written ``field:operator:value``, apply an operator to one
  field: ``contains``, ``not_contains``, ``starts_with``, ``ends_with``,
  ``equals``, ``not_equals``, ``regex`` and ``not_regex``. Matching ignores
  case. A regular expression is checked with the same bounds as a mapping rule
  pattern before it reaches the database, and the database scan has a time
  budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import exists, false, or_, text
from sqlalchemy.exc import DataError, OperationalError
from sqlalchemy.orm import Session, aliased

from app.core.datetime_utils import after_inclusive_date
from app.core.references import try_uuid
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    Event,
    FrameworkClause,
    Mapping,
)
from app.mapping.rules import rule_rationale
from app.security.regex import validate_pattern
from app.services.control_inheritance import (
    effective_framework_ids,
    event_has_control,
    source_ids_for_control,
)
from app.services.control_refs import control_by_ref
from app.services.errors import DomainError

TEXT_FIELDS = ("summary", "system", "actor", "action", "outcome", "external_id")
OPERATORS = (
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "equals",
    "not_equals",
    "regex",
    "not_regex",
)
MAX_CONDITIONS = 10
MAX_VALUE_CHARS = 512
MAX_LIST_VALUES = 50
# A regular expression scan over the events table runs with this budget.
REGEX_SCAN_TIMEOUT_MS = 5000
_QUERY_CANCELLED = "57014"


@dataclass(frozen=True)
class TextCondition:
    field: str
    op: str
    value: str

    @property
    def negated(self) -> bool:
        return self.op.startswith("not_")

    @property
    def uses_regex(self) -> bool:
        return self.op.endswith("regex")


@dataclass(frozen=True)
class EventFilters:
    framework: str
    q: str | None = None
    sources: tuple[str, ...] = ()
    system: str | None = None
    actor: str | None = None
    controls: tuple[str, ...] = ()
    clauses: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    conditions: tuple[TextCondition, ...] = ()
    unmapped: bool = False
    start_date: date | None = None
    end_date: date | None = None
    start_ts: datetime | None = None
    end_ts: datetime | None = None
    # Which filters a facet leaves out so that it can offer alternatives.
    excluded: frozenset[str] = field(default_factory=frozenset)

    @property
    def uses_regex(self) -> bool:
        return any(c.uses_regex for c in self.conditions)

    def without(self, *names: str) -> EventFilters:
        return EventFilters(**{**vars(self), "excluded": self.excluded | set(names)})

    def cache_parts(self) -> dict:
        parts = {k: v for k, v in vars(self).items() if k not in ("excluded",)}
        parts["conditions"] = [vars(c) for c in self.conditions]
        parts["excluded"] = sorted(self.excluded)
        return parts


def split_values(raw: str | None, *, label: str) -> tuple[str, ...]:
    """Read a comma-separated filter list; values keep their order."""
    values: list[str] = []
    for part in str(raw or "").split(","):
        value = part.strip()
        if value and value not in values:
            values.append(value)
    if len(values) > MAX_LIST_VALUES:
        raise DomainError(
            400, f"At most {MAX_LIST_VALUES} {label} values can be chosen"
        )
    for value in values:
        if len(value) > MAX_VALUE_CHARS:
            raise DomainError(
                400, f"A {label} value must be under {MAX_VALUE_CHARS} characters"
            )
    return tuple(values)


def parse_text_condition(raw: str) -> TextCondition:
    """Read ``field:operator:value``; the value may itself contain colons."""
    parts = str(raw or "").split(":", 2)
    if len(parts) != 3:
        raise DomainError(400, "A text filter is written field:operator:value")
    field_name, op, value = parts[0].strip().lower(), parts[1].strip().lower(), parts[2]
    if field_name not in TEXT_FIELDS:
        raise DomainError(400, "Text filters apply to " + ", ".join(TEXT_FIELDS))
    if op not in OPERATORS:
        raise DomainError(400, "Text filter operators are " + ", ".join(OPERATORS))
    if not value.strip():
        raise DomainError(400, "A text filter needs a value")
    if len(value) > MAX_VALUE_CHARS:
        raise DomainError(
            400, f"A text filter value must be under {MAX_VALUE_CHARS} characters"
        )
    if op.endswith("regex"):
        try:
            validate_pattern(value)
        except ValueError as exc:
            raise DomainError(400, f"Invalid regular expression: {exc}") from None
    return TextCondition(field=field_name, op=op, value=value)


def parse_event_filters(
    *,
    framework: str,
    q: str | None = None,
    source: str | None = None,
    system: str | None = None,
    actor: str | None = None,
    control: str | None = None,
    clause: str | None = None,
    rule: str | None = None,
    conditions: list[str] | None = None,
    unmapped: bool = False,
    start_date: date | None = None,
    end_date: date | None = None,
    start_ts: datetime | None = None,
    end_ts: datetime | None = None,
) -> EventFilters:
    raw_conditions = list(conditions or [])
    if len(raw_conditions) > MAX_CONDITIONS:
        raise DomainError(400, f"At most {MAX_CONDITIONS} text filters can be combined")
    return EventFilters(
        framework=framework,
        q=(q or "").strip() or None,
        sources=split_values(source, label="source"),
        system=(system or "").strip() or None,
        actor=(actor or "").strip() or None,
        controls=split_values(control, label="control"),
        clauses=split_values(clause, label="clause"),
        rules=split_values(rule, label="rule"),
        conditions=tuple(parse_text_condition(raw) for raw in raw_conditions),
        unmapped=bool(unmapped),
        start_date=start_date,
        end_date=end_date,
        start_ts=start_ts,
        end_ts=end_ts,
    )


def like_escape(value: str) -> str:
    """Escape LIKE wildcards so that typed text matches literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def text_condition_predicate(condition: TextCondition):
    column = getattr(Event, condition.field)
    escaped = like_escape(condition.value)
    if condition.uses_regex:
        positive = column.op("~*")(condition.value)
    else:
        pattern = {
            "contains": f"%{escaped}%",
            "not_contains": f"%{escaped}%",
            "starts_with": f"{escaped}%",
            "ends_with": f"%{escaped}",
            "equals": escaped,
            "not_equals": escaped,
        }[condition.op]
        positive = column.ilike(pattern, escape="\\")
    if condition.negated:
        # An empty field does not contain, start with or equal the value.
        return or_(column.is_(None), ~positive)
    return positive


def control_ids_for(filters: EventFilters, db: Session) -> list | None:
    """Catalogue ids for the chosen controls, or None when none was chosen.

    A reference or id from another framework names nothing here.
    """
    if not filters.controls:
        return None
    ids = []
    for value in filters.controls:
        cid = try_uuid(value)
        if cid:
            found = (
                db.query(ControlItem.id)
                .filter(
                    ControlItem.id == cid,
                    ControlItem.framework_slug == filters.framework,
                )
                .first()
            )
            if found:
                ids.append(cid)
            continue
        item = control_by_ref(db, filters.framework, value)
        if item:
            ids.append(item.id)
    return ids


def rule_predicate(filters: EventFilters):
    """Events with a mapping that one of the chosen rules made in this framework."""
    rationales = [
        rule_rationale(rule_id, filters.framework) for rule_id in filters.rules
    ]
    mapping = aliased(Mapping)
    return exists().where(
        mapping.event_id == Event.id,
        mapping.method == "rule",
        mapping.mapped_by == "system",
        mapping.rationale.in_(rationales),
    )


def applies(filters: EventFilters, name: str) -> bool:
    return name not in filters.excluded


def scan_budget(db: Session, filters: EventFilters) -> None:
    """Bound a regular expression scan; the budget ends with the transaction."""
    if filters.uses_regex and db.get_bind().dialect.name == "postgresql":
        db.execute(text(f"SET LOCAL statement_timeout = {int(REGEX_SCAN_TIMEOUT_MS)}"))


def run_scan(db: Session, filters: EventFilters, query):
    """Run a filter query, reporting database refusals of a pattern as the caller's."""
    try:
        return query()
    except DataError as exc:
        db.rollback()
        raise DomainError(
            400,
            f"The database does not support this regular expression: {_reason(exc)}",
        ) from None
    except OperationalError as exc:
        if not _cancelled(exc):
            raise
        db.rollback()
        raise DomainError(
            400,
            "The regular expression search took too long; narrow the date range "
            "or simplify the pattern",
        ) from None


def _cancelled(exc: OperationalError) -> bool:
    """True for PostgreSQL's statement-timeout cancellation (SQLSTATE 57014)."""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
    return code == _QUERY_CANCELLED or type(orig).__name__ == "QueryCanceled"


def _reason(exc: DataError) -> str:
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return str(getattr(diag, "message_primary", "") or "invalid pattern")


def norm_ts(ts: datetime) -> datetime:
    """Normalise a timestamp query param into a naive UTC datetime.

    The DB stores timestamps as naive DateTime (assumed UTC). FastAPI may parse
    ISO8601 strings with tzinfo (e.g. trailing 'Z'). Convert those to UTC and
    drop tzinfo so SQLAlchemy comparisons are consistent.
    """

    if ts.tzinfo is not None:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


def event_time_filters(filters: EventFilters):
    """Build SQLAlchemy filters for Event.timestamp.

    Supports either:
      - day granularity (start_date/end_date), OR
      - precise timestamps (start_ts/end_ts).

    Semantics:
      - start_date is inclusive at 00:00:00 UTC
      - end_date is inclusive (implemented as < end_date+1 day at 00:00:00 UTC)
      - start_ts is inclusive
      - end_ts is exclusive
    """

    out = []
    if filters.start_ts is not None:
        out.append(Event.timestamp >= norm_ts(filters.start_ts))
    elif filters.start_date:
        out.append(
            Event.timestamp >= datetime.combine(filters.start_date, datetime.min.time())
        )

    if filters.end_ts is not None:
        out.append(Event.timestamp < norm_ts(filters.end_ts))
    elif filters.end_date:
        out.append(Event.timestamp < after_inclusive_date(filters.end_date))
    return out


def event_text_search_condition(q: str | None):
    """Return the event free-text search predicate.

    The existing UI semantics are substring-style search across event display fields.
    We keep that behaviour via pg_trgm-backed ILIKE predicates, and add a PostgreSQL
    full-text predicate that can use the combined GIN index created by migration 0029.
    This avoids pushing users to a sidecar search service for the common evidence
    lookup path.
    """

    term = str(q or "").strip()
    if not term:
        return None

    # The term is a bound parameter and plainto_tsquery reads it as plain text.
    # Substring matches keep punctuation such as "R&D"; LIKE wildcards in the
    # term match literally.
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{escaped}%"
    fts = text(
        "to_tsvector('simple', "
        "coalesce(events.summary, '') || ' ' || "
        "coalesce(events.action, '') || ' ' || "
        "coalesce(events.system, '') || ' ' || "
        "coalesce(events.actor, '') || ' ' || "
        "coalesce(events.source, '') || ' ' || "
        "coalesce(events.outcome, '') || ' ' || "
        "coalesce(events.external_id, '')"
        ") @@ plainto_tsquery('simple', :keen_event_q)"
    ).bindparams(keen_event_q=term)

    return or_(
        fts,
        *(
            column.ilike(like, escape="\\")
            for column in (
                Event.summary,
                Event.action,
                Event.system,
                Event.actor,
                Event.source,
                Event.outcome,
                Event.external_id,
            )
        ),
    )


def unmapped_event_condition(framework: str, db=None):
    """Return a NOT EXISTS predicate for events without framework mappings.

    Keep this anti-join focused on ``mappings`` by using a small framework-control
    subquery. In production datasets the events table is much larger than the
    framework controls list, and this shape lets Postgres use the existing
    event/control mapping index instead of repeatedly joining ``control_items``
    inside the correlated EXISTS check.
    """

    framework_control_ids = effective_framework_ids(framework, db)
    mapping = aliased(Mapping)
    return ~exists().where(mapping.event_id == Event.id).where(
        mapping.control_item_id.in_(framework_control_ids)
    )


def clause_event_condition(framework: str, clause: str, db=None):
    """Return an EXISTS predicate for events mapped to controls linked to a clause.

    `clause` may be a FrameworkClause UUID or a clause ref such as ``5.1``.
    The condition is framework-scoped so refs from another framework cannot leak in.
    """

    cid = try_uuid(clause)
    # Aliases keep the subquery's rows apart from any mapping or control the
    # enclosing aggregate query joins.
    mapping = aliased(Mapping)
    control = aliased(ControlItem)
    link = aliased(ControlClauseLink)
    clause_row = aliased(FrameworkClause)
    cond = (
        exists()
        .where(mapping.event_id == Event.id)
        .where(
            or_(
                mapping.control_item_id == control.id,
                mapping.control_item_id.in_(source_ids_for_control(control.id, db)),
            )
        )
        .where(link.control_item_id == control.id)
        .where(link.clause_id == clause_row.id)
        .where(control.framework_slug == framework)
        .where(clause_row.framework_slug == framework)
    )
    if cid:
        cond = cond.where(clause_row.id == cid)
    else:
        cond = cond.where(clause_row.ref == str(clause or "").strip())
    return cond


def event_predicates(filters: EventFilters, db: Session) -> list:
    """Predicates for every filter not excluded; the time window is separate."""
    predicates = []
    if filters.sources and applies(filters, "source"):
        predicates.append(Event.source.in_(filters.sources))
    if filters.system:
        predicates.append(
            Event.system.ilike(f"%{like_escape(filters.system)}%", escape="\\")
        )
    if filters.actor:
        predicates.append(
            Event.actor.ilike(f"%{like_escape(filters.actor)}%", escape="\\")
        )
    search = event_text_search_condition(filters.q)
    if search is not None:
        predicates.append(search)
    for condition in filters.conditions:
        predicates.append(text_condition_predicate(condition))
    if applies(filters, "control"):
        ids = control_ids_for(filters, db)
        if ids is not None:
            predicates.append(
                or_(*(event_has_control(cid, Event.id, db) for cid in ids))
                if ids
                else false()
            )
    if filters.clauses and applies(filters, "clause"):
        predicates.append(
            or_(
                *(
                    clause_event_condition(filters.framework, c, db)
                    for c in filters.clauses
                )
            )
        )
    if filters.rules and applies(filters, "rule"):
        predicates.append(rule_predicate(filters))
    if filters.unmapped:
        predicates.append(unmapped_event_condition(filters.framework, db))
    return predicates
