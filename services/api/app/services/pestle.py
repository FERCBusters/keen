"""Pestle application operations."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive as _utcnow
from app.core.references import ref_sort_key as _ref_sort_key
from app.core.references import try_uuid as _try_uuid
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    FrameworkClause,
    PestleClauseRelevance,
    PestleControlRelevance,
    PestleItem,
    PestleRelevanceLevel,
    User,
)
from app.security.permissions import (
    has_any_permission,
    has_permission,
    has_shared_permission,
)
from app.services.affected import (
    MAX_AFFECTED,
    AffectedRef,
    affected_list,
    affected_loads,
)
from app.services.affected_links import replace_affected_links
from app.services.control_refs import (
    CLAUSE_ROW_TYPE,
    clause_summary,
    control_by_id_or_ref,
    control_summary,
)
from app.services.entity_changelog import (
    list_entity_changelogs,
    pestle_item_changelog_state,
    record_entity_changelog,
)
from app.services.errors import DomainError
from app.services.frameworks import framework_parameter
from app.services.text import clean_text, split_filter_values

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
    # The people, vendors and assets the factor affects; omit to keep them.
    affected: list[AffectedRef] | None = Field(default=None, max_length=MAX_AFFECTED)


class RelevanceLinkPayload(BaseModel):
    clause_id: uuid.UUID | None = None
    control_id: uuid.UUID | None = None
    relevance_id: uuid.UUID | None = None
    relevance_code: str | None = Field(default="na", max_length=32)


class RelevanceLinksPayload(BaseModel):
    """The clauses, or the controls, a factor is relevant to, and how strongly."""

    items: list[RelevanceLinkPayload] = Field(default_factory=list, max_length=5000)


def _replace_affected(
    db: Session, item: PestleItem, refs: list[AffectedRef], *, user: User
) -> None:
    """Replace the people, vendors and assets the factor affects."""

    item.affected_links = replace_affected_links(
        db,
        list(item.affected_links),
        refs,
        owner={"pestle_item_id": item.id},
        user=user,
    )
    db.flush()


def can_read_pestle(db: Session, user: User | None) -> bool:
    # Risk viewers can also read PESTLE(E) assessments.
    return has_any_permission(
        db, user, PESTLE_MANAGE_PERMISSION, PESTLE_READ_PERMISSION
    ) or has_shared_permission(db, user, RISK_MANAGE_PERMISSION, RISK_READ_PERMISSION)


def can_manage_pestle(db: Session, user: User | None) -> bool:
    # Risk managers also manage PESTLE(E) factors.
    return has_permission(db, user, PESTLE_MANAGE_PERMISSION) or has_shared_permission(
        db, user, RISK_MANAGE_PERMISSION
    )


def _clean_type(raw: str | None) -> str:
    value = clean_text(raw, max_len=32, required=True, label="type")
    match = {x.lower(): x for x in PESTLE_TYPES}.get(value.lower())
    if not match:
        raise DomainError(status_code=400, detail="type must be a valid PESTLE(E) type")
    return match


def _clean_lens(raw: str | None) -> str:
    value = clean_text(raw, max_len=16, required=True, label="lens")
    match = {x.lower(): x for x in PESTLE_LENSES}.get(value.lower())
    if not match:
        raise DomainError(status_code=400, detail="lens must be Internal or External")
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
        raise DomainError(status_code=400, detail="Unknown relevance_id")
    code = _normalise_relevance_code(relevance_code)
    row = (
        db.query(PestleRelevanceLevel)
        .filter(PestleRelevanceLevel.code == code)
        .one_or_none()
    )
    if not row:
        raise DomainError(status_code=400, detail="Unknown relevance_code")
    return row


def _default_relevance(db: Session) -> PestleRelevanceLevel:
    return _relevance_by_id_or_code(db, relevance_code="na")


def relevance_summary(row: PestleRelevanceLevel | None) -> dict[str, Any]:
    return {
        "id": str(row.id) if row else None,
        "code": row.code if row else "na",
        "label": row.label if row else "N/A",
        "sort_order": int(row.sort_order or 0) if row else 0,
    }


def _rated(links) -> list:
    """The links whose relevance is above "na"; saving stores no other."""
    return [link for link in links if link.relevance.code != "na"]


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
    clause_links = _rated(row.clause_links)
    control_links = _rated(row.control_links)
    out: dict[str, Any] = {
        "id": str(row.id),
        "framework": row.framework_slug,
        "type": row.type,
        "lens": row.lens,
        "item": row.item or "",
        "overall_relevance": relevance_summary(row.overall_relevance),
        "overall_relevance_id": (
            str(row.overall_relevance_id) if row.overall_relevance_id else None
        ),
        "rationale": row.rationale or "",
        "created_by": getattr(row.created_by, "username", None),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "clause_count": len(clause_links),
        "control_count": len(control_links),
        "affected": affected_list(list(row.affected_links)),
    }
    if include_links:
        out["clauses"] = [
            {
                "clause": clause_summary(link.clause),
                "relevance": relevance_summary(link.relevance),
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
        out["controls"] = [
            {
                "control": control_summary(link.control),
                "relevance": relevance_summary(link.relevance),
                "relevance_id": str(link.relevance_id),
                "updated_at": link.updated_at.isoformat() if link.updated_at else None,
            }
            for link in sorted(
                control_links,
                key=lambda x: (x.control.type, _ref_sort_key(x.control.ref)),
            )
        ]
        out["related_controls"] = [
            control_summary(c)
            for c in _related_controls_for_item(db, row.id, row.framework_slug)
        ]
    return out


def _item_or_404(db: Session, item_id: str | uuid.UUID) -> PestleItem:
    iid = item_id if isinstance(item_id, uuid.UUID) else _try_uuid(str(item_id))
    if not iid:
        raise DomainError(status_code=400, detail="pestle item id must be a UUID")
    row = db.query(PestleItem).filter(PestleItem.id == iid).one_or_none()
    if not row:
        raise DomainError(status_code=404, detail="PESTLE(E) item not found")
    return row


def _clause_or_404(
    db: Session, clause_id: str | uuid.UUID, framework: str | None = None
) -> FrameworkClause:
    cid = clause_id if isinstance(clause_id, uuid.UUID) else _try_uuid(str(clause_id))
    if not cid:
        raise DomainError(status_code=400, detail="clause_id must be a UUID")
    q = db.query(FrameworkClause).filter(FrameworkClause.id == cid)
    if framework:
        q = q.filter(FrameworkClause.framework_slug == framework)
    row = q.one_or_none()
    if not row:
        raise DomainError(status_code=404, detail="Clause not found")
    return row


def _apply_item_payload(
    db: Session, item: PestleItem, payload: PestleItemPayload, *, is_create: bool
) -> None:
    fields = set(payload.model_fields_set or set())
    if is_create or "framework" in fields:
        item.framework_slug = framework_parameter(payload.framework)
    # Type, lens and item are required fields, so every payload sets them.
    item.type = _clean_type(payload.type)
    item.lens = _clean_lens(payload.lens)
    item.item = clean_text(payload.item, max_len=20000, required=True, label="item")
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
        item.rationale = clean_text(
            payload.rationale, max_len=20000, required=False, label="rationale"
        )
    item.updated_at = _utcnow()


def pestle_meta(*, user, db: Session):
    return {
        "types": PESTLE_TYPES,
        "lenses": PESTLE_LENSES,
        "relevance_levels": [relevance_summary(x) for x in _relevance_levels(db)],
        "permissions": {
            "can_view_pestle": can_read_pestle(db, user),
            "can_manage_pestle": can_manage_pestle(db, user),
        },
    }


def list_pestle_items(
    *,
    framework: str = settings.default_framework_slug,
    q: str = "",
    type: str = "",
    lens: str = "",
    relevance_code: str = "",
    limit: int = 500,
    offset: int = 0,
    user,
    db: Session,
):
    fw = framework_parameter(framework)
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
    types = [_clean_type(value) for value in split_filter_values(type)]
    if types:
        qry = qry.filter(PestleItem.type.in_(set(types)))
    lenses = [_clean_lens(value) for value in split_filter_values(lens)]
    if lenses:
        qry = qry.filter(PestleItem.lens.in_(set(lenses)))
    relevance_ids = []
    for value in split_filter_values(relevance_code):
        rel = _relevance_by_id_or_code(db, relevance_code=value)
        relevance_ids.append(rel.id)
    if relevance_ids:
        qry = qry.filter(PestleItem.overall_relevance_id.in_(set(relevance_ids)))

    total = int(qry.count() or 0)
    rows = (
        qry.options(affected_loads(PestleItem.affected_links))
        .order_by(
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


def create_pestle_item(*, payload: PestleItemPayload, user, db: Session):
    now = _utcnow()
    row = PestleItem(
        framework_slug=framework_parameter(payload.framework),
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
    if payload.affected is not None:
        _replace_affected(db, row, payload.affected, user=user)
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


def get_pestle_item(*, item_id: str, user, db: Session):
    row = _item_or_404(db, item_id)
    return _item_out(db, row, include_links=True)


def update_pestle_item(*, item_id: str, payload: PestleItemPayload, user, db: Session):
    row = _item_or_404(db, item_id)
    before = pestle_item_changelog_state(db, row.id)
    _apply_item_payload(db, row, payload, is_create=False)
    if payload.affected is not None:
        _replace_affected(db, row, payload.affected, user=user)
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


def delete_pestle_item(*, item_id: str, user, db: Session):
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


def _control_or_404(db: Session, control_id: uuid.UUID, framework: str) -> ControlItem:
    row = control_by_id_or_ref(db, framework, str(control_id))
    if not row or row.type == CLAUSE_ROW_TYPE:
        raise DomainError(status_code=404, detail="Control not found")
    return row


def _relevance_rows(
    db: Session, row: PestleItem, targets: list, links: list, key: str, summary
) -> dict[str, Any]:
    """Every clause, or control, of the factor's framework with its relevance."""
    by_target = {getattr(link, key + "_id"): link for link in links}
    default = _default_relevance(db)
    items = []
    for target in targets:
        link = by_target.get(target.id)
        relevance = link.relevance if link else default
        items.append(
            {
                key.removesuffix("_item"): summary(target),
                "relevance": relevance_summary(relevance),
                "relevance_id": str(relevance.id),
            }
        )
    return {"item": _item_out(db, row), "items": items}


def _replace_relevance(
    db: Session,
    row: PestleItem,
    payload: RelevanceLinksPayload,
    *,
    model,
    key: str,
    field: str,
    resolve,
    user,
    request_path: str,
) -> None:
    """Replace the factor's clause, or control, relevance with the payload's.

    ``field`` names the payload member that carries the target's id and ``key``
    the link column it is stored in. A relevance of "na" stores no link.
    """
    before = pestle_item_changelog_state(db, row.id)
    db.query(model).filter(model.pestle_item_id == row.id).delete()
    now = _utcnow()
    seen: set[uuid.UUID] = set()
    for link in payload.items:
        target_id = getattr(link, field)
        if not target_id or target_id in seen:
            continue
        seen.add(target_id)
        target = resolve(db, target_id, row.framework_slug)
        relevance = _relevance_by_id_or_code(
            db, relevance_id=link.relevance_id, relevance_code=link.relevance_code
        )
        if relevance.code == "na":
            continue
        db.add(
            model(
                pestle_item_id=row.id,
                relevance_id=relevance.id,
                created_at=now,
                updated_at=now,
                **{key: target.id},
            )
        )
    row.updated_at = now
    db.add(row)
    db.flush()
    db.expire(row, ["clause_links", "control_links"])
    record_entity_changelog(
        db,
        entity_type="pestle_item",
        entity_id=row.id,
        action="updated",
        before=before,
        after=pestle_item_changelog_state(db, row.id),
        user=user,
        request_method="PATCH",
        request_path=request_path,
    )
    db.commit()


def get_item_clause_relevance(*, item_id: str, user, db: Session):
    row = _item_or_404(db, item_id)
    clauses = (
        db.query(FrameworkClause)
        .filter(FrameworkClause.framework_slug == row.framework_slug)
        .all()
    )
    clauses.sort(key=lambda c: (int(c.sort_order or 0), _ref_sort_key(c.ref)))
    return _relevance_rows(
        db, row, clauses, list(row.clause_links), "clause", clause_summary
    )


def replace_item_clause_relevance(
    *, item_id: str, payload: RelevanceLinksPayload, user, db: Session
):
    row = _item_or_404(db, item_id)
    _replace_relevance(
        db,
        row,
        payload,
        model=PestleClauseRelevance,
        key="clause_id",
        field="clause_id",
        resolve=_clause_or_404,
        user=user,
        request_path=f"/v1/pestle/items/{item_id}/clauses",
    )
    return get_item_clause_relevance(item_id=str(row.id), user=user, db=db)


def get_item_control_relevance(*, item_id: str, user, db: Session):
    row = _item_or_404(db, item_id)
    controls = (
        db.query(ControlItem)
        .filter(
            ControlItem.framework_slug == row.framework_slug,
            ControlItem.type != CLAUSE_ROW_TYPE,
        )
        .all()
    )
    controls.sort(key=lambda c: (c.type, _ref_sort_key(c.ref)))
    return _relevance_rows(
        db, row, controls, list(row.control_links), "control_item", control_summary
    )


def replace_item_control_relevance(
    *, item_id: str, payload: RelevanceLinksPayload, user, db: Session
):
    row = _item_or_404(db, item_id)
    _replace_relevance(
        db,
        row,
        payload,
        model=PestleControlRelevance,
        key="control_item_id",
        field="control_id",
        resolve=_control_or_404,
        user=user,
        request_path=f"/v1/pestle/items/{item_id}/controls",
    )
    return get_item_control_relevance(item_id=str(row.id), user=user, db=db)


def pestle_item_changelog(
    *, item_id: str, limit: int = 50, offset: int = 0, user, db: Session
):
    row = _item_or_404(db, item_id)
    return list_entity_changelogs(
        db, entity_type="pestle_item", entity_id=row.id, limit=limit, offset=offset
    )


def pestle_visualisations(
    *, framework: str = settings.default_framework_slug, user, db: Session
):
    fw = framework_parameter(framework)
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
    if item_ids:
        for link in _rated(
            db.query(PestleClauseRelevance)
            .filter(PestleClauseRelevance.pestle_item_id.in_(item_ids))
            .all()
        ):
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
                    "relevance": relevance_summary(link.relevance),
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
    }


def _factors_out(db: Session, links: list) -> list[dict[str, Any]]:
    links.sort(key=lambda x: (x.pestle_item.type, x.pestle_item.lens))
    return [
        {
            "pestle_item": _item_out(db, link.pestle_item),
            "relevance": relevance_summary(link.relevance),
            "relevance_id": str(link.relevance_id),
        }
        for link in links
    ]


def clause_pestle_relevance(*, clause_id: str, user, db: Session):
    clause = _clause_or_404(db, clause_id)
    links = (
        db.query(PestleClauseRelevance)
        .join(PestleItem, PestleItem.id == PestleClauseRelevance.pestle_item_id)
        .filter(PestleClauseRelevance.clause_id == clause.id)
        .filter(PestleItem.framework_slug == clause.framework_slug)
        .all()
    )
    return {"clause": clause_summary(clause), "items": _factors_out(db, links)}


def control_pestle_relevance(*, control_id: str, user, db: Session):
    cid = _try_uuid(control_id)
    if not cid:
        raise DomainError(status_code=400, detail="control_id must be a UUID")
    control = db.get(ControlItem, cid)
    if not control:
        raise DomainError(status_code=404, detail="Control not found")
    links = (
        db.query(PestleControlRelevance)
        .join(PestleItem, PestleItem.id == PestleControlRelevance.pestle_item_id)
        .filter(PestleControlRelevance.control_item_id == control.id)
        .filter(PestleItem.framework_slug == control.framework_slug)
        .all()
    )
    return {"control": control_summary(control), "items": _factors_out(db, links)}
