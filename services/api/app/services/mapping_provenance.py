"""Which mapping rule made a mapping.

A rule mapping records its rule in the mapping's rationale, as
``auto by rule <rule id> (<framework slug>)``, with ``method`` ``rule`` and
``mapped_by`` ``system``. Manual and imported mappings carry their own
rationale and never count as rule provenance.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import ControlItem, Mapping


def rule_mapping_condition(rule_id: str):
    """SQL predicate: this mapping was made by ``rule_id`` for its control's framework.

    The query must join ``ControlItem`` on ``Mapping.control_item_id``.
    """
    return (
        (Mapping.method == "rule")
        & (Mapping.mapped_by == "system")
        & (
            Mapping.rationale
            == "auto by rule " + rule_id + " (" + ControlItem.framework_slug + ")"
        )
    )


def rule_owned_event_ids(rule_id: str):
    """Events with at least one mapping made by ``rule_id``."""
    return (
        select(Mapping.event_id)
        .join(ControlItem, ControlItem.id == Mapping.control_item_id)
        .where(rule_mapping_condition(rule_id))
    )


def rule_id_of(mapping: Mapping, framework_slug: str) -> str | None:
    """The rule that made ``mapping`` for a control in ``framework_slug``, if any."""
    prefix, suffix = "auto by rule ", f" ({framework_slug})"
    rationale = mapping.rationale or ""
    if (
        mapping.method == "rule"
        and mapping.mapped_by == "system"
        and rationale.startswith(prefix)
        and rationale.endswith(suffix)
    ):
        return rationale[len(prefix) : -len(suffix)]
    return None
