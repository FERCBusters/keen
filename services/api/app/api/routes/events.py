from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from fastapi import Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.routing import Router
from app.core.config import settings
from app.db.models import (
    User,
)
from app.db.session import get_db
from app.outbound.incidents import (
    build_event_url,
)
from app.schemas import IncidentCreatePayload
from app.security.permissions import has_permission
from app.security.roles import is_effective_admin
from app.services import events as service
from app.services import ueu_bundle
from app.services.event_filters import parse_event_filters
from app.services.exports import Export
from app.storage.filenames import content_disposition_attachment

router = Router()

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


@router.get("/v1/events")
def list_events(
    request: Request,
    framework: str = settings.default_framework_slug,
    q: Optional[str] = None,
    source: Optional[str] = None,
    system: Optional[str] = None,
    actor: Optional[str] = None,
    control: Optional[str] = None,  # UUIDs or refs, comma-separated
    clause: Optional[str] = None,  # UUIDs or refs, comma-separated
    rule: Optional[str] = None,  # mapping rule ids, comma-separated
    conditions: list[str] = Query(default=[], alias="f"),
    unmapped: bool = False,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    user = _require_events_read(db, request)
    filters = parse_event_filters(
        framework=framework,
        q=q,
        source=source,
        system=system,
        actor=actor,
        control=control,
        clause=clause,
        rule=rule,
        conditions=conditions,
        unmapped=unmapped,
        start_date=start_date,
        end_date=end_date,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    return service.list_events(
        filters=filters, limit=limit, offset=offset, db=db, user=user
    )


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
    rule: Optional[str] = None,
    conditions: list[str] = Query(default=[], alias="f"),
    unmapped: bool = False,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
    max_sources: int = 500,
    max_controls: int = 500,
    db: Session = Depends(get_db),
):
    "Facet counts for the Events UI.\n\n    Counts are computed with other filters applied, but *exclude* the facet\n    itself so the UI can show meaningful alternatives for switching.\n\n    - sources: every filter except the source list\n    - controls: every filter except the control list\n    - rules: every filter except the rule list\n"
    user = _require_events_read(db, request)
    filters = parse_event_filters(
        framework=framework,
        q=q,
        source=source,
        system=system,
        actor=actor,
        control=control,
        clause=clause,
        rule=rule,
        conditions=conditions,
        unmapped=unmapped,
        start_date=start_date,
        end_date=end_date,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    return service.event_facets(
        filters=filters,
        max_sources=max_sources,
        max_controls=max_controls,
        db=db,
        user=user,
    )


@router.get("/v1/events/export")
def export_events(
    request: Request,
    framework: str = settings.default_framework_slug,
    format: str = "csv",
    q: Optional[str] = None,
    source: Optional[str] = None,
    system: Optional[str] = None,
    actor: Optional[str] = None,
    control: Optional[str] = None,  # UUIDs or refs, comma-separated
    clause: Optional[str] = None,  # UUIDs or refs, comma-separated
    rule: Optional[str] = None,
    conditions: list[str] = Query(default=[], alias="f"),
    unmapped: bool = False,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
    max_rows: int = 50000,
    db: Session = Depends(get_db),
):
    "Export events matching filters in CSV/JSON/NDJSON.\n\n    This is intended for audit/export workflows (e.g., export all events mapped to a control\n    over a time period). Exports are capped to avoid accidental huge downloads.\n"
    user = _require_events_read(db, request)
    filters = parse_event_filters(
        framework=framework,
        q=q,
        source=source,
        system=system,
        actor=actor,
        control=control,
        clause=clause,
        rule=rule,
        conditions=conditions,
        unmapped=unmapped,
        start_date=start_date,
        end_date=end_date,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    result = service.export_events(
        filters=filters, format=format, max_rows=max_rows, db=db, user=user
    )
    if isinstance(result, Export):
        return StreamingResponse(
            result.content, media_type=result.content_type, headers=result.attributes
        )
    return result


@router.get("/v1/evidence/latest")
def latest_evidence_event(
    request: Request,
    after: Optional[datetime] = None,
    limit: int = 1,
    db: Session = Depends(get_db),
):
    "Return the newest visible events by ingestion time, newest first.\n\n    The homepage ticker polls this endpoint. Visibility follows the normal event\n    listing rules. `after` is compared with Event.created_at because the ticker\n    shows the most recent arrivals in Keen, whatever their original event time.\n    `limit` accepts 1 to 25 events.\n"
    user = _require_events_read(db, request)
    return service.latest_evidence_event(user=user, after=after, limit=limit, db=db)


@router.post("/v1/events/{event_id}/incident")
def create_event_incident(
    event_id: str,
    payload: IncidentCreatePayload,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_events_read(db, request)
    return service.create_event_incident(
        event_id=event_id,
        payload=payload,
        context=change_context(request),
        db=db,
        event_url=build_event_url(event_id),
    )


@router.delete("/v1/events/{event_id}/incidents/{incident_id}")
def delete_event_incident(
    event_id: str,
    incident_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_events_read(db, request)
    return service.delete_event_incident(
        event_id=event_id,
        incident_id=incident_id,
        context=change_context(request),
        db=db,
    )


@router.get("/v1/events/{event_id}/control-suggestions")
def event_control_suggestions(
    request: Request,
    event_id: str,
    framework: str = settings.default_framework_slug,
    db: Session = Depends(get_db),
):
    "Rank in-scope controls against the event's wording, for proposing a rule."
    user = _require_events_read(db, request)
    return service.event_control_suggestions(
        user=user, event_id=event_id, framework=framework, db=db
    )


@router.get("/v1/events/{event_id}/ueu-bundle")
def get_event_ueu_bundle(
    request: Request, event_id: uuid.UUID, db: Session = Depends(get_db)
):
    _require_events_read(db, request)
    data = ueu_bundle.event_bundle(db=db, event_id=event_id)
    return Response(
        data,
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition_attachment(
                f"ueu-evidence-{event_id}.zip"
            ),
            "Cache-Control": "no-store",
        },
    )


@router.get("/v1/events/{event_id}")
def get_event(
    request: Request,
    event_id: str,
    framework: str = settings.default_framework_slug,
    sample_export: bool = False,
    db: Session = Depends(get_db),
):
    user = _require_events_read(db, request)
    return service.get_event(
        user=user,
        event_id=event_id,
        framework=framework,
        sample_export=sample_export,
        db=db,
    )
