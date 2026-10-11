from __future__ import annotations

import asyncio

from fastapi import Depends, HTTPException, Request, WebSocket
from sqlalchemy.orm import Session

from app.api.context import change_context
from app.api.routing import Router
from app.api.uploads import upload_inputs
from app.db.models import (
    User,
)
from app.db.session import get_db
from app.realtime.notifications import notification_hub
from app.security.auth import require_admin
from app.services import questions as service
from app.services.questions import (
    _count_unread_replies_for_user,
    _notification_snapshot,
)

router = Router()

_NOTIFICATION_RECHECK_SECONDS = 30


def _get_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@router.get("/v1/events/{event_id}/questions")
def list_event_questions(
    event_id: str, request: Request, db: Session = Depends(get_db)
):
    _get_user(request)
    return service.list_event_questions(
        event_id=event_id, context=change_context(request), db=db
    )


@router.post("/v1/events/{event_id}/questions")
async def create_event_question(
    event_id: str, request: Request, db: Session = Depends(get_db)
):
    _get_user(request)
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
    _get_user(request)
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
    _get_user(request)
    return await service.delete_question_thread(
        thread_id=thread_id, context=change_context(request), db=db
    )


@router.get("/v1/me/questions/summary")
def my_questions_summary(request: Request, db: Session = Depends(get_db)):
    user = _get_user(request)
    return {"unread_count": _count_unread_replies_for_user(db, user.id)}


@router.get("/v1/me/questions")
def my_questions_list(request: Request, db: Session = Depends(get_db)):
    _get_user(request)
    return service.my_questions_list(context=change_context(request), db=db)


@router.get("/v1/questions/entities/{entity_type}/{entity_id}")
def list_entity_questions(
    entity_type: str, entity_id: str, request: Request, db: Session = Depends(get_db)
):
    _get_user(request)
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
    _get_user(request)
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
    _get_user(request)
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
    _get_user(request)
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
    payload = await request.json()
    return await service.admin_set_question_status(
        thread_id=thread_id, context=change_context(request), db=db, payload=payload
    )


@router.websocket("/ws/notifications")
async def ws_notifications(websocket: WebSocket):
    """Bounded notification connections with periodic session revalidation."""
    from starlette.concurrency import run_in_threadpool

    from app.security.csrf import request_origin_allowed

    if not request_origin_allowed(websocket, require_origin=True):
        await websocket.close(code=1008, reason="Origin not allowed")
        return
    initial = await run_in_threadpool(
        _notification_snapshot, websocket.headers, websocket.cookies, counts=True
    )
    if initial is None:
        await websocket.close(code=1008)
        return
    identity, payloads = initial
    if not await notification_hub.connect(
        websocket, user_id=identity[0], is_admin=identity[1]
    ):
        await websocket.close(code=1008, reason="Too many notification connections")
        return
    try:
        await websocket.accept()
        for payload in payloads:
            await asyncio.wait_for(websocket.send_json(payload), timeout=5)
        loop = asyncio.get_running_loop()
        next_check = loop.time() + _NOTIFICATION_RECHECK_SECONDS
        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive_text(), timeout=max(0, next_check - loop.time())
                )
                if len(message) > 1024:
                    break
            except asyncio.TimeoutError:
                current = await run_in_threadpool(
                    _notification_snapshot, websocket.headers, websocket.cookies
                )
                if current is None or current[0] != identity:
                    break
                await asyncio.wait_for(websocket.send_json({"type": "ping"}), timeout=5)
                next_check = loop.time() + _NOTIFICATION_RECHECK_SECONDS
    except Exception:
        pass
    finally:
        await notification_hub.disconnect(websocket)
        try:
            await websocket.close(code=1008)
        except Exception:
            pass


async def question_input(request: Request):
    files = []
    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
        body = str((data or {}).get("body") or "").strip()
    else:
        form = await request.form()
        body = str(form.get("body") or "").strip()
        files = list(form.getlist("attachments") or [])
    return body, upload_inputs(files)
