"""Validate parent changes under a transaction-scoped hierarchy lock."""

from fastapi import HTTPException
from sqlalchemy import text

from app.db.models import IsmsDocumentFolder, IsmsOrgNode

_LOCK_KEYS = {IsmsOrgNode: 75431001, IsmsDocumentFolder: 75431002}


def validate_parent(db, model, parent_id, node_id=None):
    # Serialise reparenting so two concurrent edits cannot jointly form a cycle.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEYS[model]}
        )
    seen = {node_id} if node_id else set()
    current = parent_id
    while current is not None:
        if current in seen:
            raise HTTPException(400, "Parent relationship would contain a cycle")
        seen.add(current)
        # Select columns to avoid stale ORM relationships after waiting on the lock.
        row = db.query(model.parent_id).filter(model.id == current).first()
        if row is None:
            raise HTTPException(400, "Unknown parent")
        current = row[0]
