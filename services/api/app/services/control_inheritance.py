"""One-hop evidence inheritance between framework controls.

A control inherits the evidence of each control with an explicit crosswalk
link to it, and of each control of another framework that shares a NIST
reference with it (Open Security Architecture memberships). Evidence counts
only when both controls' frameworks are enabled in the organisation's
selection. Inherited evidence is never passed on: inheritance takes one hop.

``_resolve`` is the one statement of that rule. Every reader of inherited
evidence, counters, lists and the catalogue's link list alike, takes its
links from it, through ``crosswalk_links`` for each link's details or
``crosswalk_sources`` for the controls each control inherits from. It reads
the links and memberships it needs with short indexed queries and pairs
shared references here, so its cost follows the controls concerned, however
many catalogue controls hold a popular reference. Within a transaction,
repeated resolutions are answered from the session until it writes through
the session, flushes or ends.
"""

import uuid
from datetime import datetime
from types import MappingProxyType
from typing import NamedTuple

from sqlalchemy import column, event, exists, false, func, select, values
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.db.models import (
    ControlEvidenceStats,
    ControlItem,
    CrossFrameworkControlLink,
    Event,
    Mapping,
    OsaControlMapping,
)
from app.services.framework_catalogue import _framework_selection

SHARED_NIST_RATIONALE = (
    "Related evidence via shared NIST controls in Open Security Architecture "
    "(CC BY-SA 4.0). Keen-derived crosswalk; review relevance and coverage gaps, "
    "as related controls can differ in scope."
)


class CrosswalkLink(NamedTuple):
    """One control's evidence counting for another, one hop.

    ``id`` is the explicit link's id, or ``source:target`` for a pair that
    shares a NIST reference, which is ``derived``.
    """

    id: str
    source_control_id: uuid.UUID
    target_control_id: uuid.UUID
    rationale: str
    created_at: datetime | None
    derived: bool


_MEMO = "keen_crosswalk"


def _remembered(db, key, compute):
    if db.new or db.dirty or db.deleted:
        # A query flushes pending changes first, so it resolves afresh.
        return compute()
    memo = db.info.setdefault(_MEMO, {})
    if key not in memo:
        memo[key] = compute()
    return memo[key]


def _forget(session, *_):
    session.info.pop(_MEMO, None)


# A resolution holds only while the session reads. With pending changes it is
# resolved afresh, and a flush, a write statement run through the session or
# the end of a transaction (commit, rollback, close or savepoint) discards it.
# Changes other transactions commit meanwhile are seen from the next
# transaction, so a long transaction, such as an export, keeps one crosswalk;
# readers leave out a control removed since it was read. A write sent through
# db.connection() directly is seen from the next transaction too.
event.listen(Session, "after_transaction_end", _forget)
event.listen(Session, "before_flush", _forget)


@event.listens_for(Session, "do_orm_execute")
def _forget_after_writes(state):
    if not state.is_select:
        _forget(state.session)


def _enabled_frameworks(db):
    return _remembered(
        db,
        ("enabled", settings.enabled_frameworks),
        lambda: frozenset(_framework_selection(db)[3]),
    )


def _framework_controls(db, framework):
    return _remembered(
        db,
        ("controls", framework),
        lambda: tuple(
            db.execute(
                select(ControlItem.id).where(ControlItem.framework_slug == framework)
            ).scalars()
        ),
    )


def _restriction(enabled, target_framework, target_ids, source_ids):
    if target_framework is None and target_ids is None and source_ids is None:
        raise ValueError("Name the target framework, target controls or sources")
    return (
        target_framework,
        None if target_ids is None else frozenset(target_ids),
        None if source_ids is None else frozenset(source_ids),
        None if enabled is None else frozenset(enabled),
    )


def _resolution(db, restriction):
    return _remembered(
        db, ("resolution", *restriction), lambda: _resolve(db, *restriction)
    )


def crosswalk_links(
    db, *, enabled, target_framework=None, target_ids=None, source_ids=None
):
    """The links by which controls inherit evidence, one hop.

    Name the controls concerned by their framework (``target_framework``),
    the inheriting controls (``target_ids``) or the controls whose evidence
    counts (``source_ids``); together they narrow further. ``enabled`` is the
    set of frameworks whose controls take part, from the organisation's
    selection, or None for every link in the catalogue. An explicit link takes
    the place of a shared-reference pair between the same controls. Returns
    ``CrosswalkLink`` rows ordered by target and source.
    """
    restriction = _restriction(enabled, target_framework, target_ids, source_ids)

    def links():
        explicit, shared = _resolution(db, restriction)
        found = list(explicit)
        found.extend(
            CrosswalkLink(
                id=f"{source_id}:{target_id}",
                source_control_id=source_id,
                target_control_id=target_id,
                rationale=SHARED_NIST_RATIONALE,
                created_at=None,
                derived=True,
            )
            for target_id, sources in shared.items()
            for source_id in sources
        )
        # UUIDs order as their text does.
        found.sort(
            key=lambda row: (row.target_control_id.int, row.source_control_id.int)
        )
        return tuple(found)

    return _remembered(db, ("links", *restriction), links)


def crosswalk_sources(
    db, *, enabled, target_framework=None, target_ids=None, source_ids=None
):
    """The controls whose evidence each inheriting control takes, one hop.

    The links of ``crosswalk_links``, with the same restrictions, as a
    read-only ``{target id: frozenset of source ids}`` for readers that need
    no link details. Controls inheriting nothing are left out.
    """
    restriction = _restriction(enabled, target_framework, target_ids, source_ids)

    def sources():
        explicit, shared = _resolution(db, restriction)
        found = {target_id: set(ids) for target_id, ids in shared.items()}
        for link in explicit:
            found.setdefault(link.target_control_id, set()).add(link.source_control_id)
        return MappingProxyType(
            {target_id: frozenset(ids) for target_id, ids in found.items()}
        )

    return _remembered(db, ("sources", *restriction), sources)


def _resolve(db, target_framework, target_ids, source_ids, enabled):
    """Explicit links, and shared-reference sources by inheriting control.

    Returns the explicit ``CrosswalkLink`` rows and a read-only
    ``{target id: frozenset of source ids}`` of the pairs sharing a NIST
    reference across frameworks, without the pairs an explicit link names.
    """
    empty: MappingProxyType[uuid.UUID, frozenset] = MappingProxyType({})
    nothing = ((), empty)
    if (target_ids is not None and not target_ids) or (
        source_ids is not None and not source_ids
    ):
        return nothing
    if enabled is not None and target_framework is not None:
        if target_framework not in enabled:
            return nothing
    allowed = None if enabled is None else sorted(enabled)

    source_control = aliased(ControlItem)
    target_control = aliased(ControlItem)
    link = CrossFrameworkControlLink
    query = (
        select(
            link.id,
            link.source_control_id,
            link.target_control_id,
            link.rationale,
            link.created_at,
        )
        .join(source_control, source_control.id == link.source_control_id)
        .join(target_control, target_control.id == link.target_control_id)
    )
    if target_ids is not None:
        query = query.where(link.target_control_id.in_(sorted(target_ids, key=str)))
    if source_ids is not None:
        query = query.where(link.source_control_id.in_(sorted(source_ids, key=str)))
    if target_framework is not None:
        query = query.where(target_control.framework_slug == target_framework)
    if allowed is not None:
        query = query.where(
            source_control.framework_slug.in_(allowed),
            target_control.framework_slug.in_(allowed),
        )
    explicit = tuple(
        CrosswalkLink(
            id=str(link_id),
            source_control_id=source_id,
            target_control_id=target_id,
            rationale=rationale,
            created_at=created_at,
            derived=False,
        )
        for link_id, source_id, target_id, rationale, created_at in db.execute(query)
    )

    memberships = OsaControlMapping.__table__

    def by_reference(*conditions):
        """{NIST reference: {control id: framework}} for the memberships chosen."""
        found: dict[str, dict] = {}
        rows = db.execute(
            select(
                memberships.c.control_id,
                memberships.c.nist_ref,
                ControlItem.framework_slug,
            )
            .join(ControlItem, ControlItem.id == memberships.c.control_id)
            .where(*conditions)
        )
        for control_id, ref, framework in rows:
            found.setdefault(ref, {})[control_id] = framework
        return found

    in_selection = [] if allowed is None else [ControlItem.framework_slug.in_(allowed)]
    target_side = list(in_selection)
    if target_framework is not None:
        target_side.append(ControlItem.framework_slug == target_framework)
    if target_ids is not None:
        target_side.append(memberships.c.control_id.in_(sorted(target_ids, key=str)))
    # Read the named side first, then only the other side's memberships that
    # share one of its references.
    if source_ids is not None:
        sources = by_reference(
            memberships.c.control_id.in_(sorted(source_ids, key=str)), *in_selection
        )
        targets = (
            by_reference(memberships.c.nist_ref.in_(sorted(sources)), *target_side)
            if sources
            else {}
        )
    else:
        targets = by_reference(*target_side)
        source_side = list(in_selection)
        if target_framework is not None:
            # A shared reference pairs controls of different frameworks.
            source_side.append(ControlItem.framework_slug != target_framework)
        sources = (
            by_reference(memberships.c.nist_ref.in_(sorted(targets)), *source_side)
            if targets
            else {}
        )
    shared: dict[uuid.UUID, set] = {}
    for ref, inheriting in targets.items():
        offered = sources.get(ref)
        if not offered:
            continue
        # The sources of each framework's targets, built once per reference.
        others: dict[str, set] = {}
        for target_id, framework in inheriting.items():
            found = others.get(framework)
            if found is None:
                found = others[framework] = {
                    source_id
                    for source_id, source_framework in offered.items()
                    if source_framework != framework
                }
            if found:
                shared.setdefault(target_id, set()).update(found)
    for row in explicit:
        found = shared.get(row.target_control_id)
        if found is not None:
            found.discard(row.source_control_id)
    return explicit, MappingProxyType(
        {target_id: frozenset(found) for target_id, found in shared.items() if found}
    )


def _framework_sources(db, framework):
    """The framework's inheriting controls and their sources, as selected."""
    return crosswalk_sources(
        db, enabled=_enabled_frameworks(db), target_framework=framework
    )


def inherited_control_ids(db, control_ids):
    """The given controls and every enabled control whose evidence they inherit.

    Evidence lists resolve this short list before they read events or
    mappings, so the crosswalk is evaluated once per request, never once per
    event.
    """
    found = set(control_ids)
    if found:
        found.update(
            *crosswalk_sources(
                db, enabled=_enabled_frameworks(db), target_ids=found
            ).values()
        )
    return sorted(found, key=str)


def effective_framework_ids(framework, db):
    """The framework's controls and every control whose evidence they inherit.

    A sorted list, for semi-joins against those controls' mappings.
    """
    found = set(_framework_controls(db, framework))
    found.update(*_framework_sources(db, framework).values())
    return sorted(found, key=str)


def framework_evidence_sources(db, framework):
    """Each control of the framework, with the controls whose evidence it inherits.

    Returns ``{control id: set of source ids}``; every set holds the control
    itself.
    """
    sources = {
        control_id: {control_id} for control_id in _framework_controls(db, framework)
    }
    for target_id, found in _framework_sources(db, framework).items():
        sources.setdefault(target_id, {target_id}).update(found)
    return sources


def _uuid_values(name, columns, rows, *, nesting=False):
    """A VALUES list of control ids as a CTE, its rows in a stable order."""
    uuid_type = ControlItem.__table__.c.id.type
    return (
        values(*(column(c, uuid_type) for c in columns), name=name)
        .data(sorted(rows, key=lambda row: tuple(map(str, row))))
        .cte(name, nesting=nesting)
    )


def _recorded_evidence(db):
    """{control id: (count, newest timestamp)} for every control with evidence.

    PostgreSQL triggers keep ``control_evidence_stats`` exact for each
    control's own mappings; it holds a row only for a control with evidence.
    """
    return {
        control_id: (count, latest)
        for control_id, count, latest in db.execute(
            select(
                ControlEvidenceStats.control_item_id,
                ControlEvidenceStats.evidence_count,
                ControlEvidenceStats.last_evidence,
            ).where(ControlEvidenceStats.evidence_count > 0)
        )
    }


def _evidencing_pairs(db, framework):
    """(control, source) pairs for the framework's controls, each with itself.

    Only sources with evidence are kept: a source without mappings evidences
    nothing, however many frameworks are enabled. PostgreSQL's per-control
    counters name those sources; other databases, used in tests, read them
    from the mappings.
    """
    if db.get_bind().dialect.name == "postgresql":
        evidenced = _recorded_evidence(db).keys()
    else:
        evidenced = set(
            db.execute(select(Mapping.control_item_id).distinct()).scalars()
        )
    return [
        (target, source)
        for target, found in framework_evidence_sources(db, framework).items()
        for source in found
        if source in evidenced
    ]


def control_event_counts(
    db, framework, *, event_ids=None, event_conditions=(), latest=False
):
    """Distinct events evidencing each framework control, directly or by one hop.

    Equals ``count(event_id)`` of ``evidence_pairs`` grouped by control. The
    short list of (control, source) pairs is resolved first and sent as values,
    so each mapping is read once per control it evidences and the event
    restriction is evaluated once. The list holds only sources with evidence,
    however many frameworks are enabled. ``event_ids`` is a select of the
    events that count; ``event_conditions`` restrict ``Event`` columns, such
    as diary visibility and a date window. With ``latest`` the newest
    evidencing event's timestamp is returned too. Controls without evidence
    are left out.
    """
    pairs = _evidencing_pairs(db, framework)
    if not pairs:
        return {}
    edges = _uuid_values("evidence_edges", ("target_id", "source_id"), pairs)
    columns = [edges.c.target_id, func.count(func.distinct(Mapping.event_id))]
    if latest:
        columns.append(func.max(Event.timestamp))
    query = select(*columns).select_from(
        edges.join(Mapping, Mapping.control_item_id == edges.c.source_id)
    )
    if latest or event_conditions:
        query = query.join(Event, Event.id == Mapping.event_id).where(*event_conditions)
    if event_ids is not None:
        query = query.where(Mapping.event_id.in_(event_ids))
    rows = db.execute(query.group_by(edges.c.target_id)).all()
    if latest:
        return {row[0]: (row[1], row[2]) for row in rows}
    return {row[0]: row[1] for row in rows}


def recorded_control_evidence(db, framework):
    """Evidence counts and newest evidence per framework control, every event counting.

    The same figures as ``control_event_counts(..., latest=True)`` without an
    event restriction, from the trigger-maintained per-control counters. A
    control evidenced by a single control takes that control's figures. A
    control inheriting from several evidenced controls counts the largest
    one's events from the counters and adds only the other sources' events
    that the largest does not have, so the events of its largest source are
    never read. Returns ``{control id: (count, newest timestamp)}`` for
    controls with evidence. PostgreSQL only.
    """
    sources = framework_evidence_sources(db, framework)
    if not sources:
        return {}
    recorded = _recorded_evidence(db)
    result = {}
    unions: list[tuple] = []
    for target, found in sources.items():
        evidenced = sorted(
            (s for s in found if s in recorded),
            key=lambda s: (-recorded[s][0], str(s)),
        )
        if not evidenced:
            continue
        stamps = [recorded[s][1] for s in evidenced if recorded[s][1] is not None]
        largest = evidenced[0]
        result[target] = (recorded[largest][0], max(stamps) if stamps else None)
        unions.extend((target, largest, source) for source in evidenced[1:])
    if unions:
        rest = _uuid_values(
            "union_sources", ("target_id", "largest_id", "source_id"), unions
        )
        covered = aliased(Mapping)
        # OFFSET 0 keeps the check a probe of the (control, event) index for
        # each of the other sources' events; as an anti-join PostgreSQL would
        # read every mapping to hash those of the largest source.
        in_largest = (
            select(covered.id)
            .where(
                covered.control_item_id == rest.c.largest_id,
                covered.event_id == Mapping.event_id,
            )
            .offset(0)
            .exists()
        )
        for target, extra in db.execute(
            select(rest.c.target_id, func.count(func.distinct(Mapping.event_id)))
            .select_from(
                rest.join(Mapping, Mapping.control_item_id == rest.c.source_id)
            )
            .where(~in_largest)
            .group_by(rest.c.target_id)
        ):
            count, latest = result[target]
            result[target] = (count + extra, latest)
    return result


def _visible_events_only(visibility):
    """A diary visibility condition that restricts something, or None."""
    return None if visibility is None or visibility is True else visibility


def framework_event_ids(db, framework):
    """Events mapped to the framework, directly or by one crosswalk hop.

    The events of ``evidence_pairs``: those mapped to a framework control or to
    an enabled control whose evidence a framework control inherits. The short
    control list is resolved first, so callers filter events with a semi-join
    against those controls' mappings, each event once, and never expand events
    over every inheriting target. Returns None when the framework has no
    controls.
    """
    control_ids = effective_framework_ids(framework, db)
    if not control_ids:
        return None
    return select(Mapping.event_id).where(Mapping.control_item_id.in_(control_ids))


def mapped_event_count(db, framework, visibility=None):
    """Distinct events evidencing the framework, directly or by one crosswalk hop.

    Equals the distinct events of ``evidence_pairs``. ``visibility`` restricts
    the events counted.
    """
    mapped = framework_event_ids(db, framework)
    if mapped is None:
        return 0
    restriction = _visible_events_only(visibility)
    if restriction is None:
        # Every mapping names an existing event, so the mappings alone answer
        # it: the (event, control) index yields them in event order and each
        # event is counted once without reading the events table.
        query = select(func.count(func.distinct(Mapping.event_id))).where(
            mapped.whereclause
        )
    else:
        query = select(func.count(Event.id)).where(Event.id.in_(mapped), restriction)
    return int(db.execute(query).scalar() or 0)


def evidenced_control_ids(db, framework, visibility=None):
    """Framework controls with evidence, directly or by one crosswalk hop.

    Equals the distinct controls of ``evidence_pairs``. Each candidate control
    is probed once for a single mapped event, then crosswalks resolve from the
    evidenced sources to their targets, so the cost follows the number of
    controls and never the volume of evidence. ``visibility`` restricts the
    events that count as evidence.
    """
    candidates = effective_framework_ids(framework, db)
    if not candidates:
        return set()
    mapping = aliased(Mapping)
    evidence = select(mapping.id).where(mapping.control_item_id == ControlItem.id)
    restriction = _visible_events_only(visibility)
    if restriction is not None:
        evidence = evidence.join(Event, Event.id == mapping.event_id).where(restriction)
    evidenced = db.execute(
        select(ControlItem.id, ControlItem.framework_slug).where(
            ControlItem.id.in_(candidates), exists(evidence)
        )
    ).all()
    if not evidenced:
        return set()
    supported = {control_id for control_id, slug in evidenced if slug == framework}
    sources = {control_id for control_id, _ in evidenced}
    supported.update(
        target_id
        for target_id, found in _framework_sources(db, framework).items()
        if not found.isdisjoint(sources)
    )
    return supported


# The starter crosswalk stores this text on each of its same-topic links. It
# only says that the two controls share a topic, which the event page already
# explains for every control an event inherits, so events leave it out.
STARTER_SAME_TOPIC_RATIONALE = (
    "Keen starter crosswalk [same-topic]: Same control topic in the shipped "
    "catalogues. Review the evidence scope and implementation for the target; "
    "this link does not confer compliance."
)


def inherited_event_rationale(link_rationale):
    """A crosswalk link's rationale, as an event shows it beside an inherited control."""
    text = link_rationale or ""
    return "" if text == STARTER_SAME_TOPIC_RATIONALE else text


# Up to this many matching mappings are read first and the events are named by
# id, so the database fetches exactly those events. Beyond it the evidence is
# common enough for an ordered scan of events to fill a page quickly.
LISTED_MAPPINGS = 5000


def events_mapped_to(db, control_ids):
    """Events with a mapping to any of the given controls.

    The mappings index answers which events those are. When few events match,
    their ids go to the database directly, so a page of rare evidence never
    walks the whole events table. An alias keeps the subquery's mappings apart
    from any mapping the enclosing query joins, so the predicate works in
    aggregate queries too.
    """
    if not control_ids:
        return false()
    mapping = aliased(Mapping)
    matched = select(mapping.event_id).where(mapping.control_item_id.in_(control_ids))
    rows = db.execute(matched.limit(LISTED_MAPPINGS + 1)).scalars().all()
    if len(rows) > LISTED_MAPPINGS:
        return Event.id.in_(matched)
    ids = sorted(set(rows), key=str)
    return Event.id.in_(ids) if ids else false()


def evidence_pairs(framework, db):
    """Unique (target control, event) pairs, resolving crosswalks per source.

    The framework's (control, source) pairs are resolved first and sent as
    values, so each mapping meets only the controls it evidences.
    """
    pairs = _evidencing_pairs(db, framework)
    if not pairs:
        return (
            select(Mapping.control_item_id.label("control_id"), Mapping.event_id)
            .where(false())
            .subquery()
        )
    edges = _uuid_values(
        "evidence_pair_edges", ("target_id", "source_id"), pairs, nesting=True
    )
    query = select(edges.c.target_id.label("control_id"), Mapping.event_id).select_from(
        edges.join(Mapping, Mapping.control_item_id == edges.c.source_id)
    )
    # Several sources can reach one control, including the control itself, so
    # each event counts once for it.
    return query.distinct().subquery()


def event_evidence(db, framework, event_ids):
    """The framework controls each listed event evidences, directly or by one hop.

    The same pairs as ``evidence_pairs`` for a short list of events, such as
    an export batch, as ``{event id: set of control ids}``. Only those events'
    mappings are read; each is paired here with the controls its source
    evidences, so no other evidence is expanded. Events without evidence are
    left out.
    """
    evidences: dict[uuid.UUID, set] = {}
    for target, found in framework_evidence_sources(db, framework).items():
        for source in found:
            evidences.setdefault(source, set()).add(target)
    result: dict[uuid.UUID, set] = {}
    if not evidences:
        return result
    for event_id, source in db.execute(
        select(Mapping.event_id, Mapping.control_item_id).where(
            Mapping.event_id.in_(event_ids)
        )
    ):
        if source in evidences:
            result.setdefault(event_id, set()).update(evidences[source])
    return result


def has_inherited_evidence(db, framework):
    """Whether any control the framework inherits from has evidence.

    Callers keep cached direct-count paths otherwise. A disabled framework
    inherits nothing.
    """
    sources = sorted(set().union(*_framework_sources(db, framework).values()), key=str)
    if not sources:
        return False
    found = db.execute(
        select(Mapping.id).where(Mapping.control_item_id.in_(sources)).limit(1)
    )
    return found.first() is not None


def page_controls(db, framework, event_ids):
    """Resolve links once per distinct mapped source, then reuse across page events.

    Only compact control labels are loaded. Direct evidence wins, shared NIST
    references and multiple mapped sources cannot duplicate a target badge.
    """
    if not event_ids:
        return {}
    mappings = db.execute(
        select(Mapping.event_id, Mapping.control_item_id).where(
            Mapping.event_id.in_(event_ids)
        )
    ).all()
    if not mappings:
        return {}
    source_ids = {source_id for _, source_id in mappings}
    labels = (ControlItem.id, ControlItem.ref, ControlItem.title, ControlItem.type)
    direct = {
        row.id: dict(row._mapping)
        for row in db.execute(
            select(*labels).where(
                ControlItem.id.in_(source_ids), ControlItem.framework_slug == framework
            )
        )
    }
    inherited = {}
    links = crosswalk_links(
        db,
        enabled=_enabled_frameworks(db),
        target_framework=framework,
        source_ids=source_ids,
    )
    if links:
        targets = {
            row.id: row
            for row in db.execute(
                select(*labels).where(
                    ControlItem.id.in_({row.target_control_id for row in links})
                )
            )
        }
        for link in links:
            # A control removed meanwhile by another transaction is left out.
            row = targets.get(link.target_control_id)
            if row is None:
                continue
            inherited.setdefault(link.source_control_id, []).append(
                {
                    "id": str(row.id),
                    "ref": row.ref,
                    "title": row.title,
                    "type": row.type,
                    "inherited_from_control_id": str(link.source_control_id),
                }
            )
    result = {}
    # Two passes preserve direct evidence over inherited evidence regardless of
    # mapping order. Sorting source UUIDs makes multi-source attribution stable.
    for event_id, source_id in mappings:
        if source_id in direct:
            item = direct[source_id]
            result.setdefault(event_id, {})[str(source_id)] = {
                **item,
                "id": str(source_id),
            }
    for event_id, source_id in sorted(mappings, key=lambda row: str(row[1])):
        targets = result.setdefault(event_id, {})
        for item in inherited.get(source_id, ()):
            targets.setdefault(item["id"], item)
    return {
        event_id: sorted(items.values(), key=lambda item: (item["ref"], item["id"]))
        for event_id, items in result.items()
    }


def inherited_event_controls(db, framework, event_id):
    """The framework controls an event evidences by inheritance, one hop.

    Rows of (mapping, target control, link, source control) in the order of
    the targets' references, then of the sources, so a caller showing one row
    per target keeps the first. A target the event is also mapped to directly
    appears too; callers that list direct evidence first skip it.
    """
    source_ids = set(
        db.execute(
            select(Mapping.control_item_id).where(Mapping.event_id == event_id)
        ).scalars()
    )
    if not source_ids:
        return []
    links = {
        (row.source_control_id, row.target_control_id): row
        for row in crosswalk_links(
            db,
            enabled=_enabled_frameworks(db),
            target_framework=framework,
            source_ids=source_ids,
        )
    }
    if not links:
        return []
    edges = _uuid_values("event_links", ("source_id", "target_id"), list(links))
    source = aliased(ControlItem)
    rows = (
        db.query(Mapping, ControlItem, source)
        .select_from(Mapping)
        .join(edges, edges.c.source_id == Mapping.control_item_id)
        .join(ControlItem, ControlItem.id == edges.c.target_id)
        .join(source, source.id == edges.c.source_id)
        .filter(Mapping.event_id == event_id)
        .order_by(ControlItem.ref.asc(), edges.c.source_id.asc())
        .all()
    )
    return [
        (mapping, target, links[(origin.id, target.id)], origin)
        for mapping, target, origin in rows
    ]
