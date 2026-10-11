"""Control links application operations."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.core.cache import cache_delete_prefix
from app.db.models import (
    ControlItem,
    CrossFrameworkControlLink,
    EffectiveCrossFrameworkControlLink,
    User,
)
from app.services.errors import DomainError


class LinkInput(BaseModel):
    source_control_id: uuid.UUID
    rationale: str = Field(min_length=1, max_length=4000)


def _control(db, cid):
    row = db.get(ControlItem, cid)
    if row is None:
        raise DomainError(404, "Control not found")
    return row


def _out(row):
    source = row.source
    rationale = row.rationale
    if getattr(row, "derived", False):
        shared = sorted(
            set((source.meta or {}).get("osa_nist_controls", []))
            & set((row.target.meta or {}).get("osa_nist_controls", []))
        )
        if shared:
            rationale += " Shared NIST controls: " + ", ".join(shared) + "."
    return {
        "id": str(row.id),
        "source_control_id": str(source.id),
        "source_framework": source.framework_slug,
        "source_ref": source.ref,
        "source_title": source.title,
        "target_control_id": str(row.target_control_id),
        "rationale": rationale,
        "derived": getattr(row, "derived", False),
    }


def list_control_links(*, target_id: uuid.UUID, db: Session):
    _control(db, target_id)
    rows = (
        db.query(EffectiveCrossFrameworkControlLink)
        .options(joinedload(EffectiveCrossFrameworkControlLink.source))
        .filter(EffectiveCrossFrameworkControlLink.target_control_id == target_id)
        .order_by(EffectiveCrossFrameworkControlLink.created_at)
        .all()
    )
    return {"items": [_out(row) for row in rows]}


def create_control_link(
    *, target_id: uuid.UUID, payload: LinkInput, user: User, db: Session
):
    target = _control(db, target_id)
    source = _control(db, payload.source_control_id)
    if source.framework_slug == target.framework_slug:
        raise DomainError(400, "Cross-framework links require different frameworks")
    if (
        db.query(CrossFrameworkControlLink)
        .filter_by(source_control_id=source.id, target_control_id=target.id)
        .first()
    ):
        raise DomainError(409, "This control link already exists")
    row = CrossFrameworkControlLink(
        source_control_id=source.id,
        target_control_id=target.id,
        rationale=payload.rationale.strip(),
        created_by_user_id=user.id,
    )
    if not row.rationale:
        raise DomainError(400, "Explain why the controls overlap")
    db.add(row)
    db.commit()
    db.refresh(row)
    cache_delete_prefix()
    return _out(row)


def remove_control_link(*, target_id: uuid.UUID, link_id: uuid.UUID, user, db: Session):
    row = db.get(CrossFrameworkControlLink, link_id)
    if row is None or row.target_control_id != target_id:
        raise DomainError(404, "Control link not found")
    db.delete(row)
    db.commit()
    cache_delete_prefix()
