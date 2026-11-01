"""Controls application operations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, case, desc, func
from sqlalchemy.orm import Session, load_only

from app.core.cache import cached_json, user_cache_scope
from app.core.config import settings
from app.core.references import control_upstream_url as _control_upstream_url
from app.core.references import ref_sort_key as _ref_sort_key
from app.core.references import try_uuid as _try_uuid
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    Event,
    FrameworkClause,
    Mapping,
)
from app.security.diary_visibility import (
    DIARY_SOURCE,
    LINK_ENTRY_PREFIX,
    LINK_ENTRY_SYSTEM,
    diary_filter_condition,
)
from app.security.redaction import redact_obj as _redact_obj
from app.security.redaction import redact_str as _redact_str
from app.services.control_inheritance import inherited_control_ids
from app.services.control_refs import control_summary
from app.services.entity_changelog import list_entity_changelogs
from app.services.errors import DomainError
from app.services.event_filters import event_predicates, parse_event_filters
from app.services.presentation import extract_source_url as _extract_source_url


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


def list_controls(
    *,
    framework: str = settings.default_framework_slug,
    limit: int = 500,
    offset: int = 0,
    db: Session,
):
    limit = max(1, min(int(limit or 500), 5000))
    offset = max(0, int(offset or 0))
    rows = db.query(ControlItem).filter(ControlItem.framework_slug == framework).all()
    items = [control_summary(c) for c in rows]
    items.sort(key=lambda it: (it["type"], _ref_sort_key(it["ref"])))
    total = len(items)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items[offset : offset + limit],
    }


def get_control(*, control_id: str, db: Session):
    cid = _try_uuid(control_id)
    if not cid:
        raise DomainError(status_code=400, detail="control_id must be a UUID")
    c = db.query(ControlItem).filter(ControlItem.id == cid).one_or_none()
    if not c:
        raise DomainError(status_code=404, detail="Control not found")
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
        **control_summary(c),
        "tags": c.tags,
        "metadata": c.meta,
        "clauses": clauses,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def control_changelog(
    *, control_id: str, limit: int = 50, offset: int = 0, db: Session
):
    cid = _try_uuid(control_id)
    if not cid:
        raise DomainError(status_code=400, detail="control_id must be a UUID")
    exists = db.query(ControlItem.id).filter(ControlItem.id == cid).first()
    if not exists:
        raise DomainError(status_code=404, detail="Control not found")
    return list_entity_changelogs(
        db, entity_type="control", entity_id=cid, limit=limit, offset=offset
    )


def control_evidence(*, control_id: str, limit: int, offset: int, db: Session, user):
    cid = _try_uuid(control_id)
    if not cid:
        raise DomainError(status_code=400, detail="control_id must be a UUID")

    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))

    if not db.get(ControlItem, cid):
        raise DomainError(status_code=404, detail="Control not found")

    # Prefer a direct mapping when the same event also arrives via another
    # framework. Rank before pagination to avoid duplicates and empty pages.
    ranked = (
        db.query(
            Mapping.id.label("mapping_id"),
            func.row_number()
            .over(
                partition_by=Mapping.event_id,
                order_by=(
                    case((Mapping.control_item_id == cid, 0), else_=1),
                    Mapping.mapped_at.desc(),
                ),
            )
            .label("rank"),
        )
        .filter(Mapping.control_item_id.in_(inherited_control_ids(db, [cid])))
        .subquery()
    )
    base_q = (
        db.query(Mapping)
        .join(ranked, ranked.c.mapping_id == Mapping.id)
        .join(Event, Event.id == Mapping.event_id)
        .filter(ranked.c.rank == 1, diary_filter_condition(db, user))
    )
    total = int(base_q.order_by(None).count() or 0)
    q = base_q.order_by(desc(Mapping.mapped_at)).offset(offset).limit(limit).all()
    event_ids = [m.event_id for m in q]

    source_ids = {m.control_item_id for m in q if m.control_item_id != cid}
    sources = {
        c.id: c
        for c in db.query(ControlItem).filter(ControlItem.id.in_(source_ids)).all()
    }

    events = {
        e.id: e
        for e in db.query(Event)
        .options(
            load_only(
                Event.id,
                Event.timestamp,
                Event.source,
                Event.connection_id,
                Event.connection_name,
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
                "connection_id": e.connection_id,
                "connection_name": e.connection_name,
                "summary": e.summary,
                "source_url": src_url,
                "raw_pointer": _redact_obj(e.raw_pointer),
                "confidence": m.confidence,
                "method": m.method,
                "rationale": m.rationale,
                "inherited": m.control_item_id != cid,
                "inherited_from": (
                    {
                        "control_id": str(sources[m.control_item_id].id),
                        "framework": sources[m.control_item_id].framework_slug,
                        "ref": sources[m.control_item_id].ref,
                    }
                    if m.control_item_id in sources
                    else None
                ),
            }
        )
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def control_evidence_summary(*, control_id: str, db: Session, user) -> dict:
    """A control's evidence counted by source, with the newest event from each.

    The counts follow the control's evidence list: its own and inherited
    evidence, with diary entries the caller can see. Automatic diary entries
    recorded when a record is linked to the control are also counted as link
    entries.
    """
    cid = _try_uuid(control_id)
    if not cid:
        raise DomainError(status_code=400, detail="control_id must be a UUID")
    control = db.get(ControlItem, cid)
    if not control:
        raise DomainError(status_code=404, detail="Control not found")
    framework = control.framework_slug

    def _load() -> dict:
        filters = parse_event_filters(framework=framework, control=str(cid))
        link_entry = and_(
            Event.source == DIARY_SOURCE,
            Event.system == LINK_ENTRY_SYSTEM,
            Event.external_id.startswith(LINK_ENTRY_PREFIX, autoescape=True),
        )
        # The evidence predicates are semi-joins on events, so each event is
        # counted once without joining the events table to itself; the planner
        # then reads only the control's events instead of the whole table.
        source_rows = (
            db.query(
                Event.source,
                func.count(Event.id),
                func.max(Event.timestamp),
                func.sum(case((link_entry, 1), else_=0)),
            )
            .filter(diary_filter_condition(db, user))
            .filter(*event_predicates(filters, db))
            .group_by(Event.source)
            .all()
        )
        sources = [
            {
                "source": source or "",
                "count": int(count),
                "latest": _iso(latest),
                "link_entries": int(links or 0),
            }
            for source, count, latest, links in source_rows
        ]
        sources.sort(key=lambda row: (-row["count"], row["source"]))
        return {
            "control_id": str(cid),
            "framework": framework,
            "total": sum(row["count"] for row in sources),
            "latest": max(
                (row["latest"] for row in sources if row["latest"]), default=None
            ),
            "sources": sources,
        }

    return cached_json(
        "controls:evidence-summary",
        {"control": str(cid), "user": user_cache_scope(user), "version": 1},
        _load,
    )
