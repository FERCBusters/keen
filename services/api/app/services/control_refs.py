"""Resolve the controls that callers name by id or by reference."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.references import try_uuid
from app.db.models import ControlItem
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
