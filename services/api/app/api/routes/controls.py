from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc
from sqlalchemy.orm import Session, load_only

from app.api.utils import (
    control_justification as _control_justification,
    control_upstream_url as _control_upstream_url,
    extract_source_url as _extract_source_url,
    ref_sort_key as _ref_sort_key,
    try_uuid as _try_uuid,
)
from app.core.config import settings
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    Event,
    FrameworkClause,
    Mapping,
    User,
)
from app.db.session import get_db
from app.security.diary_visibility import diary_filter_condition
from app.security.permissions import has_permission
from app.security.roles import is_effective_admin
from app.security.redaction import redact_obj as _redact_obj, redact_str as _redact_str
from app.services.entity_changelog import list_entity_changelogs

router = APIRouter()

EVENTS_READ_PERMISSION = "events.read"


def _require_events_read(db: Session, request: Request) -> User:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not is_effective_admin(db, user) and not has_permission(
        db, user, EVENTS_READ_PERMISSION
    ):
        raise HTTPException(status_code=403, detail="events.read permission required")
    return user


def _clause_link_out(link: ControlClauseLink) -> dict:
    c = link.clause
    return {
        "clause_id": str(c.id),
        "ref": c.ref,
        "title": c.title,
        "parent_id": str(c.parent_clause_id) if c.parent_clause_id else None,
        "parent_ref": c.parent.ref if getattr(c, "parent", None) else None,
        "applicability": link.applicability,
        "upstream_url": _control_upstream_url(c),
    }


@router.get("/v1/controls")
def list_controls(
    framework: str = settings.default_framework_slug,
    limit: int = 500,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    limit = max(1, min(int(limit or 500), 5000))
    offset = max(0, int(offset or 0))
    rows = db.query(ControlItem).filter(ControlItem.framework_slug == framework).all()
    items = [
        {
            "id": str(c.id),
            "framework": c.framework_slug,
            "type": c.type,
            "ref": c.ref,
            "title": c.title,
            "in_scope": c.in_scope,
            "justification": _control_justification(c),
            "upstream_url": _control_upstream_url(c),
        }
        for c in rows
    ]
    items.sort(key=lambda it: (it["type"], _ref_sort_key(it["ref"])))
    total = len(items)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items[offset : offset + limit],
    }


@router.get("/v1/controls/{control_id}")
def get_control(control_id: str, db: Session = Depends(get_db)):
    cid = _try_uuid(control_id)
    if not cid:
        raise HTTPException(status_code=400, detail="control_id must be a UUID")
    c = db.query(ControlItem).filter(ControlItem.id == cid).one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Control not found")
    clause_links = (
        db.query(ControlClauseLink)
        .join(FrameworkClause, FrameworkClause.id == ControlClauseLink.clause_id)
        .filter(
            ControlClauseLink.control_item_id == c.id,
            FrameworkClause.framework_slug == c.framework_slug,
        )
        .all()
    )
    clauses = [_clause_link_out(x) for x in clause_links]
    clauses.sort(
        key=lambda it: (
            _ref_sort_key(it.get("parent_ref") or it["ref"]),
            _ref_sort_key(it["ref"]),
        )
    )

    return {
        "id": str(c.id),
        "framework": c.framework_slug,
        "type": c.type,
        "ref": c.ref,
        "title": c.title,
        "in_scope": c.in_scope,
        "justification": _control_justification(c),
        "upstream_url": _control_upstream_url(c),
        "tags": c.tags,
        "metadata": c.meta,
        "clauses": clauses,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/v1/controls/{control_id}/changelog")
def control_changelog(
    control_id: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    cid = _try_uuid(control_id)
    if not cid:
        raise HTTPException(status_code=400, detail="control_id must be a UUID")
    exists = db.query(ControlItem.id).filter(ControlItem.id == cid).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Control not found")
    return list_entity_changelogs(
        db, entity_type="control", entity_id=cid, limit=limit, offset=offset
    )


@router.get("/v1/controls/{control_id}/evidence")
def control_evidence(
    request: Request,
    control_id: str,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    cid = _try_uuid(control_id)
    if not cid:
        raise HTTPException(status_code=400, detail="control_id must be a UUID")

    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))
    user = _require_events_read(db, request)

    base_q = db.query(Mapping).filter(Mapping.control_item_id == cid)
    total = int(base_q.order_by(None).count() or 0)
    q = base_q.order_by(desc(Mapping.mapped_at)).offset(offset).limit(limit).all()
    event_ids = [m.event_id for m in q]

    events = {
        e.id: e
        for e in db.query(Event)
        .options(
            load_only(
                Event.id,
                Event.timestamp,
                Event.source,
                Event.summary,
                Event.raw_pointer,
                Event.normalized_payload,
            )
        )
        .filter(Event.id.in_(event_ids))
        .filter(diary_filter_condition(db, user))
        .all()
    }

    items: list[dict[str, Any]] = []
    for m in q:
        e = events.get(m.event_id)
        if not e:
            continue
        src_url = _redact_str(_extract_source_url(e.raw_pointer, e.normalized_payload))
        items.append(
            {
                "event_id": str(e.id),
                "timestamp": e.timestamp.isoformat(),
                "source": e.source,
                "summary": e.summary,
                "source_url": src_url,
                "raw_pointer": _redact_obj(e.raw_pointer),
                "confidence": m.confidence,
                "method": m.method,
                "rationale": m.rationale,
            }
        )
    return {"total": total, "limit": limit, "offset": offset, "items": items}
