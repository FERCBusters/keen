"""ISMS organisation; see docs/maintainability-review.md for module boundaries."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import IsmsOrgNode
from app.db.session import get_db

from .access import (
    require_isms_manage,
    require_isms_read,
)
from .common import (
    _by_id_or_404,
    _clean_framework,
    _clean_text,
    _record,
    _utcnow,
)
from .mutations import (
    _apply_links,
    _delete_entity_links,
    _replace_org_node_users,
)
from .schemas import (
    OrgNodePayload,
)
from .serializers import (
    _entity_out,
)

router = APIRouter()


@router.post("/v1/isms/org-nodes")
def create_org_node(
    payload: OrgNodePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    if (
        payload.parent_id
        and not db.query(IsmsOrgNode.id)
        .filter(IsmsOrgNode.id == payload.parent_id)
        .first()
    ):
        raise HTTPException(status_code=400, detail="Unknown parent org node")
    row = IsmsOrgNode(
        parent_id=payload.parent_id,
        name=_clean_text(payload.name, max_len=256, required=True, label="name"),
        node_type=_clean_text(
            payload.node_type or "role", max_len=64, required=True, label="node_type"
        ),
        description=_clean_text(payload.description, max_len=20000),
        sort_order=int(payload.sort_order or 0),
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    _replace_org_node_users(db, row, payload.user_ids or [], payload.relationship_type)
    _apply_links(db, "org_node", row.id, fw, payload, user)
    after = _entity_out(db, "org_node", row, fw)
    _record(db, "org_node", row, "created", None, after, user, request)
    db.commit()
    db.refresh(row)
    return _entity_out(db, "org_node", row, fw)


@router.get("/v1/isms/org-nodes")
def list_org_nodes(
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    rows = (
        db.query(IsmsOrgNode)
        .order_by(IsmsOrgNode.sort_order.asc(), IsmsOrgNode.name.asc())
        .all()
    )
    return {
        "framework": fw,
        "items": [_entity_out(db, "org_node", r, fw) for r in rows],
    }


@router.patch("/v1/isms/org-nodes/{node_id}")
def update_org_node(
    node_id: str,
    payload: OrgNodePayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsOrgNode, node_id, "Organisation chart node")
    before = _entity_out(db, "org_node", row, fw)
    fields = set(payload.model_fields_set or set())
    if "parent_id" in fields:
        row.parent_id = payload.parent_id
    if "name" in fields:
        row.name = _clean_text(payload.name, max_len=256, required=True, label="name")
    if "node_type" in fields:
        row.node_type = _clean_text(
            payload.node_type, max_len=64, required=True, label="node_type"
        )
    if "description" in fields:
        row.description = _clean_text(payload.description, max_len=20000)
    if "sort_order" in fields:
        row.sort_order = int(payload.sort_order or 0)
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    if "user_ids" in fields:
        _replace_org_node_users(
            db, row, payload.user_ids or [], payload.relationship_type
        )
    _apply_links(db, "org_node", row.id, fw, payload, user)
    after = _entity_out(db, "org_node", row, fw)
    _record(db, "org_node", row, "updated", before, after, user, request)
    db.commit()
    db.refresh(row)
    return _entity_out(db, "org_node", row, fw)


@router.delete("/v1/isms/org-nodes/{node_id}")
def delete_org_node(
    node_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsOrgNode, node_id, "Organisation chart node")
    before = _entity_out(db, "org_node", row, fw)
    _record(db, "org_node", row, "deleted", before, None, user, request)
    _delete_entity_links(db, "org_node", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}
