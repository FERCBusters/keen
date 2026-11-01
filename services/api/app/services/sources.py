"""Sources application operations."""

from __future__ import annotations

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, aliased

from app.core.cache import cached_json, user_cache_scope
from app.core.config import settings
from app.core.source_meta import apply_user_source_overrides, get_source_meta
from app.db.models import (
    Event,
    IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry,
    Mapping,
    User,
)
from app.security.diary_visibility import diary_filter_condition
from app.services.control_inheritance import effective_framework_ids
from app.services.frameworks import framework_parameter
from app.services.isms.serializers import _effectiveness_measure_out


def _source_overrides(user) -> dict | None:
    return getattr(user, "pref_source_colors", None) if isinstance(user, User) else None


def source_effectiveness_measures(
    *,
    source_name: str,
    framework: str = settings.default_framework_slug,
    user,
    db: Session,
):
    """Return effectiveness measures with metric entries recorded against a Keen source."""
    fw = framework_parameter(framework)
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


def list_sources(*, framework: str, limit: int, offset: int, db: Session, user):
    limit = max(1, min(int(limit or 5000), 5000))
    offset = max(0, int(offset or 0))
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

        # Total events per source, counted exactly as sources.html shows them.
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
        # The short list of the framework's evidence controls is resolved
        # first; a semi-join then counts each event once.
        control_ids = sorted(
            db.execute(effective_framework_ids(framework, db)).scalars(), key=str
        )
        mapping = aliased(Mapping)
        mapped_rows = (
            db.query(Event.source, func.count(Event.id))
            .filter(
                exists().where(
                    mapping.event_id == Event.id,
                    mapping.control_item_id.in_(control_ids),
                )
            )
            .filter(visibility)
            .group_by(Event.source)
            .all()
            if control_ids
            else []
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


def _visible_sources(db: Session, user) -> list[str]:
    """Distinct sources of the events the caller can see, in order.

    A recursive skip scan reads one index entry per source, so the cost follows
    the number of sources and never the number of events. A source is listed
    when at least one of its events passes the caller's diary visibility.
    """
    step = aliased(Event)
    sources = select(func.min(Event.source).label("source")).cte(
        "event_sources", recursive=True
    )
    sources = sources.union_all(
        select(
            select(func.min(step.source))
            .where(step.source > sources.c.source)
            .scalar_subquery()
        ).where(sources.c.source.is_not(None))
    )
    query = select(sources.c.source).where(sources.c.source.is_not(None))
    visibility = diary_filter_condition(db, user)
    if visibility is not True:
        query = query.where(
            exists().where(Event.source == sources.c.source, visibility)
        )
    return list(db.execute(query.order_by(sources.c.source)).scalars())


def list_source_meta(*, db: Session, user):
    """Return per-source UI display metadata.

    This is intentionally lightweight for UI pages that only need labels/colors.
    """

    cache_parts = {
        "user": user_cache_scope(user),
        "source_colors": _source_overrides(user) or {},
    }

    def _load() -> dict:
        items = []
        overrides = _source_overrides(user)
        for src in _visible_sources(db, user):
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
