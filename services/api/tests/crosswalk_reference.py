"""The crosswalk rule written as one SQL relation, as a test oracle.

Keen resolved inherited evidence through this relation before
``control_inheritance.crosswalk_links`` read the links and memberships with
short queries and paired shared references in Python. Tests compare the
resolver and the readers built on it with this relation on the same data.
"""

import uuid

from app.db.models import (
    ControlItem,
    CrossFrameworkControlLink,
    Mapping,
    OsaControlMapping,
)
from app.services.framework_catalogue import _framework_selection
from sqlalchemy import DateTime, String, cast, exists, literal, select

_osa_source = OsaControlMapping.__table__.alias("osa_source")
_osa_target = OsaControlMapping.__table__.alias("osa_target")
_source_control = ControlItem.__table__.alias("osa_source_control")
_target_control = ControlItem.__table__.alias("osa_target_control")
_explicit = CrossFrameworkControlLink.__table__


def reference_links(*, source_ids=None, target_framework=None, enabled=None):
    """Explicit links and shared-NIST pairs, an explicit link taking precedence."""
    explicit = select(
        cast(_explicit.c.id, String).label("id"),
        _explicit.c.source_control_id,
        _explicit.c.target_control_id,
        _explicit.c.rationale,
        _explicit.c.created_at,
        literal(False).label("derived"),
    )
    pairs = (
        select(
            _osa_source.c.control_id.label("source_control_id"),
            _osa_target.c.control_id.label("target_control_id"),
        )
        .select_from(
            _osa_source.join(
                _osa_target, _osa_source.c.nist_ref == _osa_target.c.nist_ref
            )
            .join(_source_control, _source_control.c.id == _osa_source.c.control_id)
            .join(_target_control, _target_control.c.id == _osa_target.c.control_id)
        )
        .where(_source_control.c.framework_slug != _target_control.c.framework_slug)
    )
    if source_ids is not None:
        explicit = explicit.where(_explicit.c.source_control_id.in_(source_ids))
        pairs = pairs.where(_osa_source.c.control_id.in_(source_ids))
    if target_framework is not None:
        targets = select(ControlItem.id).where(
            ControlItem.framework_slug == target_framework
        )
        explicit = explicit.where(_explicit.c.target_control_id.in_(targets))
        pairs = pairs.where(_target_control.c.framework_slug == target_framework)
    if enabled is not None:
        allowed = select(ControlItem.id).where(ControlItem.framework_slug.in_(enabled))
        explicit = explicit.where(
            _explicit.c.source_control_id.in_(allowed),
            _explicit.c.target_control_id.in_(allowed),
        )
        pairs = pairs.where(
            _source_control.c.framework_slug.in_(enabled),
            _target_control.c.framework_slug.in_(enabled),
        )
    unique_pairs = pairs.distinct().subquery()
    derived = select(
        (
            cast(unique_pairs.c.source_control_id, String)
            + literal(":")
            + cast(unique_pairs.c.target_control_id, String)
        ).label("id"),
        unique_pairs.c.source_control_id,
        unique_pairs.c.target_control_id,
        literal(
            "Related evidence via shared NIST controls in Open Security Architecture (CC BY-SA 4.0). "
            "Keen-derived crosswalk; review relevance and coverage gaps, as related controls can differ in scope."
        ),
        literal(None, type_=DateTime),
        literal(True),
    ).where(
        ~exists(
            select(_explicit.c.id).where(
                _explicit.c.source_control_id == unique_pairs.c.source_control_id,
                _explicit.c.target_control_id == unique_pairs.c.target_control_id,
            )
        )
    )
    return explicit.union_all(derived).subquery("effective_cross_framework_links")


def _text_id(value):
    """An id in PostgreSQL's text form; SQLite stores UUIDs as bare hex."""
    return ":".join(str(uuid.UUID(part)) for part in value.split(":"))


def _ordered(rows):
    return sorted(rows, key=lambda row: (str(row[2]), str(row[1]), row[0]))


def reference_rows(db, *, target_ids=None, **restrictions):
    """The relation's links as comparable tuples, in a stable order."""
    links = reference_links(**restrictions)
    query = select(links)
    if target_ids is not None:
        query = query.where(links.c.target_control_id.in_(target_ids))
    return _ordered(
        (
            _text_id(row.id),
            row.source_control_id,
            row.target_control_id,
            row.rationale,
            row.created_at,
            row.derived,
        )
        for row in db.execute(query)
    )


def resolved_rows(links):
    """``crosswalk_links`` rows as the tuples ``reference_rows`` returns."""
    return _ordered(
        (
            link.id,
            link.source_control_id,
            link.target_control_id,
            link.rationale,
            link.created_at,
            link.derived,
        )
        for link in links
    )


def reference_evidence_pairs(framework, db, event_ids=None):
    """Unique (control, event) pairs of the framework under the selection."""
    mapped = select(Mapping.event_id, Mapping.control_item_id)
    if event_ids is not None:
        mapped = mapped.where(Mapping.event_id.in_(event_ids))
    mapped = mapped.subquery()
    direct = (
        select(ControlItem.id.label("control_id"), mapped.c.event_id)
        .join(mapped, mapped.c.control_item_id == ControlItem.id)
        .where(ControlItem.framework_slug == framework)
    )
    links = reference_links(
        source_ids=select(mapped.c.control_item_id).distinct(),
        target_framework=framework,
        enabled=sorted(_framework_selection(db)[3]),
    )
    inherited = select(
        links.c.target_control_id.label("control_id"), mapped.c.event_id
    ).join(mapped, mapped.c.control_item_id == links.c.source_control_id)
    return direct.union(inherited).subquery()


def reference_pair_set(framework, db, event_ids=None):
    """The reference pairs as a set of (control id, event id)."""
    return set(db.execute(select(reference_evidence_pairs(framework, db, event_ids))))
