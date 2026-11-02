"""Owners of controls and clauses: the people answerable for them.

Everyone who can open a control or clause sees its owners; administrators
choose them. Each owner finds what they own under My controls.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.references import ref_sort_key, try_uuid
from app.db.models import ControlItem, ControlOwner, Framework, FrameworkClause, User
from app.services.entity_changelog import (
    clause_changelog_state,
    control_changelog_state,
    record_entity_changelog,
)
from app.services.errors import DomainError
from app.services.features import state

# A control or clause names at most this many owners.
MAX_OWNERS = 20


def owners_of(
    db: Session,
    *,
    control_id: uuid.UUID | None = None,
    clause_id: uuid.UUID | None = None,
) -> list[dict[str, str]]:
    column = ControlOwner.control_item_id if control_id else ControlOwner.clause_id
    rows = (
        db.query(User.id, User.username)
        .join(ControlOwner, ControlOwner.user_id == User.id)
        .filter(column == (control_id or clause_id))
        .order_by(User.username)
    )
    return [{"id": str(uid), "username": name} for uid, name in rows]


def set_owners(*, kind: str, target_id: str, user_ids: list, user: User, db: Session):
    """Replace a control's or clause's owners with active accounts."""
    tid = try_uuid(target_id)
    if tid is None:
        raise DomainError(status_code=400, detail=f"{kind}_id must be a UUID")
    model = ControlItem if kind == "control" else FrameworkClause
    target = db.query(model).filter(model.id == tid).with_for_update().one_or_none()
    if target is None:
        raise DomainError(status_code=404, detail=f"{kind.capitalize()} not found")
    wanted = list(dict.fromkeys(user_ids))
    if len(wanted) > MAX_OWNERS:
        raise DomainError(status_code=400, detail=f"Choose at most {MAX_OWNERS} owners")
    if wanted and db.query(User.id).filter(
        User.id.in_(wanted), User.is_active.is_(True)
    ).count() != len(wanted):
        raise DomainError(status_code=400, detail="Unknown or inactive owner")
    changelog = control_changelog_state if kind == "control" else clause_changelog_state
    before = changelog(db, tid)
    column = (
        ControlOwner.control_item_id if kind == "control" else ControlOwner.clause_id
    )
    db.query(ControlOwner).filter(column == tid).delete(synchronize_session=False)
    for uid in wanted:
        db.add(
            ControlOwner(
                user_id=uid,
                control_item_id=tid if kind == "control" else None,
                clause_id=tid if kind == "clause" else None,
            )
        )
    db.flush()
    record_entity_changelog(
        db,
        entity_type=kind,
        entity_id=tid,
        action="updated",
        before=before,
        after=changelog(db, tid),
        user=user,
        request_method="PUT",
        request_path=f"/v1/admin/{kind}s/{tid}/owners",
    )
    db.commit()
    key = {"control_id": tid} if kind == "control" else {"clause_id": tid}
    return {"owners": owners_of(db, **key)}


def my_controls(*, user: User, db: Session) -> dict[str, Any]:
    """The controls and clauses the signed-in person owns, by framework."""
    features = state(db)
    names: dict[str, str | None] = {
        slug: name for slug, name in db.query(Framework.slug, Framework.name)
    }
    items: list[dict[str, Any]] = []
    if features.enabled("controls"):
        for control in (
            db.query(ControlItem)
            .join(ControlOwner, ControlOwner.control_item_id == ControlItem.id)
            .filter(ControlOwner.user_id == user.id)
        ):
            items.append(
                {
                    "kind": "control",
                    "id": str(control.id),
                    "framework": control.framework_slug,
                    "ref": control.ref,
                    "title": control.title,
                    "in_scope": control.in_scope,
                }
            )
    if features.enabled("clauses"):
        for clause in (
            db.query(FrameworkClause)
            .join(ControlOwner, ControlOwner.clause_id == FrameworkClause.id)
            .filter(ControlOwner.user_id == user.id)
        ):
            items.append(
                {
                    "kind": "clause",
                    "id": str(clause.id),
                    "framework": clause.framework_slug,
                    "ref": clause.ref,
                    "title": clause.title,
                    "in_scope": None,
                }
            )
    for item in items:
        item["framework_name"] = names.get(item["framework"]) or item["framework"]
    items.sort(
        key=lambda i: (i["framework_name"], i["kind"], ref_sort_key(i["ref"] or ""))
    )
    return {"items": items}
