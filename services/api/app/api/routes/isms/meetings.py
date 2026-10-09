"""ISMS meetings; see docs/maintainability-review.md for module boundaries."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import IsmsMeeting
from app.db.session import get_db

from .access import (
    require_isms_manage,
    require_isms_read,
)
from .common import (
    _by_id_or_404,
    _clean_framework,
    _clean_minutes_html,
    _clean_text,
    _record,
    _utcnow,
)
from .mutations import (
    _apply_links,
    _delete_entity_links,
    _replace_meeting_links,
    _replace_meeting_people,
    _replace_meeting_person_links,
)
from .schemas import (
    MeetingPayload,
)
from .serializers import (
    _meeting_out,
)

router = APIRouter()


@router.post("/v1/isms/meetings")
def create_meeting(
    payload: MeetingPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    if not payload.date:
        raise HTTPException(status_code=400, detail="date is required")
    row = IsmsMeeting(
        title=_clean_text(
            payload.title or "ISMS Meeting", max_len=256, required=True, label="title"
        ),
        date=payload.date,
        start_time=payload.start_time,
        end_time=payload.end_time,
        agenda_minutes_notes=_clean_minutes_html(payload.agenda_minutes_notes),
        created_by_user_id=user.id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    _replace_meeting_people(
        db, row, payload.attendee_user_ids or [], payload.apology_user_ids or []
    )
    _replace_meeting_person_links(db, row, payload.attendee_person_ids or [], payload.apology_person_ids or [])
    _replace_meeting_links(db, row, payload.links or [])
    _apply_links(db, "meeting", row.id, fw, payload, user)
    after = _meeting_out(db, row, fw)
    _record(db, "meeting", row, "created", None, after, user, request)
    db.commit()
    db.refresh(row)
    return _meeting_out(db, row, fw)


@router.get("/v1/isms/meetings")
def list_meetings(
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    rows = (
        db.query(IsmsMeeting)
        .order_by(IsmsMeeting.date.desc(), IsmsMeeting.start_time.desc())
        .all()
    )
    return {"framework": fw, "items": [_meeting_out(db, r, fw) for r in rows]}


@router.get("/v1/isms/meetings/{meeting_id}")
def get_meeting(
    meeting_id: str,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_read),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsMeeting, meeting_id, "Meeting")
    return _meeting_out(db, row, fw)


@router.patch("/v1/isms/meetings/{meeting_id}")
def update_meeting(
    meeting_id: str,
    payload: MeetingPayload,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsMeeting, meeting_id, "Meeting")
    before = _meeting_out(db, row, fw)
    fields = set(payload.model_fields_set or set())
    if "title" in fields:
        row.title = _clean_text(
            payload.title, max_len=256, required=True, label="title"
        )
    if "date" in fields and payload.date:
        row.date = payload.date
    if "start_time" in fields:
        row.start_time = payload.start_time
    if "end_time" in fields:
        row.end_time = payload.end_time
    if "agenda_minutes_notes" in fields:
        row.agenda_minutes_notes = _clean_minutes_html(payload.agenda_minutes_notes)
    row.updated_at = _utcnow()
    db.add(row)
    db.flush()
    if "attendee_user_ids" in fields or "apology_user_ids" in fields:
        _replace_meeting_people(
            db, row, payload.attendee_user_ids or [], payload.apology_user_ids or []
        )
    if "attendee_person_ids" in fields or "apology_person_ids" in fields:
        _replace_meeting_person_links(db, row, payload.attendee_person_ids or [], payload.apology_person_ids or [])
    if "links" in fields:
        _replace_meeting_links(db, row, payload.links or [])
    _apply_links(db, "meeting", row.id, fw, payload, user)
    after = _meeting_out(db, row, fw)
    _record(db, "meeting", row, "updated", before, after, user, request)
    db.commit()
    db.refresh(row)
    return _meeting_out(db, row, fw)


@router.delete("/v1/isms/meetings/{meeting_id}")
def delete_meeting(
    meeting_id: str,
    request: Request,
    framework: str = settings.default_framework_slug,
    user=Depends(require_isms_manage),
    db: Session = Depends(get_db),
):
    fw = _clean_framework(framework)
    row = _by_id_or_404(db, IsmsMeeting, meeting_id, "Meeting")
    before = _meeting_out(db, row, fw)
    _record(db, "meeting", row, "deleted", before, None, user, request)
    _delete_entity_links(db, "meeting", row.id, fw)
    db.delete(row)
    db.commit()
    return {"ok": True}
