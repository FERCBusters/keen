from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.cache import cached_json, user_cache_scope
from app.db.models import (
    Event,
    ControlItem,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    Mapping,
    User,
)
from app.db.session import get_db
from app.core.source_meta import get_source_meta, apply_user_source_overrides
from app.security.diary_visibility import diary_filter_condition
from app.security.permissions import has_permission
from app.security.roles import is_effective_admin
from app.api.routes.isms import (
    _clean_framework,
    _effectiveness_measure_out,
    require_isms_read,
)
from app.core.config import settings
from app.services.control_inheritance import effective_framework_ids

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


def _source_overrides(user) -> dict | None:
    return getattr(user, "pref_source_colors", None) if isinstance(user, User) else None


@router.get("/v1/sources")
def list_sources(
    request: Request,
    framework: str = settings.default_framework_slug,
    limit: int = 5000,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    limit = max(1, min(int(limit or 5000), 5000))
    offset = max(0, int(offset or 0))
    user = _require_events_read(db, request)
    cache_parts = {
        "user": user_cache_scope(user),
        "framework": framework,
        "counter_semantics": "framework-mapped-events-v1",
        # Include source colour preferences so changing badge colours does not have
        # to wait for the aggregate cache TTL.
        "source_colors": _source_overrides(user) or {},
        "limit": limit,
        "offset": offset,
    }

    def _load() -> dict:
        visibility = diary_filter_condition(db, user)

        # Total events per source. This is used by sources.html and, historically,
        # by some dropdowns, so keep the exact count semantics intact.
        total_rows = (
            db.query(Event.source, func.count(Event.id), func.max(Event.timestamp))
            .filter(visibility)
            .group_by(Event.source)
            .order_by(Event.source.asc())
            .all()
        )

        # Count each mapped event once per source and per selected framework, even
        # if the event maps to more than one control in that framework. An event
        # mapped only in another framework must still be reported as unmapped for
        # this framework.
        mapped_event_ids = (
            db.query(Mapping.event_id.label("event_id"))
            .filter(Mapping.control_item_id.in_(effective_framework_ids(framework)))
            .distinct()
            .subquery()
        )
        mapped_rows = (
            db.query(Event.source, func.count(Event.id))
            .join(mapped_event_ids, mapped_event_ids.c.event_id == Event.id)
            .filter(visibility)
            .group_by(Event.source)
            .all()
        )
        mapped_by_source = {src: int(n) for src, n in mapped_rows}

        items = []
        overrides = _source_overrides(user)
        for src, total, last_seen in total_rows:
            default_meta = get_source_meta(src)
            meta = apply_user_source_overrides(default_meta, overrides)
            mapped = mapped_by_source.get(src, 0)
            items.append(
                {
                    "source": src,
                    "label": meta.label,
                    "color": meta.color,
                    "total_events": int(total),
                    "mapped_events": mapped,
                    "unmapped_events": int(total) - mapped,
                    "last_seen": last_seen.isoformat() if last_seen else None,
                }
            )
        total = len(items)
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": items[offset : offset + limit],
        }

    return cached_json("sources:list", cache_parts, _load)


@router.get("/v1/sources/meta")
def list_source_meta(request: Request, db: Session = Depends(get_db)):
    user = _require_events_read(db, request)
    """Return per-source UI display metadata.

    This is intentionally lightweight for UI pages that only need labels/colors.
    """

    cache_parts = {
        "user": user_cache_scope(user),
        "source_colors": _source_overrides(user) or {},
    }

    def _load() -> dict:
        rows = (
            db.query(Event.source)
            .filter(diary_filter_condition(db, user))
            .group_by(Event.source)
            .order_by(Event.source.asc())
            .all()
        )
        items = []
        overrides = _source_overrides(user)
        for (src,) in rows:
            default_meta = get_source_meta(src)
            meta = apply_user_source_overrides(default_meta, overrides)
            items.append(
                {
                    "source": src,
                    "label": meta.label,
                    "color": meta.color,
                    "default_color": default_meta.color,
                    "overridden": meta.color != default_meta.color,
                }
            )
        return {"items": items}

    return cached_json("sources:meta", cache_parts, _load)


@router.get("/v1/sources/{source_name}/effectiveness-measures")
def source_effectiveness_measures(
    source_name: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    """Return effectiveness measures with metric entries recorded against a KEEN source."""
    fw = _clean_framework(framework)
    src = str(source_name or "").strip()
    if not src:
        return {"framework": fw, "source": src, "items": []}
    rows = (
        db.query(IsmsEffectivenessMeasure)
        .join(
            IsmsEffectivenessMetricEntry,
            IsmsEffectivenessMetricEntry.measure_id == IsmsEffectivenessMeasure.id,
        )
        .filter(
            IsmsEffectivenessMeasure.framework_slug == fw,
            IsmsEffectivenessMetricEntry.source_type == src,
        )
        .distinct()
        .order_by(IsmsEffectivenessMeasure.updated_at.desc())
        .all()
    )
    return {
        "framework": fw,
        "source": src,
        "items": [_effectiveness_measure_out(db, row, fw) for row in rows],
    }
