from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.utils import control_justification as _control_justification
from app.api.utils import control_upstream_url as _upstream_url
from app.api.utils import ref_sort_key as _ref_sort_key
from app.core.config import settings
from app.db.models import ControlClauseLink, ControlItem, FrameworkClause
from app.db.session import get_db
from app.services.entity_changelog import list_entity_changelogs

router = APIRouter()


def _clean_evidence_title(raw: object, fallback_url: str = "") -> str:
    value = str(raw or "").strip()
    if not value:
        value = fallback_url
    return value[:256]


def _clause_evidence_mappings(c: FrameworkClause) -> list[dict]:
    meta = dict(getattr(c, "meta", None) or {})
    raw = meta.get("evidence_mappings")
    if raw is None:
        raw = meta.get("evidence_urls")
    if raw is None:
        raw = (
            meta.get("evidenceUrls")
            or meta.get("evidence_links")
            or meta.get("evidence")
        )
    if isinstance(raw, str):
        raw = [line.strip() for line in raw.splitlines()]
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        title = ""
        url = ""
        if isinstance(item, dict):
            title = str(
                item.get("title") or item.get("label") or item.get("name") or ""
            ).strip()
            url = str(
                item.get("url") or item.get("href") or item.get("link") or ""
            ).strip()
        else:
            url = str(item or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({"title": _clean_evidence_title(title, url), "url": url})
    return out


def _clause_evidence_urls(c: FrameworkClause) -> list[str]:
    return [item["url"] for item in _clause_evidence_mappings(c)]


def _clause_out(c: FrameworkClause, link_count: int | None = None) -> dict:
    evidence_mappings = _clause_evidence_mappings(c)
    return {
        "id": str(c.id),
        "framework": c.framework_slug,
        "ref": c.ref,
        "title": c.title,
        "parent_id": str(c.parent_clause_id) if c.parent_clause_id else None,
        "parent_ref": c.parent.ref if getattr(c, "parent", None) else None,
        "sort_order": int(c.sort_order or 0),
        "upstream_url": _upstream_url(c),
        "evidence_mappings": evidence_mappings,
        # Backward-compatible URL-only projection for older UI/API callers.
        "evidence_urls": [item["url"] for item in evidence_mappings],
        "evidence_count": len(evidence_mappings),
        "metadata": c.meta or {},
        "control_link_count": int(link_count or 0),
        "child_count": len(getattr(c, "children", None) or []),
    }


def _tree(items: list[dict]) -> list[dict]:
    by_id = {it["id"]: {**it, "children": []} for it in items}
    roots: list[dict] = []
    for it in by_id.values():
        pid = it.get("parent_id")
        if pid and pid in by_id:
            by_id[pid]["children"].append(it)
        else:
            roots.append(it)

    def sort_key(it: dict):
        return (int(it.get("sort_order") or 0), _ref_sort_key(it.get("ref")))

    def sort_children(nodes: list[dict]):
        nodes.sort(key=sort_key)
        for node in nodes:
            sort_children(node.get("children") or [])

    sort_children(roots)
    return roots


@router.get("/v1/clauses")
def list_clauses(
    framework: str = settings.default_framework_slug,
    limit: int = 5000,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    limit = max(1, min(int(limit or 5000), 5000))
    offset = max(0, int(offset or 0))
    rows = (
        db.query(
            FrameworkClause,
            func.count(ControlClauseLink.control_item_id).label("link_count"),
        )
        .outerjoin(ControlClauseLink, ControlClauseLink.clause_id == FrameworkClause.id)
        .filter(FrameworkClause.framework_slug == framework)
        .group_by(FrameworkClause.id)
        .order_by(FrameworkClause.sort_order.asc(), FrameworkClause.ref.asc())
        .all()
    )
    items = [_clause_out(c, link_count) for c, link_count in rows]
    items.sort(
        key=lambda it: (int(it.get("sort_order") or 0), _ref_sort_key(it["ref"]))
    )
    total = len(items)
    page_items = items[offset : offset + limit]
    return {
        "framework": framework,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": page_items,
        "tree": _tree(page_items),
    }


@router.get("/v1/clauses/{clause_id}")
def get_clause(clause_id: str, db: Session = Depends(get_db)):
    from app.api.utils import try_uuid as _try_uuid

    cid = _try_uuid(clause_id)
    if not cid:
        raise HTTPException(status_code=400, detail="clause_id must be a UUID")

    row = db.query(FrameworkClause).filter(FrameworkClause.id == cid).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Clause not found")
    return _clause_out(row)


@router.get("/v1/clauses/{clause_id}/changelog")
def clause_changelog(
    clause_id: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    try:
        import uuid as _uuid

        cid = _uuid.UUID(str(clause_id))
    except Exception:
        raise HTTPException(status_code=400, detail="clause_id must be a UUID")
    exists = db.query(FrameworkClause.id).filter(FrameworkClause.id == cid).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Clause not found")
    return list_entity_changelogs(
        db, entity_type="clause", entity_id=cid, limit=limit, offset=offset
    )


@router.get("/v1/clauses/{clause_id}/controls")
def clause_controls(
    clause_id: str,
    limit: int = 5000,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    limit = max(1, min(int(limit or 5000), 5000))
    offset = max(0, int(offset or 0))
    from app.api.utils import try_uuid as _try_uuid

    cid = _try_uuid(clause_id)
    if not cid:
        raise HTTPException(status_code=400, detail="clause_id must be a UUID")

    clause = db.query(FrameworkClause).filter(FrameworkClause.id == cid).one_or_none()
    if not clause:
        raise HTTPException(status_code=404, detail="Clause not found")

    rows = (
        db.query(ControlClauseLink, ControlItem)
        .join(ControlItem, ControlItem.id == ControlClauseLink.control_item_id)
        .filter(
            ControlClauseLink.clause_id == cid,
            ControlItem.framework_slug == clause.framework_slug,
        )
        .order_by(ControlItem.type.asc(), ControlItem.ref.asc())
        .all()
    )
    items = []
    for link, control in rows:
        items.append(
            {
                "id": str(control.id),
                "framework": control.framework_slug,
                "type": control.type,
                "ref": control.ref,
                "title": control.title,
                "in_scope": control.in_scope,
                "justification": _control_justification(control),
                "upstream_url": _upstream_url(control),
                "applicability": link.applicability,
            }
        )
    items.sort(key=lambda it: (it["type"], _ref_sort_key(it["ref"])))
    total = len(items)
    return {
        "clause": _clause_out(clause),
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items[offset : offset + limit],
    }
