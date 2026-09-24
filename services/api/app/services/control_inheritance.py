"""One-hop, directed evidence inheritance. No stored mappings are duplicated."""
from sqlalchemy import or_, select

from app.db.models import ControlItem, CrossFrameworkControlLink, Mapping


def source_ids_for_control(control_id):
    return select(CrossFrameworkControlLink.source_control_id).where(
        CrossFrameworkControlLink.target_control_id == control_id
    )


def effective_control_ids(control_id):
    return select(ControlItem.id).where(
        or_(ControlItem.id == control_id, ControlItem.id.in_(source_ids_for_control(control_id)))
    )


def effective_framework_ids(framework):
    direct = select(ControlItem.id).where(ControlItem.framework_slug == framework)
    inherited = select(CrossFrameworkControlLink.source_control_id).join(
        ControlItem, ControlItem.id == CrossFrameworkControlLink.target_control_id
    ).where(ControlItem.framework_slug == framework)
    return direct.union(inherited)


def event_has_control(control_id, event_id):
    return select(Mapping.id).where(
        Mapping.event_id == event_id,
        Mapping.control_item_id.in_(effective_control_ids(control_id)),
    ).exists()


def evidence_pairs(framework):
    """Unique (target control, event) pairs, direct and inherited, one hop."""
    direct = select(ControlItem.id.label("control_id"), Mapping.event_id.label("event_id")).join(
        Mapping, Mapping.control_item_id == ControlItem.id
    ).where(ControlItem.framework_slug == framework)
    inherited = select(
        CrossFrameworkControlLink.target_control_id.label("control_id"),
        Mapping.event_id.label("event_id"),
    ).join(
        Mapping, Mapping.control_item_id == CrossFrameworkControlLink.source_control_id
    ).join(
        ControlItem, ControlItem.id == CrossFrameworkControlLink.target_control_id
    ).where(ControlItem.framework_slug == framework)
    return direct.union(inherited).subquery()
