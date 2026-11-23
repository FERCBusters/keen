from __future__ import annotations

from fastapi import Depends, Request, WebSocket
from myf.api.context import change_context
from myf.api.errors import json_object
from myf.api.routing import Router
from myf.api.uploads import upload_inputs
from myf.realtime import sockets
from myf.security.auth import require_admin, require_authenticated
from myf.security.redaction import storable
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import questions as service
from app.services.questions import (
    _count_unread_replies_for_user,
    _notification_snapshot,
)

router = Router()


@router.get("/v1/events/{event_id}/questions")
def list_event_questions(
    event_id: str, request: Request, db: Session = Depends(get_db)
):
    require_authenticated(request)
    return service.list_event_questions(
        event_id=event_id, context=change_context(request), db=db
    )


@router.post("/v1/events/{event_id}/questions")
async def create_event_question(
    event_id: str, request: Request, db: Session = Depends(get_db)
):
    require_authenticated(request)
    body_text, files = await question_input(request)
    return await service.create_event_question(
        event_id=event_id,
        context=change_context(request),
        db=db,
        body_text=body_text,
        files=files,
    )


@router.post("/v1/questions/{thread_id}/posts")
async def reply_to_question(
    thread_id: str, request: Request, db: Session = Depends(get_db)
):
    require_authenticated(request)
    body_text, files = await question_input(request)
    return await service.reply_to_question(
        thread_id=thread_id,
        context=change_context(request),
        db=db,
        body_text=body_text,
        files=files,
    )


@router.delete("/v1/questions/{thread_id}")
async def delete_question_thread(
    thread_id: str, request: Request, db: Session = Depends(get_db)
):
    require_authenticated(request)
    return await service.delete_question_thread(
        thread_id=thread_id, context=change_context(request), db=db
    )


@router.get("/v1/me/questions/summary")
def my_questions_summary(request: Request, db: Session = Depends(get_db)):
    user = require_authenticated(request)
    return {"unread_count": _count_unread_replies_for_user(db, user.id)}


@router.get("/v1/me/questions")
def my_questions_list(request: Request, db: Session = Depends(get_db)):
    require_authenticated(request)
    return service.my_questions_list(context=change_context(request), db=db)


@router.get("/v1/questions/entities/{entity_type}/{entity_id}")
def list_entity_questions(
    entity_type: str, entity_id: str, request: Request, db: Session = Depends(get_db)
):
    require_authenticated(request)
    return service.list_entity_questions(
        entity_type=entity_type,
        entity_id=entity_id,
        context=change_context(request),
        db=db,
    )


@router.post("/v1/questions/entities/{entity_type}/{entity_id}")
async def create_entity_question(
    entity_type: str, entity_id: str, request: Request, db: Session = Depends(get_db)
):
    require_authenticated(request)
    body_text, files = await question_input(request)
    return await service.create_entity_question(
        entity_type=entity_type,
        entity_id=entity_id,
        context=change_context(request),
        db=db,
        body_text=body_text,
        files=files,
    )


@router.post("/v1/questions/entities/{entity_type}/{entity_id}/mark-seen")
async def mark_entity_questions_seen(
    entity_type: str, entity_id: str, request: Request, db: Session = Depends(get_db)
):
    require_authenticated(request)
    return await service.mark_entity_questions_seen(
        entity_type=entity_type,
        entity_id=entity_id,
        context=change_context(request),
        db=db,
    )


@router.post("/v1/events/{event_id}/questions/mark-seen")
async def mark_event_questions_seen(
    event_id: str, request: Request, db: Session = Depends(get_db)
):
    "Mark all questions for this event as seen by the current user (thread author)."
    require_authenticated(request)
    return await service.mark_event_questions_seen(
        event_id=event_id, context=change_context(request), db=db
    )


@router.get("/v1/admin/questions/summary", dependencies=[Depends(require_admin)])
def admin_questions_summary(db: Session = Depends(get_db)):
    return service.admin_questions_summary(db=db)


@router.get("/v1/admin/questions", dependencies=[Depends(require_admin)])
def admin_list_questions(status: str | None = None, db: Session = Depends(get_db)):
    return service.admin_list_questions(status=status, db=db)


@router.patch("/v1/admin/questions/{thread_id}", dependencies=[Depends(require_admin)])
async def admin_set_question_status(
    thread_id: str, request: Request, db: Session = Depends(get_db)
):
    payload = await json_object(request)
    return await service.admin_set_question_status(
        thread_id=thread_id, context=change_context(request), db=db, payload=payload
    )


@router.websocket("/ws/notifications")
async def ws_notifications(websocket: WebSocket):
    """myf's notification socket, opening with the account's question counts."""
    # Looked up at each call, so the counts follow this module's snapshot.
    await sockets.serve(
        websocket, lambda *args, **kwargs: _notification_snapshot(*args, **kwargs)
    )


async def question_input(request: Request):
    files = []
    if request.headers.get("content-type", "").startswith("application/json"):
        data = await json_object(request)
        body = str(data.get("body") or "").strip()
    else:
        form = await request.form()
        # Decoded JSON bodies receive the same NUL replacement.
        body = storable(str(form.get("body") or "")).strip()
        files = list(form.getlist("attachments") or [])
    return body, upload_inputs(files)
