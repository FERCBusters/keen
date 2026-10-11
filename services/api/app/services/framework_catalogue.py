"""Framework catalogue and enabled selection shared by collection and HTTP."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import ControlItem, Framework, FrameworkClause, ManagedConfiguration


class FrameworkListItem(BaseModel):
    slug: str
    name: str
    version: str | None = None
    description: str | None = None
    upstream_url: str | None = None
    has_clauses: bool = False
    control_count: int = 0
    is_default: bool = False


def _framework_catalog(db: Session):
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

    clause_slugs = {
        slug for (slug,) in db.query(FrameworkClause.framework_slug).distinct().all()
    }

    rows = db.query(Framework).order_by(Framework.slug.asc()).all()
    by_slug = {f.slug: f for f in rows}

    all_slugs = set(by_slug.keys()) | set(control_counts.keys()) | clause_slugs
    # Catalogue rows are authoritative; stale environment names must not create ghosts.

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
                has_clauses=slug in clause_slugs,
                control_count=control_counts.get(slug, 0),
                is_default=(slug == settings.default_framework_slug),
            )
        )

    return {
        "items": [i.model_dump() for i in items],
        "default": settings.default_framework_slug,
    }


def _framework_selection(db):
    catalog = _framework_catalog(db)
    known = {item["slug"] for item in catalog["items"]}
    permitted = {
        slug.strip() for slug in settings.enabled_frameworks.split(",") if slug.strip()
    }
    eligible = known & permitted if permitted else known
    row = db.get(ManagedConfiguration, "organisation-frameworks")
    configured = row.document.get("enabled") if row else None
    enabled = eligible & set(configured) if configured is not None else eligible
    if not enabled:
        # Keep one usable framework if an operator changes the environment restriction.
        # Never reintroduce a framework excluded by the environment.
        enabled = eligible
    preferred = row.document.get("default") if row else None
    default = next(
        (
            slug
            for slug in [preferred, settings.default_framework_slug]
            if slug in enabled
        ),
        min(enabled) if enabled else "",
    )
    return catalog, row, permitted, enabled, default
