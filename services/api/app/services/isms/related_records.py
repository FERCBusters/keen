"""Records other features refer to, resolved with the reader's own access.

SWOT items relate to objectives, documents, assets, people, vendors, risks and
legal register items. Legal register items relate to documents, assets, risks
and SWOT items. A relationship between two kinds that each keep relationships
(a SWOT item and a legal register item) is one row that both ends read and
either end can make or remove. Stories relate to
all of those records and also to SWOT items, legal register items,
effectiveness measures and meeting minutes. A reference is resolved only when the feature is enabled
and the reader can read the record itself, so a relationship never shows more
than the record's own page would.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mosp.services.errors import DomainError

from app.db.models import (
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsLegalItem,
    IsmsLegalItemLink,
    IsmsMeeting,
    IsmsObjective,
    IsmsPerson,
    IsmsSwot,
    IsmsVendor,
    Risk,
    RiskAsset,
)

from .access import can_read_isms

TargetRecord = (
    IsmsObjective
    | IsmsDocument
    | RiskAsset
    | IsmsPerson
    | IsmsVendor
    | Risk
    | IsmsSwot
    | IsmsEffectivenessMeasure
    | IsmsMeeting
    | IsmsLegalItem
)
TARGETS: dict[str, type[TargetRecord]] = {
    "objective": IsmsObjective,
    "document": IsmsDocument,
    "asset": RiskAsset,
    "person": IsmsPerson,
    "vendor": IsmsVendor,
    "risk": Risk,
    "swot": IsmsSwot,
    "effectiveness_measure": IsmsEffectivenessMeasure,
    "meeting": IsmsMeeting,
    "legal_item": IsmsLegalItem,
}
SEARCH_FIELDS = {
    "objective": "goal",
    "document": "title",
    "risk": "threat_summary",
    "swot": "title",
    "effectiveness_measure": "summary",
    "meeting": "title",
    "legal_item": "title",
}
PAGES = {
    "objective": "/isms-objective.html?id={id}",
    "document": "/isms-document.html?id={id}",
    "asset": "/isms-asset.html?id={id}",
    "risk": "/risk.html?id={id}",
    "person": "/person.html?id={id}",
    "vendor": "/vendor.html?id={id}",
    "swot": "/swot.html?id={id}",
    "effectiveness_measure": "/isms-effectiveness-measure.html?id={id}",
    "meeting": "/isms-meeting.html?id={id}",
    "legal_item": "/legal-register.html?id={id}",
}


def resolve_target(db, user, kind, target_id, *, required=True):
    """The record, or ``None`` (``required=False``) when it is missing or withheld."""
    if kind not in TARGETS:
        raise DomainError(400, "Unknown relationship type")
    from app.services.features import ENTITY_FEATURES, state

    if not state(db).enabled(ENTITY_FEATURES[kind]):
        if required:
            raise DomainError(404, "Related feature is disabled")
        return None
    row = db.get(TARGETS[kind], target_id)
    allowed = can_read_isms(db, user)
    if kind == "risk":
        from app.services.risks import _can_read_specific_risk

        allowed = _can_read_specific_risk(db, user, row)
    if not row or not allowed:
        if required:
            raise DomainError(404, "Related item not available")
        return None
    return row


def _title(kind, row) -> str:
    if kind == "effectiveness_measure":
        return row.summary or row.metric_key or "Effectiveness measure"
    if kind == "meeting":
        return row.title or f"Meeting minutes {row.date}"
    return (
        getattr(row, "title", None)
        or getattr(row, "name", None)
        or getattr(row, "goal", None)
        or getattr(row, "threat_summary", None)
        or kind
    )


def target_summary(kind, row):
    return {
        "target_type": kind,
        "target_id": str(row.id),
        "title": _title(kind, row),
        "url": PAGES[kind].format(id=row.id),
    }


def search_targets(db, user, kind, q, *, limit=100):
    """Readable records of ``kind`` whose name contains ``q``, as summaries."""
    if kind not in TARGETS:
        raise DomainError(400, "Unknown relationship type")
    model = TARGETS[kind]
    field = SEARCH_FIELDS.get(kind, "name")
    qry = db.query(model)
    if q:
        qry = qry.filter(getattr(model, field).ilike(f"%{q[:256]}%"))
    rows = qry.order_by(getattr(model, field), model.id).limit(limit).all()
    return [
        target_summary(kind, row)
        for row in rows
        if resolve_target(db, user, kind, row.id, required=False)
    ]


@dataclass(frozen=True)
class Mirror:
    """Where a relationship is kept when the other record's kind keeps it.

    ``model`` is the other kind's link table, ``column`` names that record in
    it, ``limit`` is how many relationships one of those records supports and
    ``noun`` is what a message calls it.
    """

    model: Any
    column: str
    limit: int
    noun: str


# By the kind a relationship is read from, then the kind it reaches: a SWOT
# item's relationship to a legal register item is the register item's row.
MIRRORS: dict[str, dict[str, Mirror]] = {
    "swot": {
        "legal_item": Mirror(IsmsLegalItemLink, "item_id", 200, "legal register item"),
    },
}


# The kind a link table's owner column names.
OWNER_KINDS = {"swot_id": "swot"}


def _owner(owner: dict) -> tuple[str, object]:
    ((column, record_id),) = owner.items()
    return OWNER_KINDS.get(column, ""), record_id


def _mirrors(owner: dict) -> dict[str, Mirror]:
    return MIRRORS.get(_owner(owner)[0], {})


def _mirror_rows(db, owner: dict, mirror: Mirror):
    kind, record_id = _owner(owner)
    return db.query(mirror.model).filter_by(target_type=kind, target_id=record_id)


def linked_pairs(db, model, owner: dict) -> set:
    """A record's relationships as (type, id) pairs, whichever end keeps them."""
    pairs = {
        (link.target_type, link.target_id)
        for link in db.query(model).filter_by(**owner)
    }
    for target_kind, mirror in _mirrors(owner).items():
        pairs |= {
            (target_kind, getattr(link, mirror.column))
            for link in _mirror_rows(db, owner, mirror)
        }
    return pairs


def add_link(db, model, owner: dict, kind: str, target_id) -> None:
    """Keep one new relationship in the table its pair of kinds uses."""
    mirror = _mirrors(owner).get(kind)
    if mirror is None:
        db.add(model(**owner, target_type=kind, target_id=target_id))
        return
    held = db.query(mirror.model).filter_by(**{mirror.column: target_id}).count()
    if held >= mirror.limit:
        raise DomainError(
            400, f"A {mirror.noun} supports up to {mirror.limit} relationships"
        )
    own_kind, record_id = _owner(owner)
    db.add(
        mirror.model(
            **{mirror.column: target_id}, target_type=own_kind, target_id=record_id
        )
    )


def remove_link(db, model, owner: dict, kind: str, target_id) -> None:
    mirror = _mirrors(owner).get(kind)
    rows = (
        db.query(model).filter_by(**owner, target_type=kind, target_id=target_id)
        if mirror is None
        else _mirror_rows(db, owner, mirror).filter_by(**{mirror.column: target_id})
    )
    rows.delete(synchronize_session=False)


def filter_by_target(qry, link_owner, record_id, kind: str, target_type: str, target):
    """Narrow a register query to the records related to one target.

    ``link_owner`` is the link table's column naming the record and
    ``record_id`` the register's own id column.
    """
    mirror = MIRRORS.get(kind, {}).get(target_type)
    if mirror is None:
        model = link_owner.class_
        return qry.join(model, link_owner == record_id).filter(
            model.target_type == target_type, model.target_id == target
        )
    return qry.join(mirror.model, mirror.model.target_id == record_id).filter(
        mirror.model.target_type == kind,
        getattr(mirror.model, mirror.column) == target,
    )


def forget_record(db, kind: str, record_id) -> None:
    """Remove the relationships other kinds keep to a record being deleted."""
    for mirror in MIRRORS.get(kind, {}).values():
        db.query(mirror.model).filter_by(target_type=kind, target_id=record_id).delete(
            synchronize_session=False
        )


def replace_links(db, user, model, owner: dict, wanted: set, *, limit: int, noun: str):
    """Make a record's relationships ``wanted`` and return those that changed.

    ``model`` is the record's link table and ``owner`` the column and id that
    name the record in it; a relationship the other kind keeps is read and
    written there. A relationship to a record the caller cannot read is
    not theirs to see or to remove, so it stays whatever the save names; the
    limit counts the relationships that stay.
    """
    current = linked_pairs(db, model, owner)
    removed = {
        pair
        for pair in current - wanted
        if resolve_target(db, user, pair[0], pair[1], required=False)
    }
    added = wanted - current
    if len(current) - len(removed) + len(added) > limit:
        raise DomainError(400, f"A {noun} supports up to {limit} relationships")
    for kind, target_id in removed:
        remove_link(db, model, owner, kind, target_id)
    for kind, target_id in sorted(added, key=lambda pair: (pair[0], str(pair[1]))):
        add_link(db, model, owner, kind, target_id)
    db.flush()
    return removed | added
