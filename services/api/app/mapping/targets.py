"""Find the catalogue rows that evidence mapping targets name."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.db.models import ControlItem
from app.mapping.rules import CLAUSE, CONTROL

log = logging.getLogger(__name__)

Target = tuple[str, str | None]  # (reference, kind)


def kind_of(item: ControlItem) -> str:
    """Return whether a catalogue row is a clause or a control."""
    return CLAUSE if item.type == CLAUSE else CONTROL


def resolve_targets(
    db: Session, framework_slug: str, targets: Iterable[Target]
) -> tuple[dict[Target, ControlItem], list[Target]]:
    """Return the row each (reference, kind) target names, and those naming none.

    A clause target names the framework's clause row with that reference and a
    control target the control. A target without a kind, such as a plain
    ``map_to`` reference, names the control when a clause shares the reference,
    and the clause otherwise. When several controls share a reference, the
    target names none of them and is left out of both results, so evidence
    never attaches to a guess.
    """
    wanted = list(dict.fromkeys((ref, kind) for ref, kind in targets if ref))
    if not wanted:
        return {}, []
    rows = (
        db.query(ControlItem)
        .filter(
            ControlItem.framework_slug == framework_slug,
            ControlItem.ref.in_(sorted({ref for ref, _ in wanted})),
        )
        .all()
    )
    by_ref: dict[str, dict[str, list[ControlItem]]] = {}
    for row in rows:
        by_ref.setdefault(row.ref, {}).setdefault(kind_of(row), []).append(row)
    found: dict[Target, ControlItem] = {}
    absent: list[Target] = []
    for ref, kind in wanted:
        rows_by_kind = by_ref.get(ref, {})
        if kind is None:
            candidates = rows_by_kind.get(CONTROL) or rows_by_kind.get(CLAUSE) or []
        else:
            candidates = rows_by_kind.get(kind, [])
        if len(candidates) == 1:
            found[(ref, kind)] = candidates[0]
        elif not candidates:
            absent.append((ref, kind))
        else:
            log.warning(
                "Mapping target %s / %s names %d controls; no evidence was attached",
                framework_slug,
                ref,
                len(candidates),
            )
    return found, absent
