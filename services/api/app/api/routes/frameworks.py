from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.payloads import FrameworkListItem, FrameworkUpsert
from app.core.config import settings
from app.db.models import ControlItem, Framework
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
