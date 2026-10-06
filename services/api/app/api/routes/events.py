from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, exists, false, func, or_, select, text
from sqlalchemy.orm import Session, load_only

from app.core.config import settings
from app.api.payloads import IncidentCreatePayload
from app.core.cache import cached_json, user_cache_scope
from app.api.utils import (
    control_upstream_url as _control_upstream_url,
    event_identifier as _event_identifier,
    extract_source_url as _extract_source_url,
    try_uuid as _try_uuid,
)
from app.db.models import (
    Artifact,
    AuditLog,
    ControlClauseLink,
    ControlItem,
    CrossFrameworkControlLink,
    Event,
    EventIncident,
    FrameworkClause,
    Mapping,
    User,
)
from app.db.session import get_db
from app.services.control_inheritance import evidence_pairs, effective_framework_ids, event_has_control, source_ids_for_control
from app.outbound.incidents import (
    IncidentWebhookConfig,
    build_event_url,
    build_incident_payload,
    incident_webhook_enabled,
    post_incident_webhook,
    sanitize_plain_text,
)
from app.security.diary_visibility import diary_filter_condition, is_diary_event_visible
from app.security.permissions import has_permission
from app.security.roles import is_effective_admin
from app.security.redaction import (
    mask_event_data_obj as _mask_event_data_obj,
    mask_event_data_str as _mask_event_data_str,
    redact_obj as _redact_obj,
    redact_str as _redact_str,
)

router = APIRouter()

INCIDENT_CREATE_PERMISSION = "incident.create"
INCIDENT_DELETE_PERMISSION = "incident.delete"
EVENTS_READ_PERMISSION = "events.read"


def _mask_event_data_for_sample_exports(sample_export: bool) -> bool:
    return bool(sample_export) and (settings.event_data_masking or "false") in {
        "samples",
        "true",
    }


def _maybe_mask_event_sample_value(value: Any, sample_export: bool) -> Any:
    if not _mask_event_data_for_sample_exports(sample_export):
        return value
    if isinstance(value, str) or value is None:
        return _mask_event_data_str(value)
    return _mask_event_data_obj(value)


def _client_ip(request: Request) -> str | None:
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip() or None
    xri = (request.headers.get("x-real-ip") or "").strip()
    if xri:
        return xri or None
    try:
        return request.client.host if request.client else None
    except Exception:
        return None


def _audit_action(
    db: Session,
    request: Request,
    user: User | None,
    action: str,
    meta: dict[str, Any] | None = None,
) -> None:
    qs = None
    if meta:
        try:
            from urllib.parse import urlencode

            qs = urlencode({k: str(v) for k, v in meta.items() if v is not None})
        except Exception:
            qs = None

    ua = (request.headers.get("user-agent") or "").strip() or None
    ref = (request.headers.get("referer") or "").strip() or None
    if ua and len(ua) > 256:
        ua = ua[:256]
    if ref and len(ref) > 512:
        ref = ref[:512]

    db.add(
        AuditLog(
            ts=datetime.utcnow(),
            username=getattr(user, "username", None) if user else None,
            method="ACTION",
            path=f"/action/{action}",
            query_string=qs,
            status_code=200,
            duration_ms=0,
            client_ip=_client_ip(request),
            user_agent=ua,
            referer=ref,
        )
    )


def _event_visible_or_404(db: Session, request: Request, event_id: uuid.UUID) -> Event:
    event = db.query(Event).filter(Event.id == event_id).one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    user = getattr(request.state, "user", None)
    if event.source == "diary" and not is_diary_event_visible(db, user, event_id):
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _require_events_read(db: Session, request: Request) -> User:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not is_effective_admin(db, user) and not has_permission(
        db, user, EVENTS_READ_PERMISSION
    ):
        raise HTTPException(status_code=403, detail="events.read permission required")
    return user


def _incident_to_dict(incident: EventIncident) -> dict[str, Any]:
    return {
        "id": str(incident.id),
        "event_id": str(incident.event_id),
        "title": incident.title,
        "text": incident.text,
        "event_url": incident.event_url,
        "created_by": getattr(incident.created_by, "username", None),
        "created_at": incident.created_at.isoformat() if incident.created_at else None,
    }


def _norm_ts(ts: datetime) -> datetime:
    """Normalise a timestamp query param into a naive UTC datetime.

    The DB stores timestamps as naive DateTime (assumed UTC). FastAPI may parse
    ISO8601 strings with tzinfo (e.g. trailing 'Z'). Convert those to UTC and
    drop tzinfo so SQLAlchemy comparisons are consistent.
    """

    if ts.tzinfo is not None:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


def _event_time_filters(
    start_date: date | None,
    end_date: date | None,
    start_ts: datetime | None = None,
    end_ts: datetime | None = None,
):
    """Build SQLAlchemy filters for Event.timestamp.

    Supports either:
      - day granularity (start_date/end_date), OR
      - precise timestamps (start_ts/end_ts).

    Semantics:
      - start_date is inclusive at 00:00:00 UTC
      - end_date is inclusive (implemented as < end_date+1 day at 00:00:00 UTC)
      - start_ts is inclusive
      - end_ts is exclusive
    """

    filters = []
    if start_ts is not None:
        filters.append(Event.timestamp >= _norm_ts(start_ts))
    elif start_date:
        filters.append(
            Event.timestamp >= datetime.combine(start_date, datetime.min.time())
        )

    if end_ts is not None:
        filters.append(Event.timestamp < _norm_ts(end_ts))
    elif end_date:
        end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
        filters.append(Event.timestamp < end_excl)
    return filters


def _event_text_search_condition(q: str | None):
    """Return the event free-text search predicate.

    The existing UI semantics are substring-style search across event display fields.
    We keep that behaviour via pg_trgm-backed ILIKE predicates, and add a PostgreSQL
    full-text predicate that can use the combined GIN index created by migration 0029.
    This avoids pushing users to a sidecar search service for the common evidence
    lookup path.
    """

    term = str(q or "").strip()
    if not term:
        return None

    # Sanitize input to prevent SQL injection via FTS
    # Remove characters that could break out of plainto_tsquery
    # plainto_tsquery treats special characters ( &:|!<> ) as operators
    # We escape them to ensure literal search
    import re

    sanitized_term = re.sub(r"[&|!<>]", "", term)

    like = f"%{sanitized_term}%"
    fts = text(
        "to_tsvector('simple', "
        "coalesce(events.summary, '') || ' ' || "
        "coalesce(events.action, '') || ' ' || "
        "coalesce(events.system, '') || ' ' || "
        "coalesce(events.actor, '') || ' ' || "
        "coalesce(events.source, '') || ' ' || "
        "coalesce(events.outcome, '') || ' ' || "
        "coalesce(events.external_id, '')"
        ") @@ plainto_tsquery('simple', :keen_event_q)"
    ).bindparams(keen_event_q=sanitized_term)

    return or_(
        fts,
        Event.summary.ilike(like),
        Event.action.ilike(like),
        Event.system.ilike(like),
        Event.actor.ilike(like),
        Event.source.ilike(like),
        Event.outcome.ilike(like),
        Event.external_id.ilike(like),
    )


def _unmapped_event_condition(framework: str):
    """Return a NOT EXISTS predicate for events without framework mappings.

    Keep this anti-join focused on ``mappings`` by using a small framework-control
    subquery. In production datasets the events table is much larger than the
    framework controls list, and this shape lets Postgres use the existing
    event/control mapping index instead of repeatedly joining ``control_items``
    inside the correlated EXISTS check.
    """

    framework_control_ids = effective_framework_ids(framework)
    return (
        ~exists()
        .where(Mapping.event_id == Event.id)
        .where(Mapping.control_item_id.in_(framework_control_ids))
    )


def _clause_event_condition(framework: str, clause: str):
    """Return an EXISTS predicate for events mapped to controls linked to a clause.

    `clause` may be a FrameworkClause UUID or a clause ref such as ``5.1``.
    The condition is framework-scoped so refs from another framework cannot leak in.
    """

    cid = _try_uuid(clause)
    cond = (
        exists()
        .where(Mapping.event_id == Event.id)
        .where(or_(Mapping.control_item_id == ControlItem.id,
                   Mapping.control_item_id.in_(source_ids_for_control(ControlItem.id).correlate(ControlItem))))
        .where(ControlClauseLink.control_item_id == ControlItem.id)
        .where(ControlClauseLink.clause_id == FrameworkClause.id)
        .where(ControlItem.framework_slug == framework)
        .where(FrameworkClause.framework_slug == framework)
    )
    if cid:
        cond = cond.where(FrameworkClause.id == cid)
    else:
        cond = cond.where(FrameworkClause.ref == str(clause or "").strip())
    return cond


def _build_event_id_query(
    db: Session,
    *,
    framework: str,
    q: str | None = None,
    source: str | None = None,
    system: str | None = None,
    actor: str | None = None,
    control: str | None = None,
    clause: str | None = None,
    unmapped: bool = False,
    start_date: date | None = None,
    end_date: date | None = None,
    start_ts: datetime | None = None,
    end_ts: datetime | None = None,
    user: User | None = None,
):
    """Return a query that yields distinct Event IDs for the given filters."""

    qry = db.query(Event.id)
    # RBAC: hide diary events the caller is not permitted to see.
    qry = qry.filter(diary_filter_condition(db, user))
    qry = qry.filter(*_event_time_filters(start_date, end_date, start_ts, end_ts))

    if source:
        qry = qry.filter(Event.source == source)

    if system:
        qry = qry.filter(Event.system.ilike(f"%{system}%"))

    if actor:
        qry = qry.filter(Event.actor.ilike(f"%{actor}%"))

    search_cond = _event_text_search_condition(q)
    if search_cond is not None:
        qry = qry.filter(search_cond)

    if control:
        cid = _try_uuid(control)
        if cid:
            valid = db.query(ControlItem.id).filter(ControlItem.id == cid, ControlItem.framework_slug == framework).first()
            qry = qry.filter(event_has_control(cid, Event.id) if valid else false())
        else:
            c = (
                db.query(ControlItem)
                .filter(
                    ControlItem.framework_slug == framework, ControlItem.ref == control
                )
                .one_or_none()
            )
            if not c:
                qry = qry.filter(false())
            else:
                qry = qry.filter(event_has_control(c.id, Event.id))

    if clause:
        qry = qry.filter(_clause_event_condition(framework, clause))

    if unmapped:
        qry = qry.filter(_unmapped_event_condition(framework))

    return qry.distinct()


@router.get("/v1/events")
def list_events(
    request: Request,
    framework: str = settings.default_framework_slug,
    q: Optional[str] = None,
    source: Optional[str] = None,
    system: Optional[str] = None,
    actor: Optional[str] = None,
    control: Optional[str] = None,  # UUID or ref
    clause: Optional[str] = None,  # UUID or ref
    unmapped: bool = False,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))

    qry = db.query(Event)

    user = _require_events_read(db, request)
    qry = qry.filter(diary_filter_condition(db, user))
    qry = qry.filter(*_event_time_filters(start_date, end_date, start_ts, end_ts))

    if source:
        qry = qry.filter(Event.source == source)

    if system:
        qry = qry.filter(Event.system.ilike(f"%{system}%"))

    if actor:
        qry = qry.filter(Event.actor.ilike(f"%{actor}%"))

    search_cond = _event_text_search_condition(q)
    if search_cond is not None:
        qry = qry.filter(search_cond)

    if control:
        cid = _try_uuid(control)
        if cid:
            valid = db.query(ControlItem.id).filter(ControlItem.id == cid, ControlItem.framework_slug == framework).first()
            qry = qry.filter(event_has_control(cid, Event.id) if valid else false())
        else:
            c = (
                db.query(ControlItem)
                .filter(
                    ControlItem.framework_slug == framework, ControlItem.ref == control
                )
                .one_or_none()
            )
            if not c:
                return {"total": 0, "limit": limit, "offset": offset, "items": []}
            qry = qry.filter(event_has_control(c.id, Event.id))

    if clause:
        qry = qry.filter(_clause_event_condition(framework, clause))

    if unmapped:
        qry = qry.filter(_unmapped_event_condition(framework))

    cache_parts = {
        "framework": framework,
        "q": q,
        "source": source,
        "system": system,
        "actor": actor,
        "control": control,
        "clause": clause,
        "unmapped": bool(unmapped),
        "start_date": start_date,
        "end_date": end_date,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "limit": limit,
        "offset": offset,
        "user": user_cache_scope(user),
        "query_version": "inherited-controls-v1",
    }

    def _load_page() -> dict[str, Any]:
        count_cache_parts = dict(cache_parts)
        count_cache_parts.pop("limit", None)
        count_cache_parts.pop("offset", None)
        total = cached_json(
            "events:list:count",
            count_cache_parts,
            lambda: int(qry.order_by(None).count()),
        )

        events = (
            qry.options(
                load_only(
                    Event.id,
                    Event.timestamp,
                    Event.source,
                    Event.system,
                    Event.actor,
                    Event.action,
                    Event.outcome,
                    Event.severity,
                    Event.summary,
                    Event.raw_pointer,
                    Event.normalized_payload,
                )
            )
            .order_by(desc(Event.timestamp))
            .limit(limit)
            .offset(offset)
            .all()
        )

        event_ids = [e.id for e in events]

        # Preload controls per event. Unmapped result pages cannot have
        # framework controls by definition, so skip the extra query entirely.
        controls_by_event: dict[uuid.UUID, list[dict[str, Any]]] = {}
        if event_ids and not unmapped:
            rows = (
                db.query(
                    Mapping.event_id,
                    ControlItem.id,
                    ControlItem.ref,
                    ControlItem.title,
                    ControlItem.type,
                )
                .join(ControlItem, ControlItem.id == Mapping.control_item_id)
                .filter(
                    Mapping.event_id.in_(event_ids),
                    ControlItem.framework_slug == framework,
                )
                .order_by(ControlItem.ref.asc())
                .all()
            )
            for ev_id, c_id, ref, title, ctype in rows:
                controls_by_event.setdefault(ev_id, []).append(
                    {"id": str(c_id), "ref": ref, "title": title, "type": ctype}
                )
            inherited = (
                db.query(Mapping.event_id, ControlItem, CrossFrameworkControlLink.source_control_id)
                .join(CrossFrameworkControlLink, CrossFrameworkControlLink.source_control_id == Mapping.control_item_id)
                .join(ControlItem, ControlItem.id == CrossFrameworkControlLink.target_control_id)
                .filter(Mapping.event_id.in_(event_ids), ControlItem.framework_slug == framework)
                .all()
            )
            for ev_id, target, source_id in inherited:
                existing = controls_by_event.setdefault(ev_id, [])
                if any(item["id"] == str(target.id) for item in existing):
                    continue
                existing.append({"id": str(target.id), "ref": target.ref, "title": target.title,
                                 "type": target.type, "inherited_from_control_id": str(source_id)})

        # Preload artifact counts
        artifact_counts: dict[uuid.UUID, int] = {}
        if event_ids:
            rows = (
                db.query(Artifact.event_id, func.count(Artifact.id))
                .filter(Artifact.event_id.in_(event_ids))
                .group_by(Artifact.event_id)
                .all()
            )
            artifact_counts = {ev_id: int(n) for ev_id, n in rows}

        items: list[dict[str, Any]] = []
        for e in events:
            items.append(
                {
                    "id": str(e.id),
                    "timestamp": e.timestamp.isoformat(),
                    "source": e.source,
                    "system": e.system,
                    "identifier": _event_identifier(e),
                    "actor": e.actor,
                    "action": e.action,
                    "outcome": e.outcome,
                    "severity": e.severity,
                    "summary": e.summary,
                    "source_url": _redact_str(
                        _extract_source_url(e.raw_pointer, e.normalized_payload)
                    ),
                    "controls": controls_by_event.get(e.id, []),
                    "artifact_count": artifact_counts.get(e.id, 0),
                }
            )

        return {"total": total, "limit": limit, "offset": offset, "items": items}

    return cached_json("events:list:page", cache_parts, _load_page)


@router.get("/v1/events/facets")
def event_facets(
    request: Request,
    framework: str = settings.default_framework_slug,
    q: Optional[str] = None,
    source: Optional[str] = None,
    system: Optional[str] = None,
    actor: Optional[str] = None,
    control: Optional[str] = None,
    clause: Optional[str] = None,
    unmapped: bool = False,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
    max_sources: int = 500,
    max_controls: int = 500,
    db: Session = Depends(get_db),
):
    """Facet counts for the Events UI.

    Counts are computed with other filters applied, but *exclude* the facet
    itself so the UI can show meaningful alternatives for switching.

    - sources: applies q/system/actor/control/unmapped (excludes source)
    - controls: applies q/system/actor/source/unmapped (excludes control)
    """

    max_sources = max(
        0, min(int(max_sources if max_sources is not None else 500), 5000)
    )
    max_controls = max(
        0, min(int(max_controls if max_controls is not None else 500), 5000)
    )

    user = _require_events_read(db, request)
    cache_parts = {
        "framework": framework,
        "q": q,
        "source": source,
        "system": system,
        "actor": actor,
        "control": control,
        "clause": clause,
        "unmapped": bool(unmapped),
        "start_date": start_date,
        "end_date": end_date,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "max_sources": max_sources,
        "max_controls": max_controls,
        "user": user_cache_scope(user),
    }

    def _load() -> dict:
        # Sources facet (exclude source filter). Allow callers to pass
        # max_sources=0 when they need the event page quickly and do not need
        # expensive facet counters.
        src_rows = []
        if max_sources:
            src_ids = _build_event_id_query(
                db,
                framework=framework,
                q=q,
                source=None,
                system=system,
                actor=actor,
                control=control,
                clause=clause,
                unmapped=unmapped,
                start_date=start_date,
                end_date=end_date,
                start_ts=start_ts,
                end_ts=end_ts,
                user=user,
            ).subquery()

            src_rows = (
                db.query(
                    Event.source.label("source"),
                    func.count(Event.id).label("count"),
                )
                .join(src_ids, src_ids.c.id == Event.id)
                .filter(Event.source.isnot(None))
                .filter(Event.source != "")
                .group_by(Event.source)
                .order_by(desc("count"), Event.source.asc())
                .limit(max_sources)
                .all()
            )

        # Controls facet (exclude control filter). It is necessarily empty for
        # unmapped-only searches, and the join is one of the slowest parts of
        # first-loading /events.html?unmapped=true on larger datasets.
        ctl_rows = []
        if max_controls and not unmapped:
            ctl_ids = _build_event_id_query(
                db,
                framework=framework,
                q=q,
                source=source,
                system=system,
                actor=actor,
                control=None,
                clause=clause,
                unmapped=unmapped,
                start_date=start_date,
                end_date=end_date,
                start_ts=start_ts,
                end_ts=end_ts,
                user=user,
            ).subquery()

            pairs = evidence_pairs(framework)
            ctl_rows = (
                db.query(
                    ControlItem.ref.label("ref"),
                    ControlItem.title.label("title"),
                    ControlItem.type.label("type"),
                    func.count(pairs.c.event_id).label("count"),
                )
                .join(pairs, pairs.c.control_id == ControlItem.id)
                .join(ctl_ids, ctl_ids.c.id == pairs.c.event_id)
                .filter(ControlItem.framework_slug == framework)
                .group_by(ControlItem.id)
                .order_by(desc("count"), ControlItem.ref.asc())
                .limit(max_controls)
                .all()
            )

        return {
            "sources": [{"source": s, "count": int(c)} for s, c in src_rows],
            "controls": [
                {"ref": r, "title": t, "type": ty, "count": int(c)}
                for r, t, ty, c in ctl_rows
            ],
        }

    return cached_json("events:facets", cache_parts, _load)


@router.get("/v1/events/export")
def export_events(
    request: Request,
    framework: str = settings.default_framework_slug,
    format: str = "csv",
    q: Optional[str] = None,
    source: Optional[str] = None,
    system: Optional[str] = None,
    actor: Optional[str] = None,
    control: Optional[str] = None,  # UUID or ref
    clause: Optional[str] = None,  # UUID or ref
    unmapped: bool = False,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
    max_rows: int = 50000,
    db: Session = Depends(get_db),
):
    """Export events matching filters in CSV/JSON/NDJSON.

    This is intended for audit/export workflows (e.g., export all events mapped to a control
    over a time period). Exports are capped to avoid accidental huge downloads.
    """

    fmt = (format or "csv").lower().strip()
    if fmt not in ("csv", "json", "ndjson"):
        raise HTTPException(
            status_code=400, detail="format must be one of: csv, json, ndjson"
        )

    # Hard cap (defense in depth)
    max_rows = max(1, min(int(max_rows or 50000), 50000))

    user = _require_events_read(db, request)

    qry = db.query(Event).filter(diary_filter_condition(db, user))
    qry = qry.filter(*_event_time_filters(start_date, end_date, start_ts, end_ts))

    if source:
        qry = qry.filter(Event.source == source)
    if system:
        qry = qry.filter(Event.system.ilike(f"%{system}%"))
    if actor:
        qry = qry.filter(Event.actor.ilike(f"%{actor}%"))
    search_cond = _event_text_search_condition(q)
    if search_cond is not None:
        qry = qry.filter(search_cond)

    if control:
        cid = _try_uuid(control)
        if cid:
            valid = db.query(ControlItem.id).filter(ControlItem.id == cid, ControlItem.framework_slug == framework).first()
            qry = qry.filter(event_has_control(cid, Event.id) if valid else false())
        else:
            c = (
                db.query(ControlItem)
                .filter(
                    ControlItem.framework_slug == framework, ControlItem.ref == control
                )
                .one_or_none()
            )
            if not c:
                # Empty export
                if fmt == "csv":
                    content = "id,timestamp,source,system,actor,action,outcome,severity,identifier,summary,source_url,controls,artifact_count\n"
                    return StreamingResponse(iter([content]), media_type="text/csv")
                return {"total": 0, "returned": 0, "items": []}
            qry = qry.filter(event_has_control(c.id, Event.id))

    if clause:
        qry = qry.filter(_clause_event_condition(framework, clause))

    if unmapped:
        qry = qry.filter(_unmapped_event_condition(framework))

    total = qry.order_by(None).count()
    returned = min(total, max_rows)

    # Filename hints
    def _safe_name(v: str) -> str:
        v = "".join(ch for ch in (v or "") if ch.isalnum() or ch in ("-", "_", "."))
        return v[:80] or "events"

    name_parts = ["events"]
    if control:
        name_parts.append(f"control-{_safe_name(control)}")
    if clause:
        name_parts.append(f"clause-{_safe_name(clause)}")
    if source:
        name_parts.append(f"source-{_safe_name(source)}")
    if start_date:
        name_parts.append(f"from-{start_date.isoformat()}")
    if end_date:
        name_parts.append(f"to-{end_date.isoformat()}")
    filename = "_".join(name_parts) + (".ndjson" if fmt == "ndjson" else f".{fmt}")

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Keen-Export-Total": str(total),
        "X-Keen-Export-Returned": str(returned),
        "X-Keen-Export-Truncated": "1" if returned < total else "0",
    }

    def iter_chunks():
        chunk = 1000
        yielded = 0

        if fmt == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(
                [
                    "id",
                    "timestamp",
                    "source",
                    "system",
                    "actor",
                    "action",
                    "outcome",
                    "severity",
                    "identifier",
                    "summary",
                    "source_url",
                    "controls",
                    "artifact_count",
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

        if fmt == "json":
            yield '{"total":' + str(total) + ',"returned":' + str(
                returned
            ) + ',"items":['
            first = True

        offset = 0
        while yielded < returned:
            take = min(chunk, returned - yielded)
            batch = qry.order_by(desc(Event.timestamp)).limit(take).offset(offset).all()
            offset += take
            if not batch:
                break

            event_ids = [e.id for e in batch]
            controls_by_event: dict[uuid.UUID, list[str]] = {}
            if event_ids:
                pairs = evidence_pairs(framework)
                rows = (
                    db.query(pairs.c.event_id, ControlItem.ref)
                    .join(ControlItem, ControlItem.id == pairs.c.control_id)
                    .filter(
                        pairs.c.event_id.in_(event_ids),
                    )
                    .order_by(ControlItem.ref.asc())
                    .all()
                )
                for ev_id, ref in rows:
                    controls_by_event.setdefault(ev_id, []).append(ref)

            artifact_counts: dict[uuid.UUID, int] = {}
            if event_ids:
                rows = (
                    db.query(Artifact.event_id, func.count(Artifact.id))
                    .filter(Artifact.event_id.in_(event_ids))
                    .group_by(Artifact.event_id)
                    .all()
                )
                artifact_counts = {ev_id: int(n) for ev_id, n in rows}

            for e in batch:
                item = {
                    "id": str(e.id),
                    "timestamp": e.timestamp.isoformat(),
                    "source": e.source,
                    "system": e.system,
                    "actor": e.actor,
                    "action": e.action,
                    "outcome": e.outcome,
                    "severity": e.severity,
                    "identifier": _event_identifier(e),
                    "summary": e.summary,
                    "source_url": _redact_str(
                        _extract_source_url(e.raw_pointer, e.normalized_payload)
                    ),
                    "controls": controls_by_event.get(e.id, []),
                    "artifact_count": artifact_counts.get(e.id, 0),
                }

                if fmt == "ndjson":
                    yield json.dumps(item, ensure_ascii=False) + "\n"
                elif fmt == "json":
                    s = json.dumps(item, ensure_ascii=False)
                    if first:
                        yield s
                        first = False
                    else:
                        yield "," + s
                else:  # csv
                    buf = io.StringIO()
                    w = csv.writer(buf)
                    w.writerow(
                        [
                            item["id"],
                            item["timestamp"],
                            item["source"] or "",
                            item["system"] or "",
                            item["actor"] or "",
                            item["action"] or "",
                            item["outcome"] or "",
                            item["severity"] or "",
                            item["identifier"] or "",
                            item["summary"] or "",
                            item["source_url"] or "",
                            ",".join(item["controls"] or []),
                            item["artifact_count"] or 0,
                        ]
                    )
                    yield buf.getvalue()

                yielded += 1
                if yielded >= returned:
                    break

        if fmt == "json":
            yield "]}"

    media = (
        "text/csv"
        if fmt == "csv"
        else ("application/x-ndjson" if fmt == "ndjson" else "application/json")
    )
    return StreamingResponse(iter_chunks(), media_type=media, headers=headers)


def _latest_event_payload(event: Event) -> dict[str, Any]:
    short_summary = (
        str(_redact_str(event.summary or "") or "")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )
    if len(short_summary) > 220:
        summary = f"{short_summary[:217].rstrip()}…"
    else:
        summary = short_summary

    source = _redact_str(event.source or "Unknown source")
    system = _redact_str(event.system or "")
    action = _redact_str(event.action or "")
    actor = _redact_str(event.actor or "")

    return {
        "id": str(event.id),
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "source": source,
        "system": system,
        "actor": actor,
        "action": action,
        "outcome": event.outcome,
        "severity": event.severity,
        "summary": summary or "New evidence received",
        "identifier": _event_identifier(event),
        "url": f"/event.html?id={event.id}",
    }


@router.get("/v1/evidence/latest")
@router.get("/v1/events/latest")
def latest_evidence_event(
    request: Request,
    after: Optional[datetime] = None,
    limit: int = 1,
    db: Session = Depends(get_db),
):
    """Return the newest visible events by ingestion time.

    The homepage ticker uses this as a low-cost polling endpoint.  Visibility
    follows the normal event listing rules; diary entries require an active
    authenticated user.  `after` is compared with Event.created_at
    because this endpoint is about what most recently arrived in Keen, not the
    original event timestamp.

    `limit` defaults to 1 to preserve the original endpoint contract; callers
    that want a small ticker batch can request up to 25 events.  The legacy
    `event` field remains the newest item, and `events` contains the returned
    batch in newest-first order.
    """

    limit = max(1, min(int(limit or 1), 25))
    user = _require_events_read(db, request)
    qry = (
        db.query(Event)
        .options(
            load_only(
                Event.id,
                Event.created_at,
                Event.timestamp,
                Event.source,
                Event.system,
                Event.actor,
                Event.action,
                Event.outcome,
                Event.severity,
                Event.summary,
            )
        )
        .filter(diary_filter_condition(db, user))
    )

    if after is not None:
        qry = qry.filter(Event.created_at > _norm_ts(after))

    events = (
        qry.order_by(desc(Event.created_at), desc(Event.timestamp), desc(Event.id))
        .limit(limit)
        .all()
    )
    if not events:
        return {"event": None, "events": []}

    payloads = [_latest_event_payload(event) for event in events]
    return {"event": payloads[0], "events": payloads}


# Legacy convenience endpoint (kept for compatibility)
@router.get("/v1/events/unmapped")
def unmapped_events(
    request: Request,
    framework: str = settings.default_framework_slug,
    limit: int = 100,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    limit = max(1, min(int(limit or 100), 1000))

    user = _require_events_read(db, request)

    q = (
        db.query(Event)
        .options(
            load_only(
                Event.id,
                Event.timestamp,
                Event.source,
                Event.summary,
                Event.raw_pointer,
            )
        )
        .filter(diary_filter_condition(db, user))
        .filter(_unmapped_event_condition(framework))
        .order_by(desc(Event.timestamp))
        .limit(limit)
    )

    return {
        "items": [
            {
                "id": str(e.id),
                "timestamp": e.timestamp.isoformat(),
                "source": e.source,
                "summary": e.summary,
                "action": e.action,
                "outcome": e.outcome,
                "severity": e.severity,
            }
            for e in q.all()
        ]
    }


@router.post("/v1/events/{event_id}/incident")
def create_event_incident(
    event_id: str,
    payload: IncidentCreatePayload,
    request: Request,
    db: Session = Depends(get_db),
):
    if not incident_webhook_enabled():
        raise HTTPException(
            status_code=503, detail="Incident webhook is not configured"
        )

    user = _require_events_read(db, request)
    if not is_effective_admin(db, user) and not has_permission(
        db, user, INCIDENT_CREATE_PERMISSION
    ):
        raise HTTPException(
            status_code=403, detail="incident.create permission required"
        )

    eid = _try_uuid(event_id)
    if not eid:
        raise HTTPException(status_code=400, detail="event_id must be a UUID")

    event = _event_visible_or_404(db, request, eid)

    title = sanitize_plain_text(payload.title, max_chars=256)
    text = sanitize_plain_text(payload.text, max_chars=5000)
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    incident_id = uuid.uuid4()
    created_at = datetime.utcnow()
    event_url = build_event_url(request, str(event.id))
    event_payload = {
        "id": str(event.id),
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "source": event.source,
        "system": _redact_str(event.system),
        "actor": _redact_str(event.actor),
        "action": _redact_str(event.action),
        "outcome": _redact_str(event.outcome),
        "severity": event.severity,
        "summary": _redact_str(event.summary),
    }
    webhook_payload = build_incident_payload(
        incident_id=str(incident_id),
        title=title,
        text=text,
        created_at=created_at.isoformat(),
        created_by=getattr(user, "username", None),
        event=event_payload,
        event_url=event_url,
    )

    config = IncidentWebhookConfig.from_settings()
    try:
        status_code, response_headers = post_incident_webhook(config, webhook_payload)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Incident webhook request failed: {exc}"
        ) from exc

    if not 200 <= int(status_code) < 300:
        raise HTTPException(
            status_code=502,
            detail=f"Incident webhook returned HTTP {int(status_code)}",
        )

    incident = EventIncident(
        id=incident_id,
        event_id=event.id,
        title=title,
        text=text,
        event_url=event_url,
        created_by_user_id=user.id,
        webhook_status_code=int(status_code),
        created_at=created_at,
        meta={"webhook_response_headers": response_headers or {}},
    )
    db.add(incident)
    db.flush()

    _audit_action(
        db,
        request,
        user,
        "incident_created",
        {
            "event_id": event.id,
            "incident_id": incident.id,
            "webhook_status_code": status_code,
        },
    )
    db.commit()
    db.refresh(incident)
    return {"ok": True, "incident": _incident_to_dict(incident)}


@router.delete("/v1/events/{event_id}/incidents/{incident_id}")
def delete_event_incident(
    event_id: str,
    incident_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_events_read(db, request)
    if not is_effective_admin(db, user) and not has_permission(
        db, user, INCIDENT_DELETE_PERMISSION
    ):
        raise HTTPException(
            status_code=403, detail="incident.delete permission required"
        )

    eid = _try_uuid(event_id)
    iid = _try_uuid(incident_id)
    if not eid or not iid:
        raise HTTPException(
            status_code=400, detail="event_id and incident_id must be UUIDs"
        )

    event = _event_visible_or_404(db, request, eid)
    incident = (
        db.query(EventIncident)
        .filter(EventIncident.id == iid, EventIncident.event_id == event.id)
        .one_or_none()
    )
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    _audit_action(
        db,
        request,
        user,
        "incident_deleted",
        {
            "event_id": event.id,
            "incident_id": incident.id,
            "title": incident.title,
        },
    )
    db.delete(incident)
    db.commit()
    return {"ok": True}


@router.get("/v1/events/{event_id}")
def get_event(
    request: Request,
    event_id: str,
    framework: str = settings.default_framework_slug,
    sample_export: bool = False,
    db: Session = Depends(get_db),
):
    eid = _try_uuid(event_id)
    if not eid:
        raise HTTPException(status_code=400, detail="event_id must be a UUID")

    user = _require_events_read(db, request)

    e = db.query(Event).filter(Event.id == eid).one_or_none()
    if not e:
        raise HTTPException(status_code=404, detail="Event not found")

    # Diary visibility
    if e.source == "diary" and not is_diary_event_visible(db, user, eid):
        # Hide diary entries from anonymous or inactive callers.
        raise HTTPException(status_code=404, detail="Event not found")

    # Mapped controls
    mapped = (
        db.query(Mapping, ControlItem)
        .join(ControlItem, ControlItem.id == Mapping.control_item_id)
        .filter(Mapping.event_id == eid, ControlItem.framework_slug == framework)
        .order_by(ControlItem.ref.asc())
        .all()
    )
    controls = [
        {
            "id": str(ci.id),
            "ref": ci.ref,
            "title": ci.title,
            "type": ci.type,
            "in_scope": ci.in_scope,
            "upstream_url": _control_upstream_url(ci),
            "confidence": mp.confidence,
            "method": mp.method,
            "rationale": mp.rationale,
            "mapped_at": mp.mapped_at.isoformat() if mp.mapped_at else None,
        }
        for mp, ci in mapped
    ]
    inherited = (
        db.query(Mapping, ControlItem, CrossFrameworkControlLink)
        .join(CrossFrameworkControlLink, CrossFrameworkControlLink.source_control_id == Mapping.control_item_id)
        .join(ControlItem, ControlItem.id == CrossFrameworkControlLink.target_control_id)
        .filter(Mapping.event_id == eid, ControlItem.framework_slug == framework)
        .order_by(ControlItem.ref.asc())
        .all()
    )
    direct_ids = {item["id"] for item in controls}
    for mapping, target, link in inherited:
        if str(target.id) in direct_ids:
            continue
        source = link.source
        controls.append({
            "id": str(target.id), "ref": target.ref, "title": target.title,
            "type": target.type, "in_scope": target.in_scope,
            "upstream_url": _control_upstream_url(target),
            "confidence": mapping.confidence, "method": "cross_framework",
            "rationale": link.rationale, "mapped_at": mapping.mapped_at.isoformat() if mapping.mapped_at else None,
            "inherited_from": {"framework": source.framework_slug, "ref": source.ref,
                               "control_id": str(source.id)},
        })
        direct_ids.add(str(target.id))

    arts = (
        db.query(Artifact)
        .filter(Artifact.event_id == eid)
        .order_by(desc(Artifact.captured_at))
        .all()
    )
    incidents = (
        db.query(EventIncident)
        .filter(EventIncident.event_id == eid)
        .order_by(desc(EventIncident.created_at))
        .all()
    )

    raw_pointer = _redact_obj(e.raw_pointer)
    normalized_payload = _redact_obj(e.normalized_payload)
    source_url = _redact_str(_extract_source_url(e.raw_pointer, e.normalized_payload))

    resp = {
        "id": str(e.id),
        "timestamp": e.timestamp.isoformat(),
        "source": e.source,
        "system": _maybe_mask_event_sample_value(e.system, sample_export),
        "actor": _maybe_mask_event_sample_value(e.actor, sample_export),
        "action": _maybe_mask_event_sample_value(e.action, sample_export),
        "outcome": _maybe_mask_event_sample_value(e.outcome, sample_export),
        "severity": e.severity,
        "summary": _maybe_mask_event_sample_value(e.summary, sample_export),
        "source_url": _maybe_mask_event_sample_value(source_url, sample_export),
        "raw_pointer": _maybe_mask_event_sample_value(raw_pointer, sample_export),
        "normalized_payload": _maybe_mask_event_sample_value(
            normalized_payload, sample_export
        ),
        "controls": controls,
        "artifacts": [
            {
                "id": str(a.id),
                "kind": a.kind,
                "storage_uri": _maybe_mask_event_sample_value(
                    _redact_str(a.storage_uri), sample_export
                ),
                "sha256": a.sha256,
                "content_type": a.content_type,
                "size_bytes": a.size_bytes,
                "captured_at": a.captured_at.isoformat() if a.captured_at else None,
            }
            for a in arts
        ],
        "incidents": [_incident_to_dict(i) for i in incidents],
    }

    return resp
