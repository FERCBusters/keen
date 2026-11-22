"""Controls application operations."""

from __future__ import annotations

from typing import Any

from myf.core.datetime_utils import iso_or_none
from myf.core.identifiers import try_uuid as _try_uuid
from myf.services.errors import DomainError
from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from app.core.cache import cached_json, user_cache_scope
from app.core.config import settings
from app.core.references import control_upstream_url as _control_upstream_url
from app.core.references import ref_sort_key as _ref_sort_key
from app.db.models import (
    ControlClauseLink,
    ControlItem,
    Event,
    FrameworkClause,
)
from app.security.diary_visibility import (
    DIARY_SOURCE,
    LINK_ENTRY_PREFIX,
    LINK_ENTRY_SYSTEM,
    diary_filter_condition,
)
from app.services.control_owners import owners_of
from app.services.control_refs import control_summary
from app.services.entity_changelog import list_entity_changelogs
from app.services.event_filters import event_predicates, parse_event_filters


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
        "owners": owners_of(db, control_id=c.id),
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
        sources: list[dict[str, Any]] = [
            {
                "source": source or "",
                "count": int(count),
                "latest": iso_or_none(latest),
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
