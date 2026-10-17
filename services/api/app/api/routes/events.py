from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import Depends, HTTPException, Request
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
from app.services.exports import Export

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
    user = _require_events_read(db, request)
    return service.list_events(
        user=user,
        framework=framework,
        q=q,
        source=source,
        system=system,
        actor=actor,
        control=control,
        clause=clause,
        unmapped=unmapped,
        start_date=start_date,
        end_date=end_date,
        start_ts=start_ts,
        end_ts=end_ts,
        limit=limit,
        offset=offset,
        db=db,
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
    unmapped: bool = False,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
    max_sources: int = 500,
    max_controls: int = 500,
    db: Session = Depends(get_db),
):
    "Facet counts for the Events UI.\n\n    Counts are computed with other filters applied, but *exclude* the facet\n    itself so the UI can show meaningful alternatives for switching.\n\n    - sources: applies q/system/actor/control/unmapped (excludes source)\n    - controls: applies q/system/actor/source/unmapped (excludes control)\n"
    user = _require_events_read(db, request)
    return service.event_facets(
        user=user,
        framework=framework,
        q=q,
        source=source,
        system=system,
        actor=actor,
        control=control,
        clause=clause,
        unmapped=unmapped,
        start_date=start_date,
        end_date=end_date,
        start_ts=start_ts,
        end_ts=end_ts,
        max_sources=max_sources,
        max_controls=max_controls,
        db=db,
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
    "Export events matching filters in CSV/JSON/NDJSON.\n\n    This is intended for audit/export workflows (e.g., export all events mapped to a control\n    over a time period). Exports are capped to avoid accidental huge downloads.\n"
    user = _require_events_read(db, request)
    result = service.export_events(
        user=user,
        framework=framework,
        format=format,
        q=q,
        source=source,
        system=system,
        actor=actor,
        control=control,
        clause=clause,
        unmapped=unmapped,
        start_date=start_date,
        end_date=end_date,
        start_ts=start_ts,
        end_ts=end_ts,
        max_rows=max_rows,
        db=db,
    )
    if isinstance(result, Export):
        return StreamingResponse(
            result.content, media_type=result.content_type, headers=result.attributes
        )
    return result


@router.get("/v1/evidence/latest")
@router.get("/v1/events/latest")
def latest_evidence_event(
    request: Request,
    after: Optional[datetime] = None,
    limit: int = 1,
    db: Session = Depends(get_db),
):
    "Return the newest visible events by ingestion time.\n\n    The homepage ticker uses this as a low-cost polling endpoint.  Visibility\n    follows the normal event listing rules; diary entries require an active\n    authenticated user.  `after` is compared with Event.created_at\n    because this endpoint is about what most recently arrived in Keen, not the\n    original event timestamp.\n\n    `limit` defaults to 1 to preserve the original endpoint contract; callers\n    that want a small ticker batch can request up to 25 events.  The legacy\n    `event` field remains the newest item, and `events` contains the returned\n    batch in newest-first order.\n"
    user = _require_events_read(db, request)
    return service.latest_evidence_event(user=user, after=after, limit=limit, db=db)


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
    user = _require_events_read(db, request)
    return service.unmapped_events(
        user=user,
        framework=framework,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
        db=db,
    )


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
