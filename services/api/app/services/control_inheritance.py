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


def evidence_pairs(framework, db=None):
    """Unique (target control, event) pairs. Never enumerate a global cross product."""
    direct = select(ControlItem.id.label('control_id'), Mapping.event_id.label('event_id')).join(
        Mapping, Mapping.control_item_id == ControlItem.id).where(ControlItem.framework_slug == framework)
    explicit = select(CrossFrameworkControlLink.target_control_id.label('control_id'),
        Mapping.event_id.label('event_id')).join(
        Mapping, Mapping.control_item_id == CrossFrameworkControlLink.source_control_id).join(
        ControlItem, ControlItem.id == CrossFrameworkControlLink.target_control_id
    ).where(ControlItem.framework_slug == framework)
    source, target, sc, tc = osa_pairs()
    osa = select(target.c.control_id.label('control_id'), Mapping.event_id.label('event_id')).select_from(
        Mapping.__table__.join(source, source.c.control_id == Mapping.control_item_id)
        .join(target, source.c.nist_ref == target.c.nist_ref)
        .join(sc, sc.c.id == source.c.control_id).join(tc, tc.c.id == target.c.control_id)
    ).where(tc.c.framework_slug == framework, sc.c.framework_slug != tc.c.framework_slug)
    if db is not None:
        enabled = enabled_control_ids(db)
        explicit = explicit.where(CrossFrameworkControlLink.source_control_id.in_(enabled),
                                  CrossFrameworkControlLink.target_control_id.in_(enabled))
        osa = osa.where(sc.c.id.in_(enabled), tc.c.id.in_(enabled))
    return direct.union(explicit, osa).subquery()
