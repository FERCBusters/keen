"""Records other features refer to, resolved with the reader's own access.

SWOT items relate to objectives, documents, assets, people, vendors and risks.
Stories relate to those records and also to SWOT items, effectiveness measures
and meeting minutes. A reference is resolved only when the feature is enabled
and the reader can read the record itself, so a relationship never shows more
than the record's own page would.
"""

from __future__ import annotations

from app.db.models import (
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsMeeting,
    IsmsObjective,
    IsmsPerson,
    IsmsSwot,
    IsmsVendor,
    Risk,
    RiskAsset,
)
from app.services.errors import DomainError

from .access import _can_read_isms

TARGETS = {
    "objective": IsmsObjective,
    "document": IsmsDocument,
    "asset": RiskAsset,
    "person": IsmsPerson,
    "vendor": IsmsVendor,
    "risk": Risk,
    "swot": IsmsSwot,
    "effectiveness_measure": IsmsEffectivenessMeasure,
    "meeting": IsmsMeeting,
}
SEARCH_FIELDS = {
    "objective": "goal",
    "document": "title",
    "risk": "threat_summary",
    "swot": "title",
    "effectiveness_measure": "summary",
    "meeting": "title",
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
    allowed = _can_read_isms(db, user)
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
