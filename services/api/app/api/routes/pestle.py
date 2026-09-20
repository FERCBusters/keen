from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.api.utils import control_justification as _control_justification
from app.api.utils import control_upstream_url as _control_upstream_url
from app.api.utils import ref_sort_key as _ref_sort_key
from app.api.utils import try_uuid as _try_uuid
from app.core.config import settings
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    FrameworkClause,
    PestleBusinessProcess,
    PestleBusinessProcessRelevance,
    PestleClauseRelevance,
    PestleItem,
    PestleRelevanceLevel,
    User,
)
from app.db.session import get_db
from app.security.auth import require_authenticated
from app.security.permissions import has_permission
from app.services.entity_changelog import (
    list_entity_changelogs,
    pestle_business_process_changelog_state,
    pestle_item_changelog_state,
    record_entity_changelog,
)

router = APIRouter()

PESTLE_READ_PERMISSION = "pestle.read"
PESTLE_MANAGE_PERMISSION = "pestle.manage"
RISK_READ_PERMISSION = "risk.read"
RISK_MANAGE_PERMISSION = "risk.manage"

PESTLE_TYPES = [
    "Political",
    "Economical",
    "Social",
    "Technological",
    "Legal",
    "Environmental",
    "Ethical",
]
PESTLE_LENSES = ["Internal", "External"]


def _split_filter_values(value: str | None) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


_RELEVANCE_LEVELS = [
    ("na", "N/A", 0),
    ("high", "1 High", 1),
    ("medium", "2 Medium", 2),
    ("low", "3 Low", 3),
]
_RELEVANCE_CODE_ALIASES = {
    "": "na",
    "n/a": "na",
    "na": "na",
    "none": "na",
    "0": "na",
    "1": "high",
    "high": "high",
    "1 high": "high",
    "2": "medium",
    "medium": "medium",
    "2 medium": "medium",
    "3": "low",
    "low": "low",
    "3 low": "low",
}


class PestleItemPayload(BaseModel):
    framework: str | None = Field(default=None, max_length=64)
    type: str = Field(..., min_length=1, max_length=32)
    lens: str = Field(..., min_length=1, max_length=16)
    item: str = Field(..., min_length=1, max_length=20000)
    overall_relevance_id: uuid.UUID | None = None
    overall_relevance_code: str | None = Field(default="na", max_length=32)
    rationale: str | None = Field(default="", max_length=20000)


class BusinessProcessPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)


class RelevanceLinkPayload(BaseModel):
    business_process_id: uuid.UUID | None = None
    clause_id: uuid.UUID | None = None
    relevance_id: uuid.UUID | None = None
    relevance_code: str | None = Field(default="na", max_length=32)


class BusinessProcessLinksPayload(BaseModel):
    items: list[RelevanceLinkPayload] = Field(default_factory=list)


class ClauseLinksPayload(BaseModel):
    items: list[RelevanceLinkPayload] = Field(default_factory=list)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _can_read_pestle(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return bool(
        has_permission(db, user, PESTLE_MANAGE_PERMISSION)
        or has_permission(db, user, PESTLE_READ_PERMISSION)
        # Backwards-compatible: existing risk viewers can see the new assessment area
        # until deployments have explicitly assigned the new PESTLE(E) permissions.
        or has_permission(db, user, RISK_MANAGE_PERMISSION)
        or has_permission(db, user, RISK_READ_PERMISSION)
    )


def _can_manage_pestle(db: Session, user: User | None) -> bool:
    if not isinstance(user, User) or not getattr(user, "is_active", False):
        return False
    return bool(
        has_permission(db, user, PESTLE_MANAGE_PERMISSION)
        or has_permission(db, user, RISK_MANAGE_PERMISSION)
    )


def require_pestle_read(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not _can_read_pestle(db, user):
        raise HTTPException(status_code=403, detail="pestle.read permission required")
    return user


def require_pestle_manage(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_authenticated(request)
    if not _can_manage_pestle(db, user):
        raise HTTPException(status_code=403, detail="pestle.manage permission required")
    return user


def _clean_text(
    raw: str | None, *, max_len: int, required: bool = False, label: str = "value"
) -> str:
    value = (raw or "").strip()
    if required and not value:
        raise HTTPException(status_code=400, detail=f"{label} is required")
    if len(value) > max_len:
        raise HTTPException(status_code=400, detail=f"{label} is too long")
    return value


def _clean_framework(raw: str | None) -> str:
    value = (raw or settings.default_framework_slug or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="framework is required")
    if len(value) > 64 or not re.match(r"^[A-Za-z0-9._:-]+$", value):
        raise HTTPException(status_code=400, detail="Invalid framework")
    return value


def _clean_type(raw: str | None) -> str:
    value = _clean_text(raw, max_len=32, required=True, label="type")
    match = {x.lower(): x for x in PESTLE_TYPES}.get(value.lower())
    if not match:
        raise HTTPException(
            status_code=400, detail="type must be a valid PESTLE(E) type"
        )
    return match


def _clean_lens(raw: str | None) -> str:
    value = _clean_text(raw, max_len=16, required=True, label="lens")
    match = {x.lower(): x for x in PESTLE_LENSES}.get(value.lower())
    if not match:
        raise HTTPException(status_code=400, detail="lens must be Internal or External")
    return match


def _normalise_relevance_code(raw: str | None) -> str:
    value = (raw or "na").strip().lower()
    return _RELEVANCE_CODE_ALIASES.get(value, value)


def _seed_relevance_levels(db: Session) -> None:
    existing = {code for (code,) in db.query(PestleRelevanceLevel.code).all()}
    now = _utcnow()
    for code, label, order in _RELEVANCE_LEVELS:
        if code in existing:
            continue
        db.add(
            PestleRelevanceLevel(
                code=code,
                label=label,
                sort_order=order,
                created_at=now,
            )
        )
    db.flush()


def _relevance_levels(db: Session) -> list[PestleRelevanceLevel]:
    _seed_relevance_levels(db)
    return (
        db.query(PestleRelevanceLevel)
        .order_by(PestleRelevanceLevel.sort_order.asc())
        .all()
    )


def _relevance_by_id_or_code(
    db: Session,
    *,
    relevance_id: uuid.UUID | None = None,
    relevance_code: str | None = None,
) -> PestleRelevanceLevel:
    _seed_relevance_levels(db)
    if relevance_id:
        row = (
            db.query(PestleRelevanceLevel)
            .filter(PestleRelevanceLevel.id == relevance_id)
            .one_or_none()
        )
        if row:
            return row
        raise HTTPException(status_code=400, detail="Unknown relevance_id")
    code = _normalise_relevance_code(relevance_code)
    row = (
        db.query(PestleRelevanceLevel)
        .filter(PestleRelevanceLevel.code == code)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=400, detail="Unknown relevance_code")
    return row


def _default_relevance(db: Session) -> PestleRelevanceLevel:
    return _relevance_by_id_or_code(db, relevance_code="na")


def _relevance_out(row: PestleRelevanceLevel | None) -> dict[str, Any]:
    return {
        "id": str(row.id) if row else None,
        "code": row.code if row else "na",
        "label": row.label if row else "N/A",
        "sort_order": int(row.sort_order or 0) if row else 0,
    }


def _business_process_out(row: PestleBusinessProcess) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "created_by": getattr(row.created_by, "username", None),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _clause_summary(row: FrameworkClause) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "framework": row.framework_slug,
        "ref": row.ref,
        "title": row.title,
        "parent_id": str(row.parent_clause_id) if row.parent_clause_id else None,
        "parent_ref": row.parent.ref if getattr(row, "parent", None) else None,
        "sort_order": int(row.sort_order or 0),
    }


def _control_summary(row: ControlItem) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "framework": row.framework_slug,
        "type": row.type,
        "ref": row.ref,
        "title": row.title,
        "in_scope": row.in_scope,
        "justification": _control_justification(row),
        "upstream_url": _control_upstream_url(row),
    }


def _related_controls_for_item(
    db: Session, item_id: uuid.UUID, framework: str
) -> list[ControlItem]:
    rows = (
        db.query(ControlItem)
        .join(ControlClauseLink, ControlClauseLink.control_item_id == ControlItem.id)
        .join(
            PestleClauseRelevance,
            PestleClauseRelevance.clause_id == ControlClauseLink.clause_id,
        )
        .join(
            PestleRelevanceLevel,
            PestleRelevanceLevel.id == PestleClauseRelevance.relevance_id,
        )
        .filter(
            PestleClauseRelevance.pestle_item_id == item_id,
            ControlItem.framework_slug == framework,
            PestleRelevanceLevel.code != "na",
        )
        .distinct()
        .all()
    )
    rows.sort(key=lambda c: (c.type, _ref_sort_key(c.ref)))
    return rows


def _item_out(
    db: Session,
    row: PestleItem,
    *,
    include_links: bool = False,
) -> dict[str, Any]:
    business_links = [
        link
        for link in list(row.business_process_links or [])
        if getattr(getattr(link, "relevance", None), "code", None) != "na"
    ]
    clause_links = [
        link
        for link in list(row.clause_links or [])
        if getattr(getattr(link, "relevance", None), "code", None) != "na"
    ]
    out: dict[str, Any] = {
        "id": str(row.id),
        "framework": row.framework_slug,
        "type": row.type,
        "lens": row.lens,
        "item": row.item or "",
        "overall_relevance": _relevance_out(row.overall_relevance),
        "overall_relevance_id": (
            str(row.overall_relevance_id) if row.overall_relevance_id else None
        ),
        "rationale": row.rationale or "",
        "created_by": getattr(row.created_by, "username", None),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "business_process_count": len(business_links),
        "clause_count": len(clause_links),
    }
    if include_links:
        out["business_processes"] = [
            {
                "business_process": _business_process_out(link.business_process),
                "relevance": _relevance_out(link.relevance),
                "relevance_id": str(link.relevance_id),
                "updated_at": link.updated_at.isoformat() if link.updated_at else None,
            }
            for link in sorted(
                business_links,
                key=lambda x: (
                    (x.business_process.name or "").lower(),
                    x.business_process.name or "",
                ),
            )
        ]
        out["clauses"] = [
            {
                "clause": _clause_summary(link.clause),
                "relevance": _relevance_out(link.relevance),
                "relevance_id": str(link.relevance_id),
                "updated_at": link.updated_at.isoformat() if link.updated_at else None,
            }
            for link in sorted(
                clause_links,
                key=lambda x: (
                    int(x.clause.sort_order or 0),
                    _ref_sort_key(x.clause.ref),
                ),
            )
        ]
        out["related_controls"] = [
            _control_summary(c)
            for c in _related_controls_for_item(db, row.id, row.framework_slug)
        ]
    return out


def _item_or_404(db: Session, item_id: str | uuid.UUID) -> PestleItem:
    iid = item_id if isinstance(item_id, uuid.UUID) else _try_uuid(str(item_id))
    if not iid:
        raise HTTPException(status_code=400, detail="pestle item id must be a UUID")
    row = db.query(PestleItem).filter(PestleItem.id == iid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="PESTLE(E) item not found")
    return row


def _business_process_or_404(
    db: Session, process_id: str | uuid.UUID
) -> PestleBusinessProcess:
    pid = (
        process_id if isinstance(process_id, uuid.UUID) else _try_uuid(str(process_id))
    )
    if not pid:
        raise HTTPException(
            status_code=400, detail="business_process_id must be a UUID"
        )
    row = (
        db.query(PestleBusinessProcess)
        .filter(PestleBusinessProcess.id == pid)
        .one_or_none()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Business process not found")
    return row


def _clause_or_404(
    db: Session, clause_id: str | uuid.UUID, framework: str | None = None
) -> FrameworkClause:
    cid = clause_id if isinstance(clause_id, uuid.UUID) else _try_uuid(str(clause_id))
    if not cid:
        raise HTTPException(status_code=400, detail="clause_id must be a UUID")
    q = db.query(FrameworkClause).filter(FrameworkClause.id == cid)
    if framework:
        q = q.filter(FrameworkClause.framework_slug == framework)
    row = q.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Clause not found")
    return row


def _apply_item_payload(
    db: Session, item: PestleItem, payload: PestleItemPayload, *, is_create: bool
) -> None:
    fields = set(payload.model_fields_set or set())
    if is_create or "framework" in fields:
        item.framework_slug = _clean_framework(payload.framework)
    if is_create or "type" in fields:
        item.type = _clean_type(payload.type)
    if is_create or "lens" in fields:
        item.lens = _clean_lens(payload.lens)
    if is_create or "item" in fields:
        item.item = _clean_text(
            payload.item, max_len=20000, required=True, label="item"
        )
    if (
        is_create
        or "overall_relevance_id" in fields
        or "overall_relevance_code" in fields
    ):
        relevance = _relevance_by_id_or_code(
            db,
            relevance_id=payload.overall_relevance_id,
            relevance_code=payload.overall_relevance_code,
        )
        item.overall_relevance_id = relevance.id
    if is_create or "rationale" in fields:
        item.rationale = _clean_text(
            payload.rationale, max_len=20000, required=False, label="rationale"
        )
    item.updated_at = _utcnow()


@router.get("/v1/pestle/meta")
def pestle_meta(user=Depends(require_pestle_read), db: Session = Depends(get_db)):
    return {
        "types": PESTLE_TYPES,
        "lenses": PESTLE_LENSES,
        "relevance_levels": [_relevance_out(x) for x in _relevance_levels(db)],
        "permissions": {
            "can_view_pestle": _can_read_pestle(db, user),
            "can_manage_pestle": _can_manage_pestle(db, user),
        },
    }


@router.get("/v1/pestle/items")
def list_pestle_items(
    framework: str = settings.default_framework_slug,
    q: str = "",
    type: str = "",
    lens: str = "",
    relevance_code: str = "",
    limit: int = 500,
    offset: int = 0,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    limit = max(1, min(int(limit or 500), 5000))
    offset = max(0, int(offset or 0))
    qry = db.query(PestleItem).filter(PestleItem.framework_slug == fw)
    sq = (q or "").strip().lower()
    if sq:
        like = f"%{sq}%"
        qry = qry.filter(
            or_(
                func.lower(PestleItem.item).like(like),
                func.lower(PestleItem.rationale).like(like),
                func.lower(PestleItem.type).like(like),
                func.lower(PestleItem.lens).like(like),
            )
        )
    types = [_clean_type(value) for value in _split_filter_values(type)]
    if types:
        qry = qry.filter(PestleItem.type.in_(set(types)))
    lenses = [_clean_lens(value) for value in _split_filter_values(lens)]
    if lenses:
        qry = qry.filter(PestleItem.lens.in_(set(lenses)))
    relevance_ids = []
    for value in _split_filter_values(relevance_code):
        rel = _relevance_by_id_or_code(db, relevance_code=value)
        relevance_ids.append(rel.id)
    if relevance_ids:
        qry = qry.filter(PestleItem.overall_relevance_id.in_(set(relevance_ids)))

    total = int(qry.count() or 0)
    rows = (
        qry.order_by(
            PestleItem.type.asc(), PestleItem.lens.asc(), PestleItem.updated_at.desc()
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "framework": fw,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_item_out(db, row) for row in rows],
    }


@router.post("/v1/pestle/items")
def create_pestle_item(
    payload: PestleItemPayload,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    now = _utcnow()
    row = PestleItem(
        framework_slug=_clean_framework(payload.framework),
        type=_clean_type(payload.type),
        lens=_clean_lens(payload.lens),
        item="",
        overall_relevance_id=_default_relevance(db).id,
        rationale="",
        created_by_user_id=user.id,
        created_at=now,
        updated_at=now,
    )
    _apply_item_payload(db, row, payload, is_create=True)
    db.add(row)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="pestle_item",
        entity_id=row.id,
        action="created",
        before=None,
        after=pestle_item_changelog_state(db, row.id),
        user=user,
        request_method="POST",
        request_path="/v1/pestle/items",
    )
    db.commit()
    db.refresh(row)
    return _item_out(db, row, include_links=True)


@router.get("/v1/pestle/items/{item_id}")
def get_pestle_item(
    item_id: str,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    row = _item_or_404(db, item_id)
    return _item_out(db, row, include_links=True)


@router.patch("/v1/pestle/items/{item_id}")
def update_pestle_item(
    item_id: str,
    payload: PestleItemPayload,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    row = _item_or_404(db, item_id)
    before = pestle_item_changelog_state(db, row.id)
    _apply_item_payload(db, row, payload, is_create=False)
    db.add(row)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="pestle_item",
        entity_id=row.id,
        action="updated",
        before=before,
        after=pestle_item_changelog_state(db, row.id),
        user=user,
        request_method="PATCH",
        request_path=f"/v1/pestle/items/{item_id}",
    )
    db.commit()
    db.refresh(row)
    return _item_out(db, row, include_links=True)


@router.delete("/v1/pestle/items/{item_id}")
def delete_pestle_item(
    item_id: str,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    row = _item_or_404(db, item_id)
    before = pestle_item_changelog_state(db, row.id)
    record_entity_changelog(
        db,
        entity_type="pestle_item",
        entity_id=row.id,
        action="deleted",
        before=before,
        after=None,
        user=user,
        request_method="DELETE",
        request_path=f"/v1/pestle/items/{item_id}",
    )
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/v1/pestle/business-processes")
def list_business_processes(
    q: str = "",
    limit: int = 5000,
    offset: int = 0,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    limit = max(1, min(int(limit or 5000), 5000))
    offset = max(0, int(offset or 0))
    qry = db.query(PestleBusinessProcess)
    sq = (q or "").strip().lower()
    if sq:
        qry = qry.filter(func.lower(PestleBusinessProcess.name).like(f"%{sq}%"))
    total = int(qry.count() or 0)
    rows = (
        qry.order_by(func.lower(PestleBusinessProcess.name).asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_business_process_out(r) for r in rows],
    }


@router.post("/v1/pestle/business-processes")
def create_business_process(
    payload: BusinessProcessPayload,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    name = _clean_text(payload.name, max_len=256, required=True, label="name")
    existing = (
        db.query(PestleBusinessProcess)
        .filter(func.lower(PestleBusinessProcess.name) == name.lower())
        .one_or_none()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Business process already exists")
    row = PestleBusinessProcess(
        name=name,
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="pestle_business_process",
        entity_id=row.id,
        action="created",
        before=None,
        after=pestle_business_process_changelog_state(db, row.id),
        user=user,
        request_method="POST",
        request_path="/v1/pestle/business-processes",
    )
    db.commit()
    db.refresh(row)
    return _business_process_out(row)


@router.patch("/v1/pestle/business-processes/{process_id}")
def update_business_process(
    process_id: str,
    payload: BusinessProcessPayload,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    row = _business_process_or_404(db, process_id)
    name = _clean_text(payload.name, max_len=256, required=True, label="name")
    conflict = (
        db.query(PestleBusinessProcess)
        .filter(
            func.lower(PestleBusinessProcess.name) == name.lower(),
            PestleBusinessProcess.id != row.id,
        )
        .one_or_none()
    )
    if conflict:
        raise HTTPException(status_code=400, detail="Business process already exists")
    before = pestle_business_process_changelog_state(db, row.id)
    row.name = name
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="pestle_business_process",
        entity_id=row.id,
        action="updated",
        before=before,
        after=pestle_business_process_changelog_state(db, row.id),
        user=user,
        request_method="PATCH",
        request_path=f"/v1/pestle/business-processes/{process_id}",
    )
    db.commit()
    db.refresh(row)
    return _business_process_out(row)


@router.delete("/v1/pestle/business-processes/{process_id}")
def delete_business_process(
    process_id: str,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    row = _business_process_or_404(db, process_id)
    before = pestle_business_process_changelog_state(db, row.id)
    record_entity_changelog(
        db,
        entity_type="pestle_business_process",
        entity_id=row.id,
        action="deleted",
        before=before,
        after=None,
        user=user,
        request_method="DELETE",
        request_path=f"/v1/pestle/business-processes/{process_id}",
    )
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/v1/pestle/items/{item_id}/business-processes")
def get_item_business_process_relevance(
    item_id: str,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    row = _item_or_404(db, item_id)
    processes = (
        db.query(PestleBusinessProcess)
        .order_by(func.lower(PestleBusinessProcess.name).asc())
        .all()
    )
    links = {
        str(link.business_process_id): link for link in row.business_process_links or []
    }
    default = _default_relevance(db)
    return {
        "item": _item_out(db, row),
        "items": [
            {
                "business_process": _business_process_out(proc),
                "relevance": _relevance_out(
                    links.get(str(proc.id)).relevance
                    if str(proc.id) in links
                    else default
                ),
                "relevance_id": str(
                    links.get(str(proc.id)).relevance_id
                    if str(proc.id) in links
                    else default.id
                ),
            }
            for proc in processes
        ],
    }


@router.patch("/v1/pestle/items/{item_id}/business-processes")
def replace_item_business_process_relevance(
    item_id: str,
    payload: BusinessProcessLinksPayload,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    row = _item_or_404(db, item_id)
    before = pestle_item_changelog_state(db, row.id)
    db.query(PestleBusinessProcessRelevance).filter(
        PestleBusinessProcessRelevance.pestle_item_id == row.id
    ).delete()
    now = _utcnow()
    seen: set[str] = set()
    for link in payload.items or []:
        if not link.business_process_id:
            continue
        pid = str(link.business_process_id)
        if pid in seen:
            continue
        seen.add(pid)
        process = _business_process_or_404(db, link.business_process_id)
        relevance = _relevance_by_id_or_code(
            db, relevance_id=link.relevance_id, relevance_code=link.relevance_code
        )
        if relevance.code == "na":
            continue
        db.add(
            PestleBusinessProcessRelevance(
                pestle_item_id=row.id,
                business_process_id=process.id,
                relevance_id=relevance.id,
                created_at=now,
                updated_at=now,
            )
        )
    row.updated_at = now
    db.add(row)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="pestle_item",
        entity_id=row.id,
        action="updated",
        before=before,
        after=pestle_item_changelog_state(db, row.id),
        user=user,
        request_method="PATCH",
        request_path=f"/v1/pestle/items/{item_id}/business-processes",
    )
    db.commit()
    return get_item_business_process_relevance(str(row.id), user=user, db=db)


@router.get("/v1/pestle/items/{item_id}/clauses")
def get_item_clause_relevance(
    item_id: str,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    row = _item_or_404(db, item_id)
    clauses = (
        db.query(FrameworkClause)
        .filter(FrameworkClause.framework_slug == row.framework_slug)
        .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
        .all()
    )
    clauses.sort(key=lambda c: (int(c.sort_order or 0), _ref_sort_key(c.ref)))
    links = {str(link.clause_id): link for link in row.clause_links or []}
    default = _default_relevance(db)
    return {
        "item": _item_out(db, row),
        "items": [
            {
                "clause": _clause_summary(clause),
                "relevance": _relevance_out(
                    links.get(str(clause.id)).relevance
                    if str(clause.id) in links
                    else default
                ),
                "relevance_id": str(
                    links.get(str(clause.id)).relevance_id
                    if str(clause.id) in links
                    else default.id
                ),
            }
            for clause in clauses
        ],
    }


@router.patch("/v1/pestle/items/{item_id}/clauses")
def replace_item_clause_relevance(
    item_id: str,
    payload: ClauseLinksPayload,
    user=Depends(require_pestle_manage),
    db: Session = Depends(get_db),
):
    row = _item_or_404(db, item_id)
    before = pestle_item_changelog_state(db, row.id)
    db.query(PestleClauseRelevance).filter(
        PestleClauseRelevance.pestle_item_id == row.id
    ).delete()
    now = _utcnow()
    seen: set[str] = set()
    for link in payload.items or []:
        if not link.clause_id:
            continue
        cid = str(link.clause_id)
        if cid in seen:
            continue
        seen.add(cid)
        clause = _clause_or_404(db, link.clause_id, framework=row.framework_slug)
        relevance = _relevance_by_id_or_code(
            db, relevance_id=link.relevance_id, relevance_code=link.relevance_code
        )
        if relevance.code == "na":
            continue
        db.add(
            PestleClauseRelevance(
                pestle_item_id=row.id,
                clause_id=clause.id,
                relevance_id=relevance.id,
                created_at=now,
                updated_at=now,
            )
        )
    row.updated_at = now
    db.add(row)
    db.flush()
    record_entity_changelog(
        db,
        entity_type="pestle_item",
        entity_id=row.id,
        action="updated",
        before=before,
        after=pestle_item_changelog_state(db, row.id),
        user=user,
        request_method="PATCH",
        request_path=f"/v1/pestle/items/{item_id}/clauses",
    )
    db.commit()
    return get_item_clause_relevance(str(row.id), user=user, db=db)


@router.get("/v1/pestle/items/{item_id}/changelog")
def pestle_item_changelog(
    item_id: str,
    limit: int = 50,
    offset: int = 0,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    row = _item_or_404(db, item_id)
    return list_entity_changelogs(
        db, entity_type="pestle_item", entity_id=row.id, limit=limit, offset=offset
    )


@router.get("/v1/pestle/business-processes/{process_id}/changelog")
def pestle_business_process_changelog(
    process_id: str,
    limit: int = 50,
    offset: int = 0,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    row = _business_process_or_404(db, process_id)
    return list_entity_changelogs(
        db,
        entity_type="pestle_business_process",
        entity_id=row.id,
        limit=limit,
        offset=offset,
    )


@router.get("/v1/pestle/visualisations")
def pestle_visualisations(
    framework: str = settings.default_framework_slug,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    items = (
        db.query(PestleItem)
        .filter(PestleItem.framework_slug == fw)
        .order_by(PestleItem.type.asc(), PestleItem.lens.asc(), PestleItem.item.asc())
        .all()
    )
    item_ids = [x.id for x in items]

    lens_type_counts: dict[str, dict[str, int]] = {
        lens: {typ: 0 for typ in PESTLE_TYPES} for lens in PESTLE_LENSES
    }
    relevance_counts: dict[str, dict[str, Any]] = {}
    for level in _relevance_levels(db):
        relevance_counts[level.code] = {
            "code": level.code,
            "label": level.label,
            "count": 0,
        }
    item_summaries = []
    for item in items:
        lens_type_counts.setdefault(item.lens, {}).setdefault(item.type, 0)
        lens_type_counts[item.lens][item.type] += 1
        rel = item.overall_relevance
        code = getattr(rel, "code", "na") or "na"
        relevance_counts.setdefault(
            code, {"code": code, "label": getattr(rel, "label", code), "count": 0}
        )
        relevance_counts[code]["count"] += 1
        item_summaries.append(_item_out(db, item))

    clause_links = []
    process_links = []
    if item_ids:
        rows = (
            db.query(PestleClauseRelevance)
            .join(PestleItem, PestleItem.id == PestleClauseRelevance.pestle_item_id)
            .join(
                FrameworkClause, FrameworkClause.id == PestleClauseRelevance.clause_id
            )
            .join(
                PestleRelevanceLevel,
                PestleRelevanceLevel.id == PestleClauseRelevance.relevance_id,
            )
            .filter(PestleClauseRelevance.pestle_item_id.in_(item_ids))
            .filter(PestleRelevanceLevel.code != "na")
            .all()
        )
        for link in rows:
            clause_links.append(
                {
                    "item_id": str(link.pestle_item_id),
                    "item": link.pestle_item.item,
                    "item_type": link.pestle_item.type,
                    "type": link.pestle_item.type,
                    "lens": link.pestle_item.lens,
                    "clause_id": str(link.clause_id),
                    "clause_ref": link.clause.ref,
                    "clause_title": link.clause.title,
                    "relevance_code": link.relevance.code,
                    "relevance_label": link.relevance.label,
                    "relevance": _relevance_out(link.relevance),
                }
            )
        proc_rows = (
            db.query(PestleBusinessProcessRelevance)
            .join(
                PestleItem,
                PestleItem.id == PestleBusinessProcessRelevance.pestle_item_id,
            )
            .join(
                PestleBusinessProcess,
                PestleBusinessProcess.id
                == PestleBusinessProcessRelevance.business_process_id,
            )
            .join(
                PestleRelevanceLevel,
                PestleRelevanceLevel.id == PestleBusinessProcessRelevance.relevance_id,
            )
            .filter(PestleBusinessProcessRelevance.pestle_item_id.in_(item_ids))
            .filter(PestleRelevanceLevel.code != "na")
            .all()
        )
        for link in proc_rows:
            process_links.append(
                {
                    "item_id": str(link.pestle_item_id),
                    "item": link.pestle_item.item,
                    "item_type": link.pestle_item.type,
                    "type": link.pestle_item.type,
                    "lens": link.pestle_item.lens,
                    "business_process_id": str(link.business_process_id),
                    "business_process": link.business_process.name,
                    "relevance_code": link.relevance.code,
                    "relevance_label": link.relevance.label,
                    "relevance": _relevance_out(link.relevance),
                }
            )

    return {
        "framework": fw,
        "types": PESTLE_TYPES,
        "lenses": PESTLE_LENSES,
        "items": item_summaries,
        "lens_type_counts": lens_type_counts,
        "relevance_counts": list(relevance_counts.values()),
        "clause_links": clause_links,
        "process_links": process_links,
    }


@router.get("/v1/clauses/{clause_id}/pestle")
def clause_pestle_relevance(
    clause_id: str,
    user=Depends(require_pestle_read),
    db: Session = Depends(get_db),
):
    clause = _clause_or_404(db, clause_id)
    links = (
        db.query(PestleClauseRelevance)
        .join(PestleItem, PestleItem.id == PestleClauseRelevance.pestle_item_id)
        .filter(PestleClauseRelevance.clause_id == clause.id)
        .filter(PestleItem.framework_slug == clause.framework_slug)
        .all()
    )
    links.sort(
        key=lambda x: (
            x.pestle_item.type,
            x.pestle_item.lens,
            _ref_sort_key(x.pestle_item.type),
        )
    )
    return {
        "clause": _clause_summary(clause),
        "items": [
            {
                "pestle_item": _item_out(db, link.pestle_item),
                "relevance": _relevance_out(link.relevance),
                "relevance_id": str(link.relevance_id),
            }
            for link in links
        ],
    }
