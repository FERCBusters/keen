"""Records other features refer to, resolved with the reader's own access.

SWOT items and stories relate to objectives, documents, assets, people,
vendors and risks. A reference is resolved only when the feature is enabled
and the reader can read the record itself, so a relationship never shows more
than the record's own page would.
"""

from __future__ import annotations

from app.db.models import (
    IsmsDocument,
    IsmsObjective,
    IsmsPerson,
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
}
SEARCH_FIELDS = {"objective": "goal", "document": "title", "risk": "threat_summary"}


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


def target_summary(kind, row):
    title = (
        getattr(row, "title", None)
        or getattr(row, "name", None)
        or getattr(row, "goal", None)
        or getattr(row, "threat_summary", None)
        or kind
    )
    page = {
        "objective": "isms-objective",
        "document": "isms-document",
        "asset": "isms-asset",
        "risk": "risk",
        "person": "assurance",
        "vendor": "assurance",
    }[kind]
    suffix = (
        f"?tab={kind}s&edit={row.id}"
        if kind in ("person", "vendor")
        else f"?id={row.id}"
    )
    return {
        "target_type": kind,
        "target_id": str(row.id),
        "title": title,
        "url": f"/{page}.html{suffix}",
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
