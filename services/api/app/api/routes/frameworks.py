from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from urllib.parse import urlsplit
import uuid
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.payloads import FrameworkListItem, FrameworkUpsert
from app.core.config import settings
from app.db.models import ControlItem, Framework, FrameworkClause, ControlClauseLink, Mapping
from app.db.session import get_db
from app.security.auth import require_admin

router = APIRouter()


@router.get("/v1/frameworks")
def list_frameworks(db: Session = Depends(get_db)):
    """List known framework slugs.

    Sources:
    - framework rows explicitly created/imported
    - any framework_slug already present on control_items
    - configured default framework slug (always included)
    """

    control_counts = {
        slug: int(n)
        for slug, n in (
            db.query(ControlItem.framework_slug, func.count(ControlItem.id))
            .group_by(ControlItem.framework_slug)
            .all()
        )
    }

    rows = db.query(Framework).order_by(Framework.slug.asc()).all()
    by_slug = {f.slug: f for f in rows}

    all_slugs = set(by_slug.keys()) | set(control_counts.keys())
    all_slugs.add(settings.default_framework_slug)

    items: list[FrameworkListItem] = []
    for slug in sorted(all_slugs):
        row = by_slug.get(slug)

        # Framework model currently persists only `slug`; metadata fields are
        # optional and may be provided by newer schemas in future migrations.
        name = getattr(row, "name", None) if row else None
        version = getattr(row, "version", None) if row else None
        description = getattr(row, "description", None) if row else None
        upstream_url = (getattr(row, "upstream_url", None) if row else None) or (
            getattr(row, "url", None) if row else None
        )

        items.append(
            FrameworkListItem(
                slug=slug,
                name=name or slug,
                version=version,
                description=description,
                upstream_url=upstream_url,
                control_count=control_counts.get(slug, 0),
                is_default=(slug == settings.default_framework_slug),
            )
        )

    return {
        "items": [i.model_dump() for i in items],
        "default": settings.default_framework_slug,
    }


@router.post("/v1/frameworks", dependencies=[Depends(require_admin)])
def upsert_framework(payload: FrameworkUpsert, db: Session = Depends(get_db)):
    slug = (payload.slug or "").strip()
    if not slug:
        raise HTTPException(status_code=400, detail="slug is required")

    row = db.query(Framework).filter(Framework.slug == slug).one_or_none()
    created = False
    if row is None:
        row = Framework(slug=slug)
        db.add(row)
        created = True

    # Persist optional metadata only when the DB model has those attributes.
    # This keeps compatibility with existing deployments where frameworks only
    # store a slug.
    if hasattr(row, "name"):
        setattr(
            row,
            "name",
            (payload.name or slug).strip() if payload.name is not None else slug,
        )
    if hasattr(row, "version"):
        setattr(row, "version", (payload.version or "").strip() or None)
    if hasattr(row, "description"):
        setattr(row, "description", (payload.description or "").strip() or None)
    if hasattr(row, "upstream_url"):
        setattr(row, "upstream_url", (payload.upstream_url or "").strip() or None)
    if hasattr(row, "url"):
        setattr(row, "url", (payload.upstream_url or "").strip() or None)

    db.commit()

    return {
        "ok": True,
        "created": created,
        "item": {
            "slug": row.slug,
            "name": getattr(row, "name", None) or row.slug,
            "version": getattr(row, "version", None),
            "description": getattr(row, "description", None),
            "upstream_url": getattr(row, "upstream_url", None)
            or getattr(row, "url", None),
            "is_default": row.slug == settings.default_framework_slug,
        },
    }


class FrameworkNodeInput(BaseModel):
    kind: str = "control"  # clause, annex_control, custom, or another control type
    ref: str
    title: str
    description: str | None = None
    upstream_url: str | None = None
    parent_ref: str | None = None
    clause_refs: list[str] = Field(default_factory=list)
    in_scope: bool = True
    sort_order: int = 0


def _url(value: str | None) -> str | None:
    value = (value or "").strip() or None
    if value and (urlsplit(value).scheme not in ("http", "https") or not urlsplit(value).netloc):
        raise HTTPException(400, "Upstream URL must be an absolute HTTP(S) URL")
    return value


def _node(row: ControlItem, clauses: dict[str, FrameworkClause], links: dict[uuid.UUID, list[str]]) -> dict:
    meta = row.meta or {}
    return {
        "id": str(row.id), "kind": row.type, "ref": row.ref,
        "title": row.title or "", "description": meta.get("description") or "",
        "upstream_url": meta.get("upstream_url") or "",
        "parent_ref": (clauses[row.ref].parent.ref if row.type == "clause" and row.ref in clauses and clauses[row.ref].parent else meta.get("parent_ref") or ""),
        "clause_refs": links.get(row.id, []), "in_scope": row.in_scope,
        "sort_order": clauses[row.ref].sort_order if row.type == "clause" and row.ref in clauses else meta.get("sort_order", 0),
    }


@router.get("/v1/admin/frameworks/{slug}/nodes", dependencies=[Depends(require_admin)])
def framework_nodes(slug: str, db: Session = Depends(get_db)):
    rows = db.query(ControlItem).filter(ControlItem.framework_slug == slug).all()
    clauses = {c.ref: c for c in db.query(FrameworkClause).filter(FrameworkClause.framework_slug == slug).all()}
    links = {}
    for link in db.query(ControlClauseLink).join(ControlItem).filter(ControlItem.framework_slug == slug).all():
        if link.clause:
            links.setdefault(link.control_item_id, []).append(link.clause.ref)
    return {"items": [_node(row, clauses, links) for row in rows]}


@router.put("/v1/admin/frameworks/{slug}/nodes/{kind}/{ref}", dependencies=[Depends(require_admin)])
def save_framework_node(slug: str, kind: str, ref: str, payload: FrameworkNodeInput, db: Session = Depends(get_db)):
    kind, ref = kind.strip(), ref.strip()
    if kind != payload.kind.strip() or ref != payload.ref.strip() or not kind or not ref:
        raise HTTPException(400, "Node kind and reference must match the URL")
    if len(kind) > 32 or len(ref) > 64 or not payload.title.strip() or len(payload.title) > 256:
        raise HTTPException(400, "Invalid kind, reference or title")
    framework_exists = bool(db.query(Framework.id).filter(Framework.slug == slug).first())
    if not framework_exists and slug != settings.default_framework_slug and not db.query(ControlItem.id).filter_by(framework_slug=slug).first():
        raise HTTPException(404, "Create the framework first")
    parent_ref = (payload.parent_ref or "").strip()
    if parent_ref == ref:
        raise HTTPException(400, "A node cannot be its own parent")
    parent = None
    if parent_ref:
        parent = db.query(ControlItem).filter_by(framework_slug=slug, type=kind, ref=parent_ref).one_or_none()
        if not parent:
            raise HTTPException(400, "Parent must exist in the same framework and category")
        seen = {ref}
        cursor = parent
        while cursor:
            if cursor.ref in seen:
                raise HTTPException(400, "Parent relationship would form a cycle")
            seen.add(cursor.ref)
            next_ref = (cursor.meta or {}).get("parent_ref")
            cursor = db.query(ControlItem).filter_by(framework_slug=slug, type=kind, ref=next_ref).one_or_none() if next_ref else None
    clause_rows = []
    if kind != "clause":
        for clause_ref in dict.fromkeys(payload.clause_refs):
            clause = db.query(FrameworkClause).filter_by(framework_slug=slug, ref=clause_ref).one_or_none()
            if not clause:
                raise HTTPException(400, f"Unknown clause: {clause_ref}")
            clause_rows.append(clause)
    elif payload.clause_refs:
        raise HTTPException(400, "Only controls can link to clauses")
    url = _url(payload.upstream_url)
    # Older imports may have controls but no framework row. Promote the slug
    # when the first edited node is saved, in the same transaction.
    if not framework_exists:
        db.add(Framework(slug=slug, name=slug))
    row = db.query(ControlItem).filter_by(framework_slug=slug, type=kind, ref=ref).one_or_none()
    created = row is None
    if created:
        row = ControlItem(framework_slug=slug, type=kind, ref=ref)
        db.add(row)
    row.title = payload.title.strip()
    row.in_scope = payload.in_scope
    row.meta = {**(row.meta or {}), "description": payload.description or "", "upstream_url": url,
                "parent_ref": parent_ref or None, "sort_order": payload.sort_order}
    db.flush()
    if kind == "clause":
        clause = db.query(FrameworkClause).filter_by(framework_slug=slug, ref=ref).one_or_none()
        if clause is None:
            clause = FrameworkClause(framework_slug=slug, ref=ref)
            db.add(clause)
        clause.title = row.title
        clause.parent_clause_id = db.query(FrameworkClause.id).filter_by(framework_slug=slug, ref=parent_ref).scalar() if parent_ref else None
        clause.sort_order = payload.sort_order
        clause.meta = {**(clause.meta or {}), **row.meta}
    else:
        db.query(ControlClauseLink).filter_by(control_item_id=row.id).delete()
        for clause in clause_rows:
            db.add(ControlClauseLink(control_item_id=row.id, clause_id=clause.id, applicability="applicable"))
    db.commit()
    return {"ok": True, "created": created, "id": str(row.id)}


@router.delete("/v1/admin/frameworks/{slug}/nodes/{kind}/{ref}", dependencies=[Depends(require_admin)])
def delete_framework_node(slug: str, kind: str, ref: str, db: Session = Depends(get_db)):
    row = db.query(ControlItem).filter_by(framework_slug=slug, type=kind, ref=ref).one_or_none()
    if not row:
        raise HTTPException(404, "Node not found")
    children = db.query(ControlItem).filter_by(framework_slug=slug, type=kind).all()
    if any((child.meta or {}).get("parent_ref") == ref and child.id != row.id for child in children):
        raise HTTPException(409, "Move or delete child nodes first")
    clause = db.query(FrameworkClause).filter_by(framework_slug=slug, ref=ref).one_or_none() if kind == "clause" else None
    if clause:
        if db.query(FrameworkClause.id).filter_by(parent_clause_id=clause.id).first():
            raise HTTPException(409, "Move or delete child clauses first")
        if db.query(ControlClauseLink.clause_id).filter_by(clause_id=clause.id).first():
            raise HTTPException(409, "Remove control links first")
        db.delete(clause)
    # Keep related audit/mapping data intact unless the administrator has
    # explicitly removed the relationships first.
    if db.query(ControlClauseLink.control_item_id).filter_by(control_item_id=row.id).first():
        raise HTTPException(409, "Remove clause links first")
    if db.query(Mapping.id).filter_by(control_item_id=row.id).first():
        raise HTTPException(409, "Remove event mappings before deleting this node")
    db.delete(row)
    db.commit()
    return {"ok": True}
