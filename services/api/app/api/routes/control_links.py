"""Administrator approved, directed control relationships across frameworks."""
from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.db.models import ControlItem, CrossFrameworkControlLink, User
from app.db.session import get_db
from app.core.cache import cache_delete_prefix
from app.security.auth import require_admin

router = APIRouter()


class LinkInput(BaseModel):
    source_control_id: uuid.UUID
    rationale: str = Field(min_length=1, max_length=4000)


def _control(db, cid):
    row = db.get(ControlItem, cid)
    if row is None:
        raise HTTPException(404, "Control not found")
    return row


def _out(row):
    source = row.source
    return {
        "id": str(row.id), "source_control_id": str(source.id),
        "source_framework": source.framework_slug, "source_ref": source.ref,
        "source_title": source.title, "target_control_id": str(row.target_control_id),
        "rationale": row.rationale,
    }


@router.get("/v1/controls/{target_id}/cross-framework")
def list_control_links(target_id: uuid.UUID, db: Session = Depends(get_db)):
    _control(db, target_id)
    rows = db.query(CrossFrameworkControlLink).options(joinedload(CrossFrameworkControlLink.source)).filter(
        CrossFrameworkControlLink.target_control_id == target_id
    ).order_by(CrossFrameworkControlLink.created_at).all()
    return {"items": [_out(row) for row in rows]}


@router.post("/v1/controls/{target_id}/cross-framework", status_code=201)
def create_control_link(target_id: uuid.UUID, payload: LinkInput, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = _control(db, target_id)
    source = _control(db, payload.source_control_id)
    if source.framework_slug == target.framework_slug:
        raise HTTPException(400, "Cross-framework links require different frameworks")
    if db.query(CrossFrameworkControlLink).filter_by(source_control_id=source.id, target_control_id=target.id).first():
        raise HTTPException(409, "This control link already exists")
    row = CrossFrameworkControlLink(
        source_control_id=source.id, target_control_id=target.id,
        rationale=payload.rationale.strip(), created_by_user_id=user.id,
    )
    if not row.rationale:
        raise HTTPException(400, "Explain why the controls overlap")
    db.add(row)
    db.commit()
    db.refresh(row)
    cache_delete_prefix()
    return _out(row)


@router.delete("/v1/controls/{target_id}/cross-framework/{link_id}", status_code=204)
def remove_control_link(target_id: uuid.UUID, link_id: uuid.UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    row = db.get(CrossFrameworkControlLink, link_id)
    if row is None or row.target_control_id != target_id:
        raise HTTPException(404, "Control link not found")
    db.delete(row)
    db.commit()
    cache_delete_prefix()
