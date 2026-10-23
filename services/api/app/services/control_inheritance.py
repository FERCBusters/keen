"""One-hop evidence inheritance, starting from evidence before joining OSA memberships."""

from sqlalchemy import false, select
from sqlalchemy.orm import aliased

from app.db.models import (
    ControlItem,
    CrossFrameworkControlLink,
    Event,
    Mapping,
    OsaControlMapping,
)


def osa_pairs():
    source = OsaControlMapping.__table__.alias("source_membership")
    target = OsaControlMapping.__table__.alias("target_membership")
    source_control = ControlItem.__table__.alias("source_control")
    target_control = ControlItem.__table__.alias("target_control")
    return source, target, source_control, target_control


def enabled_control_ids(db):
    from app.services.framework_catalogue import _framework_selection

    enabled = _framework_selection(db)[3]
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
    direct = select(ControlItem.id).where(ControlItem.framework_slug == framework)
    explicit = (
        select(CrossFrameworkControlLink.source_control_id)
        .join(
            ControlItem, ControlItem.id == CrossFrameworkControlLink.target_control_id
        )
        .where(ControlItem.framework_slug == framework)
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
            tc.c.framework_slug == framework, sc.c.framework_slug != tc.c.framework_slug
        )
    )
    if db is not None:
        enabled = enabled_control_ids(db)
        explicit = explicit.where(
            CrossFrameworkControlLink.source_control_id.in_(enabled),
            CrossFrameworkControlLink.target_control_id.in_(enabled),
        )
        osa = osa.where(sc.c.id.in_(enabled), tc.c.id.in_(enabled))
    return direct.union(explicit, osa)


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
    from app.services.framework_catalogue import _framework_selection

    mapped = select(Mapping.event_id, Mapping.control_item_id)
    if event_ids is not None:
        mapped = mapped.where(Mapping.event_id.in_(event_ids))
    mapped = mapped.subquery()
    direct = (
        select(ControlItem.id.label("control_id"), mapped.c.event_id)
        .join(mapped, mapped.c.control_item_id == ControlItem.id)
        .where(ControlItem.framework_slug == framework)
    )
    enabled = _framework_selection(db)[3] if db is not None else None
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
    from app.services.framework_catalogue import _framework_selection

    enabled = _framework_selection(db)[3]
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
    from app.services.framework_catalogue import _framework_selection

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
    enabled = _framework_selection(db)[3]
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
