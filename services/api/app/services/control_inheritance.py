"""One-hop evidence inheritance, starting from evidence before joining OSA memberships."""

from sqlalchemy import column, exists, false, func, select, values
from sqlalchemy.orm import aliased

from app.db.models import (
    ControlEvidenceStats,
    ControlItem,
    CrossFrameworkControlLink,
    Event,
    Mapping,
    OsaControlMapping,
)
from app.services.framework_catalogue import _framework_selection


def osa_pairs():
    source = OsaControlMapping.__table__.alias("source_membership")
    target = OsaControlMapping.__table__.alias("target_membership")
    source_control = ControlItem.__table__.alias("source_control")
    target_control = ControlItem.__table__.alias("target_control")
    return source, target, source_control, target_control


def _enabled_frameworks(db):
    return _framework_selection(db)[3]


def enabled_control_ids(db):
    enabled = _enabled_frameworks(db)
    return select(ControlItem.id).where(ControlItem.framework_slug.in_(enabled))


def source_ids_for_controls(control_ids, db=None):
    """Controls whose evidence any of the given controls inherits, one hop away."""
    explicit = select(CrossFrameworkControlLink.source_control_id).where(
        CrossFrameworkControlLink.target_control_id.in_(control_ids)
    )
    source, target, sc, tc = osa_pairs()
    osa = (
        select(source.c.control_id)
        .select_from(
            source.join(target, source.c.nist_ref == target.c.nist_ref)
            .join(sc, sc.c.id == source.c.control_id)
            .join(tc, tc.c.id == target.c.control_id)
        )
        .where(
            target.c.control_id.in_(control_ids),
            sc.c.framework_slug != tc.c.framework_slug,
        )
    )
    if db is not None:
        enabled = enabled_control_ids(db)
        explicit = explicit.where(
            CrossFrameworkControlLink.source_control_id.in_(enabled),
            CrossFrameworkControlLink.target_control_id.in_(enabled),
        )
        osa = osa.where(sc.c.id.in_(enabled), tc.c.id.in_(enabled))
    return explicit.union(osa)


def inherited_control_ids(db, control_ids):
    """The given controls and every enabled control whose evidence they inherit.

    Evidence lists resolve this short list in one query before they read events
    or mappings, so the crosswalk is evaluated once per request, never once per
    event.
    """
    found = set(control_ids)
    if found:
        chosen = sorted(found, key=str)
        found.update(db.execute(source_ids_for_controls(chosen, db)).scalars())
    return sorted(found, key=str)


def effective_framework_ids(framework, db=None):
    """The framework's controls and every control whose evidence they inherit.

    With a session, inheritance needs the framework enabled and comes only from
    enabled controls. The shared-NIST branch reads the memberships of the
    other enabled frameworks' controls and keeps those sharing a reference
    with this framework, so its cost follows those frameworks' memberships
    alone, however many catalogue controls hold the same reference.
    """
    direct = select(ControlItem.id).where(ControlItem.framework_slug == framework)
    target_control = aliased(ControlItem)
    explicit = (
        select(CrossFrameworkControlLink.source_control_id)
        .join(
            target_control,
            target_control.id == CrossFrameworkControlLink.target_control_id,
        )
        .where(target_control.framework_slug == framework)
    )
    source, target, sc, tc = osa_pairs()
    if db is None:
        osa = (
            select(source.c.control_id)
            .select_from(
                source.join(target, source.c.nist_ref == target.c.nist_ref)
                .join(sc, sc.c.id == source.c.control_id)
                .join(tc, tc.c.id == target.c.control_id)
            )
            .where(
                tc.c.framework_slug == framework,
                sc.c.framework_slug != tc.c.framework_slug,
            )
        )
        return direct.union(explicit, osa)
    enabled = _enabled_frameworks(db)
    if framework not in enabled:
        return direct
    explicit = explicit.where(
        CrossFrameworkControlLink.source_control_id.in_(
            select(ControlItem.id).where(
                ControlItem.framework_slug.in_(sorted(enabled))
            )
        )
    )
    others = sorted(enabled - {framework})
    if not others:
        return direct.union(explicit)
    shared_refs = (
        select(target.c.nist_ref)
        .join(tc, tc.c.id == target.c.control_id)
        .where(tc.c.framework_slug == framework)
    )
    osa = (
        select(source.c.control_id)
        .join(sc, sc.c.id == source.c.control_id)
        .where(sc.c.framework_slug.in_(others), source.c.nist_ref.in_(shared_refs))
    )
    return direct.union(explicit, osa)


def framework_evidence_sources(db, framework):
    """Each control of the framework, with the controls whose evidence it inherits.

    Returns ``{control id: set of source ids}``; every set holds the control
    itself. Inheritance is that of ``evidence_pairs``: explicit links from
    enabled controls, and controls of another enabled framework sharing a NIST
    reference, when the framework is enabled. Short indexed reads of the
    framework's links and of the enabled frameworks' NIST memberships resolve
    it, and the shared references are paired here.
    """
    sources = {
        control_id: {control_id}
        for control_id in db.execute(
            select(ControlItem.id).where(ControlItem.framework_slug == framework)
        ).scalars()
    }
    enabled = _enabled_frameworks(db)
    if not sources or framework not in enabled:
        return sources
    source_control = aliased(ControlItem)
    target_control = aliased(ControlItem)
    for source_id, target_id in db.execute(
        select(
            CrossFrameworkControlLink.source_control_id,
            CrossFrameworkControlLink.target_control_id,
        )
        .join(
            source_control,
            source_control.id == CrossFrameworkControlLink.source_control_id,
        )
        .join(
            target_control,
            target_control.id == CrossFrameworkControlLink.target_control_id,
        )
        .where(
            target_control.framework_slug == framework,
            source_control.framework_slug.in_(sorted(enabled)),
        )
    ):
        sources[target_id].add(source_id)
    others = sorted(enabled - {framework})
    if not others:
        return sources
    memberships = OsaControlMapping.__table__

    def by_reference(frameworks):
        found: dict[str, set] = {}
        for control_id, ref in db.execute(
            select(memberships.c.control_id, memberships.c.nist_ref)
            .join(ControlItem, ControlItem.id == memberships.c.control_id)
            .where(ControlItem.framework_slug.in_(frameworks))
        ):
            found.setdefault(ref, set()).add(control_id)
        return found

    targets_by_ref = by_reference([framework])
    if targets_by_ref:
        for ref, source_ids in by_reference(others).items():
            for target_id in targets_by_ref.get(ref, ()):
                sources[target_id].update(source_ids)
    return sources


def _uuid_values(name, columns, rows):
    """A VALUES list of control ids as a CTE, its rows in a stable order."""
    uuid_type = ControlItem.__table__.c.id.type
    return (
        values(*(column(c, uuid_type) for c in columns), name=name)
        .data(sorted(rows, key=lambda row: tuple(map(str, row))))
        .cte(name)
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


def control_event_counts(
    db, framework, *, event_ids=None, event_conditions=(), latest=False
):
    """Distinct events evidencing each framework control, directly or by one hop.

    Equals ``count(event_id)`` of ``evidence_pairs`` grouped by control. The
    short list of (control, source) pairs is resolved first and sent as values,
    so each mapping is read once per control it evidences and the event
    restriction is evaluated once. On PostgreSQL the list holds only sources
    with evidence, however many frameworks are enabled. ``event_ids`` is a
    select of the events that count; ``event_conditions`` restrict ``Event``
    columns, such as diary visibility and a date window. With ``latest`` the
    newest evidencing event's timestamp is returned too. Controls without
    evidence are left out.
    """
    sources = framework_evidence_sources(db, framework)
    if db.get_bind().dialect.name == "postgresql":
        evidenced = _recorded_evidence(db)
        sources = {
            target: {source for source in found if source in evidenced}
            for target, found in sources.items()
        }
    pairs = [(target, source) for target, found in sources.items() for source in found]
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
    control_ids = db.execute(effective_framework_ids(framework, db)).scalars().all()
    if not control_ids:
        return None
    return select(Mapping.event_id).where(
        Mapping.control_item_id.in_(sorted(control_ids, key=str))
    )


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
    from app.db.models import effective_cross_framework_links

    candidates = db.execute(effective_framework_ids(framework, db)).scalars().all()
    if not candidates:
        return set()
    mapping = aliased(Mapping)
    evidence = select(mapping.id).where(mapping.control_item_id == ControlItem.id)
    restriction = _visible_events_only(visibility)
    if restriction is not None:
        evidence = evidence.join(Event, Event.id == mapping.event_id).where(restriction)
    evidenced = db.execute(
        select(ControlItem.id, ControlItem.framework_slug).where(
            ControlItem.id.in_(sorted(candidates, key=str)), exists(evidence)
        )
    ).all()
    if not evidenced:
        return set()
    supported = {control_id for control_id, slug in evidenced if slug == framework}
    links = effective_cross_framework_links(
        source_ids=sorted((control_id for control_id, _ in evidenced), key=str),
        target_framework=framework,
        enabled=_enabled_frameworks(db),
    )
    supported.update(db.execute(select(links.c.target_control_id).distinct()).scalars())
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


def evidence_pairs(framework, db=None, event_ids=None):
    """Unique (target control, event) pairs, resolving crosswalks per source.

    Crosswalk deduplication happens before expansion over matching mappings.
    Optional event restrictions apply to both the source lookup and evidence
    branches, so exports and filtered facets cannot expand unrelated evidence.
    """
    from app.db.models import effective_cross_framework_links

    mapped = select(Mapping.event_id, Mapping.control_item_id)
    if event_ids is not None:
        mapped = mapped.where(Mapping.event_id.in_(event_ids))
    mapped = mapped.subquery()
    direct = (
        select(ControlItem.id.label("control_id"), mapped.c.event_id)
        .join(mapped, mapped.c.control_item_id == ControlItem.id)
        .where(ControlItem.framework_slug == framework)
    )
    enabled = _enabled_frameworks(db) if db is not None else None
    links = effective_cross_framework_links(
        source_ids=select(mapped.c.control_item_id).distinct(),
        target_framework=framework,
        enabled=enabled,
    )
    inherited = select(
        links.c.target_control_id.label("control_id"), mapped.c.event_id
    ).join(mapped, mapped.c.control_item_id == links.c.source_control_id)
    # Multiple mapped sources can reach the same target, including a target
    # already mapped directly. UNION counts each event once for that target.
    return direct.union(inherited).subquery()


def has_inherited_evidence(db, framework):
    """Only leave cached direct-count paths when enabled sources have evidence.

    Start with actual mappings, never the global control-pair relation. Empty
    mapping tables and disabled crosswalks must not cause catalogue expansion.
    """
    enabled = _enabled_frameworks(db)
    if framework not in enabled:
        return False
    source_control = ControlItem.__table__.alias("inherited_source")
    target_control = ControlItem.__table__.alias("inherited_target")
    links = CrossFrameworkControlLink.__table__
    explicit = (
        select(Mapping.id)
        .select_from(
            Mapping.__table__.join(
                links, links.c.source_control_id == Mapping.control_item_id
            )
            .join(source_control, source_control.c.id == links.c.source_control_id)
            .join(target_control, target_control.c.id == links.c.target_control_id)
        )
        .where(
            source_control.c.framework_slug.in_(enabled),
            target_control.c.framework_slug == framework,
        )
        .limit(1)
    )
    if db.execute(explicit).first() is not None:
        return True
    if not (enabled - {framework}):
        return False
    source, target, sc, tc = osa_pairs()
    derived = (
        select(Mapping.id)
        .select_from(
            Mapping.__table__.join(
                source, source.c.control_id == Mapping.control_item_id
            )
            .join(sc, sc.c.id == source.c.control_id)
            .join(target, target.c.nist_ref == source.c.nist_ref)
            .join(tc, tc.c.id == target.c.control_id)
        )
        .where(
            sc.c.framework_slug.in_(enabled - {framework}),
            tc.c.framework_slug == framework,
        )
        .limit(1)
    )
    return db.execute(derived).first() is not None


def page_controls(db, framework, event_ids):
    """Resolve links once per distinct mapped source, then reuse across page events.

    Only compact control labels are loaded. Direct evidence wins, shared NIST
    references and multiple mapped sources cannot duplicate a target badge.
    """
    from app.db.models import effective_cross_framework_links

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
    enabled = _enabled_frameworks(db)
    inherited = {}
    if framework in enabled:
        links = effective_cross_framework_links(
            source_ids=source_ids, target_framework=framework, enabled=enabled
        )
        for row in db.execute(
            select(links.c.source_control_id, *labels).join(
                ControlItem, ControlItem.id == links.c.target_control_id
            )
        ):
            inherited.setdefault(row.source_control_id, []).append(
                {
                    "id": str(row.id),
                    "ref": row.ref,
                    "title": row.title,
                    "type": row.type,
                    "inherited_from_control_id": str(row.source_control_id),
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
