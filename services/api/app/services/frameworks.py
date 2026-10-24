"""Frameworks application operations."""

from __future__ import annotations

import re
import uuid
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    Framework,
    FrameworkClause,
    Mapping,
)
from app.schemas import FrameworkUpsert
from app.services.errors import DomainError
from app.services.framework_catalogue import _framework_selection

_SLUG_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")


def validate_framework_slug(slug: str) -> str:
    """Return the slug when it has the form every framework parameter accepts.

    A framework created with any other slug could never be selected or used.
    """
    if not _SLUG_RE.fullmatch(slug):
        raise DomainError(
            status_code=400,
            detail="Framework slugs use up to 64 letters, digits, '.', '_', ':' or '-'",
        )
    return slug


def framework_parameter(raw: str | None) -> str:
    """The framework a request names, or the deployment's default framework."""
    value = (raw or settings.default_framework_slug or "").strip()
    if not value:
        raise DomainError(status_code=400, detail="framework is required")
    if not _SLUG_RE.fullmatch(value):
        raise DomainError(status_code=400, detail="Invalid framework")
    return value


def list_frameworks(*, db: Session):
    catalog, _, _, enabled, default = _framework_selection(db=db)
    return {
        "items": [
            {**item, "is_default": item["slug"] == default}
            for item in catalog["items"]
            if item["slug"] in enabled
        ],
        "default": default,
    }


def framework_selection(*, db: Session):
    catalog, row, permitted, enabled, default = _framework_selection(db=db)
    return {
        "items": [
            {
                **item,
                "enabled": item["slug"] in enabled,
                "allowed": not permitted or item["slug"] in permitted,
            }
            for item in catalog["items"]
        ],
        "default": default,
        "version": row.version if row else 0,
        "environment_restricted": bool(permitted),
        "unknown_environment_slugs": sorted(
            permitted - {item["slug"] for item in catalog["items"]}
        ),
    }


class FrameworkSelectionInput(BaseModel):
    enabled: list[str] = Field(min_length=1, max_length=500)
    default: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=0)


def save_framework_selection(*, payload: FrameworkSelectionInput, user, db: Session):
    catalog, _, permitted, _, _ = _framework_selection(db=db)
    known = {item["slug"] for item in catalog["items"]}
    selected = set(payload.enabled)
    if len(selected) != len(payload.enabled) or not selected <= known:
        raise DomainError(400, "Choose distinct frameworks from the catalogue")
    if permitted and not selected <= permitted:
        raise DomainError(
            400, "A selected framework is excluded by KEEN_ENABLED_FRAMEWORKS"
        )
    if payload.default not in selected:
        raise DomainError(400, "The default framework must be selected")
    from app.services.managed_configurations import _save

    _save(
        db,
        "organisation-frameworks",
        {"enabled": sorted(selected), "default": payload.default},
        payload.version,
        user,
    )
    return framework_selection(db=db)


def upsert_framework(*, payload: FrameworkUpsert, db: Session):
    slug = (payload.slug or "").strip()
    if not slug:
        raise DomainError(status_code=400, detail="slug is required")
    validate_framework_slug(slug)

    row = db.query(Framework).filter(Framework.slug == slug).one_or_none()
    created = False
    if row is None:
        row = Framework(slug=slug)
        db.add(row)
        created = True

    row.name = (payload.name or slug).strip() if payload.name is not None else slug
    row.version = (payload.version or "").strip() or None
    row.description = (payload.description or "").strip() or None
    row.upstream_url = (payload.upstream_url or "").strip() or None

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
    if value and (
        urlsplit(value).scheme not in ("http", "https") or not urlsplit(value).netloc
    ):
        raise DomainError(400, "Upstream URL must be an absolute HTTP(S) URL")
    return value


def _node(
    row: ControlItem,
    clauses: dict[str, FrameworkClause],
    links: dict[uuid.UUID, list[str]],
) -> dict:
    meta = row.meta or {}
    return {
        "id": str(row.id),
        "kind": row.type,
        "ref": row.ref,
        "title": row.title or "",
        "description": meta.get("description") or "",
        "upstream_url": meta.get("upstream_url") or "",
        "parent_ref": (
            clauses[row.ref].parent.ref
            if row.type == "clause" and row.ref in clauses and clauses[row.ref].parent
            else meta.get("parent_ref") or ""
        ),
        "clause_refs": links.get(row.id, []),
        "in_scope": row.in_scope,
        "sort_order": clauses[row.ref].sort_order
        if row.type == "clause" and row.ref in clauses
        else meta.get("sort_order", 0),
    }


def framework_nodes(*, slug: str, db: Session):
    rows = db.query(ControlItem).filter(ControlItem.framework_slug == slug).all()
    clauses = {
        c.ref: c
        for c in db.query(FrameworkClause)
        .filter(FrameworkClause.framework_slug == slug)
        .all()
    }
    links = {}
    for link in (
        db.query(ControlClauseLink)
        .join(ControlItem)
        .filter(ControlItem.framework_slug == slug)
        .all()
    ):
        if link.clause:
            links.setdefault(link.control_item_id, []).append(link.clause.ref)
    return {"items": [_node(row, clauses, links) for row in rows]}


def save_framework_node(
    *, slug: str, kind: str, ref: str, payload: FrameworkNodeInput, db: Session
):
    kind, ref = kind.strip(), ref.strip()
    if (
        kind != payload.kind.strip()
        or ref != payload.ref.strip()
        or not kind
        or not ref
    ):
        raise DomainError(400, "Node kind and reference must match the URL")
    if (
        len(kind) > 32
        or len(ref) > 64
        or not payload.title.strip()
        or len(payload.title) > 256
    ):
        raise DomainError(400, "Invalid kind, reference or title")
    framework_exists = bool(
        db.query(Framework.id).filter(Framework.slug == slug).first()
    )
    if (
        not framework_exists
        and slug != settings.default_framework_slug
        and not db.query(ControlItem.id).filter_by(framework_slug=slug).first()
    ):
        raise DomainError(404, "Create the framework first")
    parent_ref = (payload.parent_ref or "").strip()
    if parent_ref == ref:
        raise DomainError(400, "A node cannot be its own parent")
    parent = None
    if parent_ref:
        parent = (
            db.query(ControlItem)
            .filter_by(framework_slug=slug, type=kind, ref=parent_ref)
            .one_or_none()
        )
        if not parent:
            raise DomainError(
                400, "Parent must exist in the same framework and category"
            )
        seen = {ref}
        cursor = parent
        while cursor:
            if cursor.ref in seen:
                raise DomainError(400, "Parent relationship would form a cycle")
            seen.add(cursor.ref)
            next_ref = (cursor.meta or {}).get("parent_ref")
            cursor = (
                db.query(ControlItem)
                .filter_by(framework_slug=slug, type=kind, ref=next_ref)
                .one_or_none()
                if next_ref
                else None
            )
    clause_rows = []
    if kind != "clause":
        for clause_ref in dict.fromkeys(payload.clause_refs):
            clause = (
                db.query(FrameworkClause)
                .filter_by(framework_slug=slug, ref=clause_ref)
                .one_or_none()
            )
            if not clause:
                raise DomainError(400, f"Unknown clause: {clause_ref}")
            clause_rows.append(clause)
    elif payload.clause_refs:
        raise DomainError(400, "Only controls can link to clauses")
    url = _url(payload.upstream_url)
    # A framework known only from its controls gains its row when its first
    # edited node is saved, in the same transaction.
    if not framework_exists:
        db.add(Framework(slug=slug, name=slug))
    row = (
        db.query(ControlItem)
        .filter_by(framework_slug=slug, type=kind, ref=ref)
        .one_or_none()
    )
    created = row is None
    if created:
        row = ControlItem(framework_slug=slug, type=kind, ref=ref)
        db.add(row)
    row.title = payload.title.strip()
    row.in_scope = payload.in_scope
    row.meta = {
        **(row.meta or {}),
        "description": payload.description or "",
        "upstream_url": url,
        "parent_ref": parent_ref or None,
        "sort_order": payload.sort_order,
    }
    db.flush()
    if kind == "clause":
        clause = (
            db.query(FrameworkClause)
            .filter_by(framework_slug=slug, ref=ref)
            .one_or_none()
        )
        if clause is None:
            clause = FrameworkClause(framework_slug=slug, ref=ref)
            db.add(clause)
        clause.title = row.title
        clause.parent_clause_id = (
            db.query(FrameworkClause.id)
            .filter_by(framework_slug=slug, ref=parent_ref)
            .scalar()
            if parent_ref
            else None
        )
        clause.sort_order = payload.sort_order
        clause.meta = {**(clause.meta or {}), **row.meta}
    else:
        db.query(ControlClauseLink).filter_by(control_item_id=row.id).delete()
        for clause in clause_rows:
            db.add(
                ControlClauseLink(
                    control_item_id=row.id,
                    clause_id=clause.id,
                    applicability="applicable",
                )
            )
    db.commit()
    return {"ok": True, "created": created, "id": str(row.id)}


def delete_framework_node(*, slug: str, kind: str, ref: str, db: Session):
    row = (
        db.query(ControlItem)
        .filter_by(framework_slug=slug, type=kind, ref=ref)
        .one_or_none()
    )
    if not row:
        raise DomainError(404, "Node not found")
    children = db.query(ControlItem).filter_by(framework_slug=slug, type=kind).all()
    if any(
        (child.meta or {}).get("parent_ref") == ref and child.id != row.id
        for child in children
    ):
        raise DomainError(409, "Move or delete child nodes first")
    clause = (
        db.query(FrameworkClause).filter_by(framework_slug=slug, ref=ref).one_or_none()
        if kind == "clause"
        else None
    )
    if clause:
        if db.query(FrameworkClause.id).filter_by(parent_clause_id=clause.id).first():
            raise DomainError(409, "Move or delete child clauses first")
        if db.query(ControlClauseLink.clause_id).filter_by(clause_id=clause.id).first():
            raise DomainError(409, "Remove control links first")
        db.delete(clause)
    # Keep related audit/mapping data intact unless the administrator has
    # explicitly removed the relationships first.
    if (
        db.query(ControlClauseLink.control_item_id)
        .filter_by(control_item_id=row.id)
        .first()
    ):
        raise DomainError(409, "Remove clause links first")
    if db.query(Mapping.id).filter_by(control_item_id=row.id).first():
        raise DomainError(409, "Remove event mappings before deleting this node")
    db.delete(row)
    db.commit()
    return {"ok": True}
