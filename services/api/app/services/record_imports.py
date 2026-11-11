"""Add many records in one transaction, keeping them all or none.

An import checks every row through the same code that saves one record, so a
row is accepted exactly when a save of that record would be. Each row runs in
its own savepoint: a refused row is reported and the others are still checked.
The rows are kept only when every one is accepted and the import is no dry run;
otherwise the transaction is rolled back and nothing is written, the diary
entries of linked records included.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.errors import DomainError
from app.services.isms.link_diary import (
    deferred_link_diary,
    write_deferred_link_diary,
)

# The most rows one import adds; larger sets are split by the caller.
IMPORT_ROWS = 500
M = TypeVar("M", bound=BaseModel)


def row_payload(
    model: type[M],
    row: dict[str, Any],
    allowed: Iterable[str],
    fixed: dict[str, Any] | None = None,
) -> M:
    """``row`` as ``model``, refusing fields an import does not set; ``fixed``
    sets values the whole import shares, such as its framework."""
    allowed = set(allowed)
    unknown = sorted(set(row) - allowed)
    if unknown:
        raise DomainError(
            400,
            f"Unknown field{'s' if len(unknown) > 1 else ''}: "
            + ", ".join(unknown[:10])
            + f". Fields an import sets: {', '.join(sorted(allowed))}",
        )
    return model.model_validate({**row, **(fixed or {})})


def _problem(error: Exception) -> str:
    if isinstance(error, DomainError):
        return str(error.detail)
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'row'}: {item['msg']}"
            for item in error.errors()[:5]
        )
    # An IntegrityError: a uniqueness or reference rule refused the row.
    return "The row conflicts with a record Keen already holds"


def import_rows(
    db: Session,
    rows: list[dict[str, Any]],
    add: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Add each row with ``add``, which saves one record without committing and
    returns a short description of it; keep them all or none.

    Returns each row's outcome, how many were accepted and, unless this was a
    dry run or a row was refused, how many were created.
    """
    outcomes: list[dict[str, Any]] = []
    with deferred_link_diary(db) as pending:
        for number, row in enumerate(rows, start=1):
            written = len(pending)
            try:
                with db.begin_nested():
                    described = add(row)
                    db.flush()
            except (DomainError, ValidationError, IntegrityError) as error:
                # The row's savepoint is gone, and with it its diary writes.
                del pending[written:]
                outcomes.append({"row": number, "ok": False, "error": _problem(error)})
                continue
            outcomes.append({"row": number, "ok": True, **described})
    refused = sum(1 for outcome in outcomes if not outcome["ok"])
    result = {
        "dry_run": dry_run,
        "accepted": len(rows) - refused,
        "refused": refused,
        "created": 0,
        "rows": outcomes,
    }
    if dry_run or refused:
        db.rollback()
        return result
    write_deferred_link_diary(db, pending)
    db.commit()
    result["created"] = len(rows)
    return result
