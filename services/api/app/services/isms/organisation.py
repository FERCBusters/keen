"""ISMS organisation domain operations."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import IsmsOrgNode, IsmsOrgNodeUser, Risk, User
from app.security.permissions import has_permission
from app.services.context import ChangeContext
from app.services.errors import DomainError
from app.services.hierarchy import validate_parent

from .common import _by_id_or_404, _clean_framework, _clean_text, _record, _utcnow
from .mutations import _apply_links, _delete_entity_links, _replace_org_node_users
from .schemas import OrgNodePayload
from .serializers import _entity_out

RISK_MANAGE_PERMISSION = "risk.manage"


def _require_risk_authority_for_owner_role(
    db: Session, user: User, node: IsmsOrgNode, action: str
) -> None:
    """Membership of a risk-owning role grants read access to those risks.

    Organisation chart management therefore cannot change who belongs to, or
    remove, such a role without the authority to manage risks.
    """
    owns_risks = (
        db.query(Risk.id).filter(Risk.risk_owner_role_id == node.id).first() is not None
    )
    if owns_risks and not has_permission(db, user, RISK_MANAGE_PERMISSION):
        raise DomainError(
            status_code=403,
            detail=f"risk.manage permission is required to {action} a role that owns risks",
        )


def create_org_node(
    *,
    payload: OrgNodePayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = _clean_framework(framework)
    validate_parent(db, IsmsOrgNode, payload.parent_id)
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
    _record(db, "org_node", row, "created", None, after, user, context)
    db.commit()
    db.refresh(row)
    return _entity_out(db, "org_node", row, fw)


def list_org_nodes(*, framework: str, user: User, db: Session):
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


def update_org_node(
    *,
    node_id: str,
    payload: OrgNodePayload,
    context: ChangeContext,
    framework: str,
    user: User,
    db: Session,
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsOrgNode, node_id, "Organisation chart node")
    before = _entity_out(db, "org_node", row, fw)
    fields = set(payload.model_fields_set or set())
    if "parent_id" in fields:
        validate_parent(db, IsmsOrgNode, payload.parent_id, row.id)
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
        current = {
            uid
            for (uid,) in db.query(IsmsOrgNodeUser.user_id).filter(
                IsmsOrgNodeUser.org_node_id == row.id
            )
        }
        if current != set(payload.user_ids or []):
            _require_risk_authority_for_owner_role(
                db, user, row, "change the members of"
            )
        _replace_org_node_users(
            db, row, payload.user_ids or [], payload.relationship_type
        )
    _apply_links(db, "org_node", row.id, fw, payload, user)
    after = _entity_out(db, "org_node", row, fw)
    _record(db, "org_node", row, "updated", before, after, user, context)
    db.commit()
    db.refresh(row)
    return _entity_out(db, "org_node", row, fw)


def delete_org_node(
    *, node_id: str, context: ChangeContext, framework: str, user: User, db: Session
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsOrgNode, node_id, "Organisation chart node")
    _require_risk_authority_for_owner_role(db, user, row, "delete")
    before = _entity_out(db, "org_node", row, fw)
    _record(db, "org_node", row, "deleted", before, None, user, context)
    _delete_entity_links(db, "org_node", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}
