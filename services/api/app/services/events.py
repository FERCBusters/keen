"""Events application operations."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, aliased, joinedload, load_only

from app.core.cache import cached_json, user_cache_scope
from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.core.references import control_upstream_url as _control_upstream_url
from app.core.references import try_uuid as _try_uuid
from app.db.models import (
    Artifact,
    ControlItem,
    EffectiveCrossFrameworkControlLink,
    Event,
    EventIncident,
    Mapping,
    User,
    effective_cross_framework_links,
)
from app.outbound.incidents import (
    IncidentWebhookConfig,
    build_incident_payload,
    incident_webhook_enabled,
    post_incident_webhook,
    sanitize_plain_text,
)
from app.schemas import IncidentCreatePayload
from app.security.csv import spreadsheet_cell
from app.security.diary_visibility import diary_filter_condition, is_diary_event_visible
from app.security.permissions import has_permission
from app.security.redaction import mask_event_data_obj as _mask_event_data_obj
from app.security.redaction import mask_event_data_str as _mask_event_data_str
from app.security.redaction import redact_obj as _redact_obj
from app.security.redaction import redact_str as _redact_str
from app.security.roles import is_effective_admin
from app.services.activity_log import record_action as _audit_action
from app.services.context import ChangeContext
from app.services.control_inheritance import (
    evidence_pairs,
    page_controls,
)
from app.services.errors import DomainError
from app.services.event_filters import (
    EventFilters,
    event_predicates,
    event_time_filters,
    norm_ts,
    run_scan,
    scan_budget,
)
from app.services.exports import Export
from app.services.mapping_provenance import rule_id_of
from app.services.presentation import event_identifier as _event_identifier
from app.services.presentation import extract_source_url as _extract_source_url

INCIDENT_CREATE_PERMISSION = "incident.create"
INCIDENT_DELETE_PERMISSION = "incident.delete"
EVENTS_READ_PERMISSION = "events.read"
# Question attachments are discussion material uploaded by KEEN users. They stay
# with their question thread and never appear as the event's collected evidence.
QUESTION_ATTACHMENT_KIND = "question_attachment"


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


def _event_visible_or_404(
    db: Session, context: ChangeContext, event_id: uuid.UUID
) -> Event:
    event = db.query(Event).filter(Event.id == event_id).one_or_none()
    if not event:
        raise DomainError(status_code=404, detail="Event not found")
    user = context.actor
    if event.source == "diary" and not is_diary_event_visible(db, user, event_id):
        raise DomainError(status_code=404, detail="Event not found")
    return event


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


def _build_event_id_query(db: Session, filters: EventFilters, user: User | None):
    """Return a query that yields distinct Event IDs for the given filters."""
    qry = db.query(Event.id)
    # RBAC: hide diary events the caller is not permitted to see.
    qry = qry.filter(diary_filter_condition(db, user))
    qry = qry.filter(*event_time_filters(filters))
    qry = qry.filter(*event_predicates(filters, db))
    return qry.distinct()


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


def list_events(
    *,
    filters: EventFilters,
    limit: int,
    offset: int,
    db: Session,
    user,
):
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    framework = filters.framework
    unmapped = filters.unmapped

    qry = db.query(Event)
    qry = qry.filter(diary_filter_condition(db, user))
    qry = qry.filter(*event_time_filters(filters))
    qry = qry.filter(*event_predicates(filters, db))

    cache_parts = {
        **filters.cache_parts(),
        "limit": limit,
        "offset": offset,
        "user": user_cache_scope(user),
        "query_version": "inherited-controls-v3",
    }

    def _load_page() -> dict[str, Any]:
        count_cache_parts = dict(cache_parts)
        count_cache_parts.pop("limit", None)
        count_cache_parts.pop("offset", None)
        scan_budget(db, filters)
        total = cached_json(
            "events:list:count",
            count_cache_parts,
            lambda: run_scan(db, filters, lambda: int(qry.order_by(None).count())),
        )

        events = run_scan(
            db,
            filters,
            lambda: (
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
            ),
        )

        event_ids = [e.id for e in events]

        controls_by_event = (
            page_controls(db, framework, event_ids) if not unmapped else {}
        )

        # Preload artifact counts
        artifact_counts: dict[uuid.UUID, int] = {}
        if event_ids:
            rows = (
                db.query(Artifact.event_id, func.count(Artifact.id))
                .filter(Artifact.event_id.in_(event_ids))
                .filter(Artifact.kind != QUESTION_ATTACHMENT_KIND)
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
                    "connection_id": e.connection_id,
                    "connection_name": e.connection_name,
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


def _rule_facet(db: Session, filters: EventFilters, user) -> list[dict[str, Any]]:
    """Mapping rules that made mappings for the filtered events, with counts.

    The rule list itself is left out of the filters, so the facet offers the
    rules a user could switch to. Rules are described by their description
    and targets; their conditions stay in the administration area.
    """
    from app.services.managed_configurations import rule_descriptions

    event_ids = _build_event_id_query(db, filters.without("rule"), user).subquery()
    rows = run_scan(
        db,
        filters,
        lambda: (
            db.query(
                Mapping.rationale,
                func.count(func.distinct(Mapping.event_id)).label("count"),
            )
            .join(event_ids, event_ids.c.id == Mapping.event_id)
            .filter(Mapping.method == "rule", Mapping.mapped_by == "system")
            .filter(Mapping.rationale.like("auto by rule %"))
            .group_by(Mapping.rationale)
            .all()
        ),
    )
    suffix = f" ({filters.framework})"
    counts: dict[str, int] = {}
    for rationale, count in rows:
        if rationale.endswith(suffix):
            rule_id = rationale[len("auto by rule ") : -len(suffix)]
            counts[rule_id] = counts.get(rule_id, 0) + int(count)
    described = rule_descriptions(db)
    facet = [
        {
            "rule_id": rule_id,
            "count": count,
            **described.get(rule_id, {"description": "", "targets": []}),
        }
        for rule_id, count in counts.items()
    ]
    facet.sort(key=lambda row: (-row["count"], row["rule_id"]))
    return facet


def event_facets(
    *,
    filters: EventFilters,
    max_sources: int,
    max_controls: int,
    db: Session,
    user,
):
    """Facet counts for the Events UI.

    Counts are computed with other filters applied, but *exclude* the facet
    itself so the UI can show meaningful alternatives for switching.

    - sources: every filter except the source list
    - controls: every filter except the control list
    - rules: every filter except the rule list
    """

    framework = filters.framework
    unmapped = filters.unmapped
    max_sources = max(
        0, min(int(max_sources if max_sources is not None else 500), 5000)
    )
    max_controls = max(
        0, min(int(max_controls if max_controls is not None else 500), 5000)
    )

    cache_parts = {
        **filters.cache_parts(),
        "max_sources": max_sources,
        "max_controls": max_controls,
        "user": user_cache_scope(user),
        "query_version": "rules-facet-v1",
    }

    def _load() -> dict:
        # Sources facet (exclude source filter). Allow callers to pass
        # max_sources=0 when they need the event page quickly and do not need
        # expensive facet counters.
        scan_budget(db, filters)
        src_rows = []
        if max_sources:
            src_ids = _build_event_id_query(
                db, filters.without("source"), user
            ).subquery()

            src_rows = run_scan(
                db,
                filters,
                lambda: (
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
                ),
            )

        # Controls facet (exclude control filter). It is necessarily empty for
        # unmapped-only searches, and the join is one of the slowest parts of
        # first-loading /events.html?unmapped=true on larger datasets.
        ctl_rows = []
        if max_controls and not unmapped:
            ctl_ids = _build_event_id_query(
                db, filters.without("control"), user
            ).subquery()

            pairs = evidence_pairs(framework, db, select(ctl_ids.c.id))
            ctl_rows = run_scan(
                db,
                filters,
                lambda: (
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
                ),
            )

        return {
            "sources": [{"source": s, "count": int(c)} for s, c in src_rows],
            "controls": [
                {"ref": r, "title": t, "type": ty, "count": int(c)}
                for r, t, ty, c in ctl_rows
            ],
            "rules": [] if unmapped else _rule_facet(db, filters, user),
        }

    return cached_json("events:facets", cache_parts, _load)


def latest_evidence_event(*, after: Optional[datetime], limit: int, db: Session, user):
    """Return up to 25 of the newest visible events by ingestion time.

    Visibility follows the normal event listing rules; diary entries require an
    active authenticated user. `after` is compared with Event.created_at, and
    `events` lists the batch newest first.
    """

    limit = max(1, min(int(limit or 1), 25))
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
        qry = qry.filter(Event.created_at > norm_ts(after))

    events = (
        qry.order_by(desc(Event.created_at), desc(Event.timestamp), desc(Event.id))
        .limit(limit)
        .all()
    )
    return {"events": [_latest_event_payload(event) for event in events]}


SUGGESTION_LIMIT = 8


def event_control_suggestions(*, event_id: str, framework: str, db: Session, user):
    """Rank in-scope controls against an event's wording, for proposing a rule.

    The event's summary and activity fields are matched against the catalogue
    text of the selected framework. Controls the event already maps to in that
    framework are listed separately so a reviewer sees what is left to decide.
    """
    from app.services.control_suggestions import rank_controls

    eid = _try_uuid(event_id)
    if not eid:
        raise DomainError(status_code=400, detail="event_id must be a UUID")
    e = db.query(Event).filter(Event.id == eid).one_or_none()
    if not e:
        raise DomainError(status_code=404, detail="Event not found")
    if e.source == "diary" and not is_diary_event_visible(db, user, eid):
        raise DomainError(status_code=404, detail="Event not found")
    fw = str(framework or "").strip()[:64]
    if not fw:
        raise DomainError(status_code=400, detail="framework is required")

    mapped_refs = sorted(
        {
            ref
            for (ref,) in db.query(ControlItem.ref)
            .join(Mapping, Mapping.control_item_id == ControlItem.id)
            .filter(Mapping.event_id == eid, ControlItem.framework_slug == fw)
            .all()
        }
    )
    candidates = (
        db.query(ControlItem)
        .filter(ControlItem.framework_slug == fw, ControlItem.in_scope.is_(True))
        .order_by(ControlItem.ref, ControlItem.id)
        .limit(5000)
        .all()
    )
    activity = " ".join(
        str(part or "").strip() for part in (e.action, e.system, e.outcome)
    ).strip()
    description = f"{activity} {e.summary or ''}".strip()[:4000]
    ranked = rank_controls(
        candidates,
        description=description or (e.source or ""),
        sample_summary=str(e.actor or "")[:4000],
        limit=SUGGESTION_LIMIT + len(mapped_refs),
    )
    items = [row for row in ranked if row["ref"] not in mapped_refs][:SUGGESTION_LIMIT]
    return {
        "event_id": str(eid),
        "framework": fw,
        "unmapped": not mapped_refs,
        "already_mapped": mapped_refs,
        "items": items,
        "note": (
            "Suggestions match the event's wording against catalogue text. "
            "Review each control before proposing a rule; nothing is mapped "
            "until a rule is saved or a mapping is added."
        ),
    }


def get_event(*, event_id: str, framework: str, sample_export: bool, db: Session, user):
    eid = _try_uuid(event_id)
    if not eid:
        raise DomainError(status_code=400, detail="event_id must be a UUID")

    e = db.query(Event).filter(Event.id == eid).one_or_none()
    if not e:
        raise DomainError(status_code=404, detail="Event not found")

    # Diary visibility
    if e.source == "diary" and not is_diary_event_visible(db, user, eid):
        # Hide diary entries from anonymous or inactive callers.
        raise DomainError(status_code=404, detail="Event not found")

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
            "rule_id": rule_id_of(mp, ci.framework_slug),
            "mapped_at": mp.mapped_at.isoformat() if mp.mapped_at else None,
        }
        for mp, ci in mapped
    ]
    from app.services.framework_catalogue import _framework_selection

    enabled = _framework_selection(db)[3]
    source_ids = select(Mapping.control_item_id).where(Mapping.event_id == eid)
    scoped_links = effective_cross_framework_links(
        source_ids=source_ids, target_framework=framework, enabled=enabled
    )
    link_model = aliased(
        EffectiveCrossFrameworkControlLink, scoped_links, adapt_on_names=True
    )
    inherited = (
        db.query(Mapping, ControlItem, link_model)
        .join(link_model, link_model.source_control_id == Mapping.control_item_id)
        .join(ControlItem, ControlItem.id == link_model.target_control_id)
        .options(joinedload(link_model.source))
        .filter(Mapping.event_id == eid)
        .order_by(ControlItem.ref.asc(), link_model.source_control_id.asc())
        .all()
    )
    direct_ids = {item["id"] for item in controls}
    for mapping, target, link in inherited:
        if str(target.id) in direct_ids:
            continue
        source = link.source
        controls.append(
            {
                "id": str(target.id),
                "ref": target.ref,
                "title": target.title,
                "type": target.type,
                "in_scope": target.in_scope,
                "upstream_url": _control_upstream_url(target),
                "confidence": mapping.confidence,
                "method": "cross_framework",
                "rationale": link.rationale,
                "mapped_at": mapping.mapped_at.isoformat()
                if mapping.mapped_at
                else None,
                "inherited_from": {
                    "framework": source.framework_slug,
                    "ref": source.ref,
                    "control_id": str(source.id),
                },
            }
        )
        direct_ids.add(str(target.id))

    arts = (
        db.query(Artifact)
        .filter(Artifact.event_id == eid)
        .filter(Artifact.kind != QUESTION_ATTACHMENT_KIND)
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
        "connection_id": e.connection_id,
        "connection_name": e.connection_name,
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


def export_events(
    *,
    filters: EventFilters,
    format: str,
    max_rows: int,
    db: Session,
    user,
):
    """Export events matching filters in CSV/JSON/NDJSON.

    This is intended for audit/export workflows (e.g., export all events mapped to a control
    over a time period). Exports are capped to avoid accidental huge downloads.
    """

    framework = filters.framework
    fmt = (format or "csv").lower().strip()
    if fmt not in ("csv", "json", "ndjson"):
        raise DomainError(
            status_code=400, detail="format must be one of: csv, json, ndjson"
        )

    # Hard cap (defense in depth)
    max_rows = max(1, min(int(max_rows or 50000), 50000))

    qry = db.query(Event).filter(diary_filter_condition(db, user))
    qry = qry.filter(*event_time_filters(filters))
    qry = qry.filter(*event_predicates(filters, db))

    scan_budget(db, filters)
    total = run_scan(db, filters, lambda: qry.order_by(None).count())
    returned = min(total, max_rows)

    # Filename hints
    def _safe_name(v: str) -> str:
        v = "".join(ch for ch in (v or "") if ch.isalnum() or ch in ("-", "_", "."))
        return v[:80] or "events"

    name_parts = ["events"]
    for label, values in (
        ("control", filters.controls),
        ("clause", filters.clauses),
        ("source", filters.sources),
        ("rule", filters.rules),
    ):
        if values:
            name_parts.append(f"{label}-{_safe_name('-'.join(values))}")
    if filters.start_date:
        name_parts.append(f"from-{filters.start_date.isoformat()}")
    if filters.end_date:
        name_parts.append(f"to-{filters.end_date.isoformat()}")
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
            yield (
                '{"total":' + str(total) + ',"returned":' + str(returned) + ',"items":['
            )
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
                pairs = evidence_pairs(framework, db, event_ids)
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
                    .filter(Artifact.kind != QUESTION_ATTACHMENT_KIND)
                    .group_by(Artifact.event_id)
                    .all()
                )
                artifact_counts = {ev_id: int(n) for ev_id, n in rows}

            for e in batch:
                item = {
                    "id": str(e.id),
                    "timestamp": e.timestamp.isoformat(),
                    "source": e.source,
                    "connection_id": e.connection_id,
                    "connection_name": e.connection_name,
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
                        spreadsheet_cell(value)
                        for value in [
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
    return Export(iter_chunks(), content_type=media, attributes=headers)


def create_event_incident(
    *,
    event_id: str,
    payload: IncidentCreatePayload,
    context: ChangeContext,
    db: Session,
    event_url: str,
):
    if not incident_webhook_enabled():
        raise DomainError(status_code=503, detail="Incident webhook is not configured")

    user = context.actor
    if not is_effective_admin(db, user) and not has_permission(
        db, user, INCIDENT_CREATE_PERMISSION
    ):
        raise DomainError(status_code=403, detail="incident.create permission required")

    eid = _try_uuid(event_id)
    if not eid:
        raise DomainError(status_code=400, detail="event_id must be a UUID")

    event = _event_visible_or_404(db, context, eid)

    title = sanitize_plain_text(payload.title, max_chars=256)
    text = sanitize_plain_text(payload.text, max_chars=5000)
    if not title:
        raise DomainError(status_code=400, detail="Title is required")

    incident_id = uuid.uuid4()
    created_at = utc_now_naive()
    event_payload = {
        "id": str(event.id),
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "source": event.source,
        "connection_id": event.connection_id,
        "connection_name": event.connection_name,
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
    except ValueError:
        raise DomainError(
            status_code=503, detail="Incident webhook configuration is invalid"
        ) from None
    except Exception:
        raise DomainError(
            status_code=502,
            detail="Incident webhook context failed; check connectivity and configuration",
        ) from None

    if not 200 <= int(status_code) < 300:
        raise DomainError(
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
        context,
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


def delete_event_incident(
    *, event_id: str, incident_id: str, context: ChangeContext, db: Session
):
    user = context.actor
    if not is_effective_admin(db, user) and not has_permission(
        db, user, INCIDENT_DELETE_PERMISSION
    ):
        raise DomainError(status_code=403, detail="incident.delete permission required")

    eid = _try_uuid(event_id)
    iid = _try_uuid(incident_id)
    if not eid or not iid:
        raise DomainError(
            status_code=400, detail="event_id and incident_id must be UUIDs"
        )

    event = _event_visible_or_404(db, context, eid)
    incident = (
        db.query(EventIncident)
        .filter(EventIncident.id == iid, EventIncident.event_id == event.id)
        .one_or_none()
    )
    if not incident:
        raise DomainError(status_code=404, detail="Incident not found")

    _audit_action(
        db,
        context,
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
