"""Explicit SWOT audit scope; source access is required before sharing."""

from app.db.models import AuditSwotScope, IsmsSwot
from app.services.errors import DomainError
from app.services.isms.swot import _lock, require_access


def scope_items(db, audit_id):
    # Audit access deliberately shares the selected title and quadrant, like
    # existing document scopes. It never exposes SWOT relationships or notes.
    rows = (
        db.query(IsmsSwot)
        .join(AuditSwotScope, AuditSwotScope.swot_id == IsmsSwot.id)
        .filter(AuditSwotScope.audit_id == audit_id)
        .order_by(IsmsSwot.title)
        .limit(200)
        .all()
    )
    return [
        {
            "id": str(row.id),
            "title": row.title,
            "kind": row.kind,
            "url": f"/swot.html?id={row.id}",
        }
        for row in rows
    ]


def set_scope(db, audit, ids, user):
    if ids is None:
        return
    from app.services.features import state

    if ids:
        state(db).require("swot")
    require_access(db, user)
    _lock(db)
    ids = set(ids)
    rows = db.query(IsmsSwot).filter(IsmsSwot.id.in_(ids)).all() if ids else []
    if len(rows) != len(ids):
        raise DomainError(400, "Unknown SWOT scope item")
    db.query(AuditSwotScope).filter_by(audit_id=audit.id).delete(
        synchronize_session=False
    )
    for row in rows:
        db.add(AuditSwotScope(audit_id=audit.id, swot_id=row.id))
