from __future__ import annotations

from typing import Any

from app.db.models import (
    ControlItem,
    FrameworkClause,
    IsmsDocument,
)


def control_out(c: ControlItem) -> dict[str, Any]:
    return {"id": str(c.id), "ref": c.ref, "title": c.title, "type": c.type}


def clause_out(c: FrameworkClause) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "ref": c.ref,
        "title": c.title,
        "parent_id": str(c.parent_clause_id) if c.parent_clause_id else None,
        "parent_ref": c.parent.ref if getattr(c, "parent", None) else None,
    }


def document_out(d: IsmsDocument) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "title": d.title,
        "document_type": d.document_type,
        "external_url": d.external_url,
        "filename": d.filename,
    }
