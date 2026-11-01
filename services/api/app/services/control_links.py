"""Control links application operations."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.cache import cache_delete_prefix
from app.db.models import ControlItem, CrossFrameworkControlLink, User
from app.services.control_inheritance import crosswalk_links
from app.services.errors import DomainError


class LinkInput(BaseModel):
    source_control_id: uuid.UUID
    rationale: str = Field(min_length=1, max_length=4000)


def _control(db, cid):
    row = db.get(ControlItem, cid)
    if row is None:
        raise DomainError(404, "Control not found")
    return row


def _out(link, source, target, *, derived=False):
    rationale = link.rationale
    if derived:
        shared = sorted(
            set((source.meta or {}).get("osa_nist_controls", []))
            & set((target.meta or {}).get("osa_nist_controls", []))
        )
        if shared:
            rationale += " Shared NIST controls: " + ", ".join(shared) + "."
    return {
        "id": str(link.id),
        "source_control_id": str(source.id),
        "source_framework": source.framework_slug,
        "source_ref": source.ref,
        "source_title": source.title,
        "target_control_id": str(link.target_control_id),
        "rationale": rationale,
        "derived": derived,
    }


def list_control_links(*, target_id: uuid.UUID, db: Session):
    """Every link into the control, whatever the framework selection.

    Explicit links come first, oldest first, then shared-reference pairs by
    their source's framework and reference.
    """
    target = _control(db, target_id)
    links = crosswalk_links(db, enabled=None, target_ids=[target_id])
    sources = {
        row.id: row
        for row in db.query(ControlItem).filter(
            ControlItem.id.in_({link.source_control_id for link in links})
        )
    }

    def order(link):
        source = sources[link.source_control_id]
        if link.derived:
            return (1, None, source.framework_slug, source.ref, link.id)
        return (0, link.created_at, "", "", link.id)

    # A control removed meanwhile by another transaction is left out.
    links = [link for link in links if link.source_control_id in sources]
    return {
        "items": [
            _out(link, sources[link.source_control_id], target, derived=link.derived)
            for link in sorted(links, key=order)
        ]
    }


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
    return _out(row, source, target)


def remove_control_link(*, target_id: uuid.UUID, link_id: uuid.UUID, user, db: Session):
    row = db.get(CrossFrameworkControlLink, link_id)
    if row is None or row.target_control_id != target_id:
        raise DomainError(404, "Control link not found")
    db.delete(row)
    db.commit()
    cache_delete_prefix()
