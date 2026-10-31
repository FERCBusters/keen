"""Resolve the controls that callers name by id or by reference."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.references import (
    control_justification,
    control_upstream_url,
    try_uuid,
)
from app.db.models import ControlItem, FrameworkClause
from app.services.errors import DomainError

# Framework clauses are also kept in the control catalogue as rows of this
# type, alongside their first-class FrameworkClause rows. A caller that names a
# control by reference means a control, so reference lookups skip these rows.
CLAUSE_ROW_TYPE = "clause"


def control_by_ref(
    db: Session, framework: str, ref: str | None, *, ignore_case: bool = False
) -> ControlItem | None:
    """Return the control with this reference in the framework, or None.

    Clause rows that share the reference are skipped. When the reference still
    names more than one control, the request is refused so that the caller can
    send the control's id. With ``ignore_case`` an exact match wins over
    matches that differ only in case.
    """
    value = (ref or "").strip()
    if not value:
        return None
    query = db.query(ControlItem).filter(
        ControlItem.framework_slug == framework,
        ControlItem.type != CLAUSE_ROW_TYPE,
    )
    if ignore_case:
        rows = query.filter(func.lower(ControlItem.ref) == value.lower()).all()
        exact = [row for row in rows if row.ref == value]
        if len(rows) > 1 and len(exact) == 1:
            return exact[0]
    else:
        rows = query.filter(ControlItem.ref == value).all()
    if len(rows) > 1:
        raise DomainError(
            status_code=400,
            detail=(
                f"More than one control in {framework} has the reference {value}; "
                "use the control id"
            ),
        )
    return rows[0] if rows else None


def control_by_id_or_ref(
    db: Session, framework: str, value: str | None, *, ignore_case: bool = False
) -> ControlItem | None:
    """Return the control named by UUID or by reference in the framework.

    A UUID names exactly one catalogue row and is used as given; references are
    resolved by :func:`control_by_ref`.
    """
    text = (value or "").strip()
    cid = try_uuid(text)
    if cid:
        return (
            db.query(ControlItem)
            .filter(ControlItem.framework_slug == framework, ControlItem.id == cid)
            .one_or_none()
        )
    return control_by_ref(db, framework, text, ignore_case=ignore_case)


def control_summary(control: ControlItem | None) -> dict[str, Any]:
    """Return the control fields shown wherever an entity lists its controls."""
    if not control:
        return {}
    return {
        "id": str(control.id),
        "framework": control.framework_slug,
        "type": control.type,
        "ref": control.ref,
        "title": control.title,
        "in_scope": control.in_scope,
        "justification": control_justification(control),
        "upstream_url": control_upstream_url(control),
    }


def clause_summary(clause: FrameworkClause | None) -> dict[str, Any]:
    """Return the clause fields shown wherever an entity lists its clauses."""
    if not clause:
        return {}
    return {
        "id": str(clause.id),
        "framework": clause.framework_slug,
        "ref": clause.ref,
        "title": clause.title,
        "parent_id": str(clause.parent_clause_id) if clause.parent_clause_id else None,
        "parent_ref": clause.parent.ref if clause.parent else None,
        "sort_order": clause.sort_order,
    }
