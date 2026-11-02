"""Built-in mapping rules, added to an installation's rules by migrations.

Keen ships rules for the evidence its own collectors produce, mapped to the
Keen Assurance Framework. A migration adds each rule once, beside the rules an
administrator already has: a rule whose ID is already in use is left out, and so
is any target the framework no longer has. From then on a built-in rule is an
ordinary rule that administrators edit, disable or delete; nothing adds it back.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import sqlalchemy as sa

from app.core.datetime_utils import utc_now_naive

FRAMEWORK = "KEEN-AF:1.0"


def targets(*refs: str) -> list[dict[str, str]]:
    return [{"framework": FRAMEWORK, "ref": ref, "kind": "control"} for ref in refs]


def _rules_document(bind) -> tuple[dict[str, Any], list, int] | None:
    row = (
        bind.execute(
            sa.text(
                "SELECT document, version FROM managed_configurations "
                "WHERE name='rules' FOR UPDATE"
            )
        )
        .mappings()
        .first()
    )
    if row is None or not isinstance(row["document"], dict):
        return None
    document = row["document"]
    current = document.get("rules")
    return document, current if isinstance(current, list) else [], row["version"]


def _shipped(bind, rules: list[dict]) -> list[dict]:
    """Each rule as it is added here: with the targets the framework still has."""
    refs = {
        ref
        for (ref,) in bind.execute(
            sa.text(
                "SELECT ref FROM control_items "
                "WHERE framework_slug=:framework AND type <> 'clause'"
            ),
            {"framework": FRAMEWORK},
        )
    }
    out = []
    for rule in rules:
        kept = [target for target in rule["map_to"] if target["ref"] in refs]
        if kept:
            out.append({**rule, "map_to": kept})
    return out


def _store(bind, document: dict[str, Any], version: int) -> None:
    values = {
        "document": json.dumps(document),
        "version": version,
        "at": utc_now_naive(),
    }
    bind.execute(
        sa.text(
            "UPDATE managed_configurations SET document=CAST(:document AS jsonb), "
            "version=:version, updated_by=NULL, updated_at=:at WHERE name='rules'"
        ),
        values,
    )
    bind.execute(
        sa.text(
            "INSERT INTO managed_configuration_revisions "
            "(id,name,version,document,updated_at) "
            "VALUES (:id,'rules',:version,CAST(:document AS jsonb),:at)"
        ),
        {**values, "id": uuid.uuid4()},
    )


def add_rules(bind, rules: list[dict]) -> int:
    """Add the rules not yet present; returns how many were added.

    Nothing changes until the installation has stored rules and the framework.
    """
    found = _rules_document(bind)
    if found is None:
        return 0
    document, current, version = found
    taken = {item.get("id") for item in current if isinstance(item, dict)}
    added = [rule for rule in _shipped(bind, rules) if rule["id"] not in taken]
    if added:
        _store(bind, {**document, "rules": current + added}, version + 1)
    return len(added)


def remove_rules(bind, rules: list[dict]) -> int:
    """Remove the rules still exactly as added; edited ones stay."""
    found = _rules_document(bind)
    if found is None:
        return 0
    document, current, version = found
    shipped = _shipped(bind, rules)
    kept = [item for item in current if item not in shipped]
    if len(kept) != len(current):
        _store(bind, {**document, "rules": kept}, version + 1)
    return len(current) - len(kept)
