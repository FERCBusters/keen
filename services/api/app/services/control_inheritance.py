"""One-hop evidence inheritance, starting from evidence before joining OSA memberships."""
from sqlalchemy import or_, select
from app.db.models import ControlItem, CrossFrameworkControlLink, OsaControlMapping, Mapping


def osa_pairs():
    source = OsaControlMapping.__table__.alias('source_membership')
    target = OsaControlMapping.__table__.alias('target_membership')
    source_control = ControlItem.__table__.alias('source_control')
    target_control = ControlItem.__table__.alias('target_control')
    return source, target, source_control, target_control


def enabled_control_ids(db):
    from app.api.routes.frameworks import _framework_selection
    enabled = _framework_selection(db)[3]
    return select(ControlItem.id).where(ControlItem.framework_slug.in_(enabled))


def source_ids_for_control(control_id, db=None):
    explicit = select(CrossFrameworkControlLink.source_control_id).where(
        CrossFrameworkControlLink.target_control_id == control_id)
    source, target, sc, tc = osa_pairs()
    osa = select(source.c.control_id).select_from(source.join(target, source.c.nist_ref == target.c.nist_ref)
        .join(sc, sc.c.id == source.c.control_id).join(tc, tc.c.id == target.c.control_id)
    ).where(target.c.control_id == control_id, sc.c.framework_slug != tc.c.framework_slug)
    if db is not None:
        enabled = enabled_control_ids(db)
        explicit = explicit.where(CrossFrameworkControlLink.source_control_id.in_(enabled),
                                  CrossFrameworkControlLink.target_control_id.in_(enabled))
        osa = osa.where(sc.c.id.in_(enabled), tc.c.id.in_(enabled))
    return explicit.union(osa)


def effective_control_ids(control_id, db=None):
    return select(ControlItem.id).where(
        or_(ControlItem.id == control_id, ControlItem.id.in_(source_ids_for_control(control_id, db))))


def effective_framework_ids(framework, db=None):
    direct = select(ControlItem.id).where(ControlItem.framework_slug == framework)
    explicit = select(CrossFrameworkControlLink.source_control_id).join(
        ControlItem, ControlItem.id == CrossFrameworkControlLink.target_control_id
    ).where(ControlItem.framework_slug == framework)
    source, target, sc, tc = osa_pairs()
    osa = select(source.c.control_id).select_from(source.join(target, source.c.nist_ref == target.c.nist_ref)
        .join(sc, sc.c.id == source.c.control_id).join(tc, tc.c.id == target.c.control_id)
    ).where(tc.c.framework_slug == framework, sc.c.framework_slug != tc.c.framework_slug)
    if db is not None:
        enabled = enabled_control_ids(db)
        explicit = explicit.where(CrossFrameworkControlLink.source_control_id.in_(enabled),
                                  CrossFrameworkControlLink.target_control_id.in_(enabled))
        osa = osa.where(sc.c.id.in_(enabled), tc.c.id.in_(enabled))
    return direct.union(explicit, osa)


def event_has_control(control_id, event_id, db=None):
    return select(Mapping.id).where(Mapping.event_id == event_id,
        Mapping.control_item_id.in_(effective_control_ids(control_id, db))).exists()


def evidence_pairs(framework, db=None, event_ids=None):
    """Unique (target control, event) pairs, resolving crosswalks per source.

    Crosswalk deduplication happens before expansion over matching mappings.
    Optional event restrictions apply to both the source lookup and evidence
    branches, so exports and filtered facets cannot expand unrelated evidence.
    """
    from app.db.models import effective_cross_framework_links
    from app.api.routes.frameworks import _framework_selection
    mapped = select(Mapping.event_id, Mapping.control_item_id)
    if event_ids is not None:
        mapped = mapped.where(Mapping.event_id.in_(event_ids))
    mapped = mapped.subquery()
    direct = select(ControlItem.id.label('control_id'), mapped.c.event_id).join(
        mapped, mapped.c.control_item_id == ControlItem.id
    ).where(ControlItem.framework_slug == framework)
    enabled = _framework_selection(db)[3] if db is not None else None
    links = effective_cross_framework_links(
        source_ids=select(mapped.c.control_item_id).distinct(),
        target_framework=framework, enabled=enabled)
    inherited = select(links.c.target_control_id.label('control_id'), mapped.c.event_id).join(
        mapped, mapped.c.control_item_id == links.c.source_control_id)
    # Multiple mapped sources can reach the same target, including a target
    # already mapped directly. UNION counts each event once for that target.
    return direct.union(inherited).subquery()


def has_inherited_evidence(db, framework):
    """Only leave cached direct-count paths when enabled sources have evidence.

    Start with actual mappings, never the global control-pair relation. Empty
    mapping tables and disabled crosswalks must not cause catalogue expansion.
    """
    from app.api.routes.frameworks import _framework_selection
    enabled = _framework_selection(db)[3]
    if framework not in enabled:
        return False
    source_control = ControlItem.__table__.alias('inherited_source')
    target_control = ControlItem.__table__.alias('inherited_target')
    links = CrossFrameworkControlLink.__table__
    explicit = select(Mapping.id).select_from(
        Mapping.__table__.join(links, links.c.source_control_id == Mapping.control_item_id)
        .join(source_control, source_control.c.id == links.c.source_control_id)
        .join(target_control, target_control.c.id == links.c.target_control_id)
    ).where(source_control.c.framework_slug.in_(enabled),
            target_control.c.framework_slug == framework).limit(1)
    if db.execute(explicit).first() is not None:
        return True
    if not (enabled - {framework}):
        return False
    source, target, sc, tc = osa_pairs()
    derived = select(Mapping.id).select_from(
        Mapping.__table__.join(source, source.c.control_id == Mapping.control_item_id)
        .join(sc, sc.c.id == source.c.control_id)
        .join(target, target.c.nist_ref == source.c.nist_ref)
        .join(tc, tc.c.id == target.c.control_id)
    ).where(sc.c.framework_slug.in_(enabled - {framework}),
            tc.c.framework_slug == framework).limit(1)
    return db.execute(derived).first() is not None


def page_controls(db, framework, event_ids):
    """Resolve links once per distinct mapped source, then reuse across page events.

    Only compact control labels are loaded. Direct evidence wins, shared NIST
    references and multiple mapped sources cannot duplicate a target badge.
    """
    from app.api.routes.frameworks import _framework_selection
    from app.db.models import effective_cross_framework_links
    if not event_ids:
        return {}
    mappings = db.execute(select(Mapping.event_id, Mapping.control_item_id).where(
        Mapping.event_id.in_(event_ids))).all()
    if not mappings:
        return {}
    source_ids = {source_id for _, source_id in mappings}
    labels = (ControlItem.id, ControlItem.ref, ControlItem.title, ControlItem.type)
    direct = {row.id: dict(row._mapping) for row in db.execute(
        select(*labels).where(ControlItem.id.in_(source_ids),
                              ControlItem.framework_slug == framework))}
    enabled = _framework_selection(db)[3]
    inherited = {}
    if framework in enabled:
        links = effective_cross_framework_links(source_ids=source_ids,
            target_framework=framework, enabled=enabled)
        for row in db.execute(select(links.c.source_control_id, *labels).join(
                ControlItem, ControlItem.id == links.c.target_control_id)):
            inherited.setdefault(row.source_control_id, []).append({
                "id": str(row.id), "ref": row.ref, "title": row.title, "type": row.type,
                "inherited_from_control_id": str(row.source_control_id)})
    result = {}
    # Two passes preserve direct evidence over inherited evidence regardless of
    # mapping order. Sorting source UUIDs makes multi-source attribution stable.
    for event_id, source_id in mappings:
        if source_id in direct:
            item = direct[source_id]
            result.setdefault(event_id, {})[str(source_id)] = {**item, "id": str(source_id)}
    for event_id, source_id in sorted(mappings, key=lambda row: str(row[1])):
        targets = result.setdefault(event_id, {})
        for item in inherited.get(source_id, ()):
            targets.setdefault(item["id"], item)
    return {event_id: sorted(items.values(), key=lambda item: (item["ref"], item["id"]))
            for event_id, items in result.items()}
