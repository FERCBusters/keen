from __future__ import annotations

import asyncio
import os
import re
import uuid
from urllib.parse import urlencode, urlparse
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.utils import try_uuid as _try_uuid
from app.db.models import (
    Artifact,
    Event,
    EventQuestionPost,
    EventQuestionPostAttachment,
    EventQuestionThread,
    PestleItem,
    Risk,
    User,
    AuditLog,
)
from app.db.session import get_db
from app.security.auth import require_admin, get_current_user_from_headers_cookies
from app.security.permissions import has_permission
from app.security.roles import is_effective_admin
from app.security.diary_visibility import is_diary_event_visible
from app.storage.s3 import put_bytes
from app.realtime.notifications import notification_bus, notification_hub
from app.core.config import settings

router = APIRouter()

QUESTION_CREATE_PERMISSION = "question.create"

_ALLOWED_WS_ORIGINS = (
    frozenset((getattr(settings, "public_base_url") or "").split(","))
    if getattr(settings, "public_base_url", "")
    else frozenset()
)


def _count_open_questions(db: Session) -> int:
    return int(
        db.query(EventQuestionThread)
        .filter(EventQuestionThread.status.in_(["unanswered", "reviewing"]))
        .count()
    )


def _client_ip(request: Request) -> str | None:
    # Prefer reverse-proxy headers if present (nginx).
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
    meta: dict | None = None,
):
    """Write a semantic audit entry (in addition to per-request logging middleware).

    This helps auditors locate meaningful actions (question opened/replied/status set)
    without having to interpret raw HTTP paths.
    """
    username = getattr(user, "username", None) if user else None
    qs = None
    if meta:
        try:
            qs = urlencode(
                {k: str(v) for k, v in (meta or {}).items() if v is not None}
            )
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
            username=username,
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _count_unread_replies_for_user(db: Session, user_id):
    """Count threads where the author has an unseen admin reply."""
    return int(
        db.query(EventQuestionThread)
        .filter(EventQuestionThread.created_by_user_id == user_id)
        .filter(EventQuestionThread.last_admin_reply_at.isnot(None))
        .filter(
            (EventQuestionThread.author_last_seen_at.is_(None))
            | (
                EventQuestionThread.author_last_seen_at
                < EventQuestionThread.last_admin_reply_at
            )
        )
        .count()
    )


async def _broadcast_open_count(db: Session) -> None:
    """Best-effort push of open question count to all connected admins."""
    try:
        await notification_bus.publish_admin_open_count(
            _count_open_questions(db),
            fallback_hub=notification_hub,
        )
    except Exception:
        return


async def _broadcast_user_unread_count(db: Session, user_id) -> None:
    """Best-effort push of unread-replies count to a specific user."""
    try:
        await notification_bus.publish_user_unread_replies(
            str(user_id),
            _count_unread_replies_for_user(db, user_id),
            fallback_hub=notification_hub,
        )
    except Exception:
        return


def _get_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _ensure_event_visible(db: Session, user: User, event_id: uuid.UUID) -> Event:
    ev = db.query(Event).filter(Event.id == event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")

    # Diary events are visible to active authenticated users only.
    if (ev.source or "") == "diary":
        if not is_diary_event_visible(db, user, ev.id):
            raise HTTPException(status_code=404, detail="Event not found")

    return ev


def _risk_question_target(
    db: Session, user: User, entity_id: uuid.UUID
) -> tuple[str, str, str]:
    row = db.query(Risk).filter(Risk.id == entity_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="CIA Triad risk not found")
    is_owner = bool(row.risk_owner_user_id and row.risk_owner_user_id == user.id)
    if not (
        is_effective_admin(db, user)
        or has_permission(db, user, "risk.read")
        or has_permission(db, user, "risk.manage")
        or is_owner
    ):
        raise HTTPException(
            status_code=403, detail="risk.read permission or risk ownership required"
        )
    asset = getattr(row.asset, "name", None) or "CIA Triad risk"
    title = (row.threat_summary or "").strip() or asset
    return (asset[:128], title[:512], f"/risk.html?id={row.id}#questions")


def _pestle_question_target(
    db: Session, user: User, entity_id: uuid.UUID
) -> tuple[str, str, str]:
    row = db.query(PestleItem).filter(PestleItem.id == entity_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="PESTLE(E) item not found")
    if not (
        is_effective_admin(db, user)
        or has_permission(db, user, "pestle.read")
        or has_permission(db, user, "pestle.manage")
        or has_permission(db, user, "risk.read")
        or has_permission(db, user, "risk.manage")
    ):
        raise HTTPException(status_code=403, detail="pestle.read permission required")
    ref = f"{row.type} / {row.lens}"
    title = (row.item or ref).strip()
    return (
        ref[:128],
        title[:512],
        f"/pestle_item.html?id={row.id}&tab=questions#questions",
    )


def _ensure_question_target_visible(
    db: Session, user: User, entity_type: str, entity_id: uuid.UUID
) -> tuple[str, str, str, uuid.UUID | None]:
    typ = (entity_type or "").strip().lower().replace("-", "_")
    if typ in {"event", "evidence"}:
        ev = _ensure_event_visible(db, user, entity_id)
        return (
            (ev.source or "evidence")[:128],
            (ev.summary or "Evidence")[:512],
            f"/event.html?id={ev.id}#questions",
            ev.id,
        )
    if typ in {"risk", "cia_risk", "cia_triad_risk"}:
        ref, title, url = _risk_question_target(db, user, entity_id)
        return (ref, title, url, None)
    if typ in {"pestle", "pestle_item", "pestle_risk"}:
        ref, title, url = _pestle_question_target(db, user, entity_id)
        return (ref, title, url, None)
    raise HTTPException(status_code=400, detail="Unsupported question target type")


def _question_target_label(thread: EventQuestionThread) -> str:
    typ = (thread.target_type or "event").strip().lower()
    ref = (thread.target_ref or "").strip()
    title = (thread.target_title or "").strip()
    if typ == "risk":
        prefix = "CIA Triad risk"
    elif typ in {"pestle", "pestle_item"}:
        prefix = "PESTLE(E) item"
    else:
        prefix = "Evidence"
    if ref and title:
        return f"{prefix}: {ref} — {title}"
    if title:
        return f"{prefix}: {title}"
    if ref:
        return f"{prefix}: {ref}"
    return prefix


def _question_target_url(thread: EventQuestionThread) -> str:
    tid = str(thread.id)
    typ = (thread.target_type or "event").strip().lower()
    target_id = str(thread.target_id or thread.event_id or "")
    if typ == "risk" and target_id:
        return f"/risk.html?id={target_id}&thread={tid}#questions"
    if typ in {"pestle", "pestle_item"} and target_id:
        return f"/pestle_item.html?id={target_id}&tab=questions&thread={tid}#questions"
    if target_id:
        return f"/event.html?id={target_id}&thread={tid}#questions"
    return "/questions.html"


def _ensure_thread_visible_for_user(
    db: Session, user: User, thread: EventQuestionThread
) -> None:
    """Ensure non-admin question deletion cannot target hidden/private entities."""
    target_type = (thread.target_type or "event").strip().lower()
    if target_type == "event":
        if not thread.event_id:
            raise HTTPException(status_code=404, detail="Event not found")
        _ensure_event_visible(db, user, thread.event_id)
        return
    if thread.target_id:
        _ensure_question_target_visible(db, user, target_type, thread.target_id)
        return
    raise HTTPException(status_code=404, detail="Question target not found")


def _can_delete_question(db: Session, user: User) -> bool:
    return bool(
        is_effective_admin(db, user) or has_permission(db, user, "question.delete")
    )


def _thread_to_dict(
    thread: EventQuestionThread,
    users_by_id: dict[uuid.UUID, str],
    posts_by_thread: dict[uuid.UUID, list[EventQuestionPost]],
    attachments_by_post: dict[uuid.UUID, list[dict]],
) -> dict:
    posts_out = []
    for p in posts_by_thread.get(thread.id, []):
        posts_out.append(
            {
                "id": str(p.id),
                "body": p.body,
                "author_user_id": str(p.author_user_id) if p.author_user_id else None,
                "author_username": (
                    users_by_id.get(p.author_user_id, "") if p.author_user_id else ""
                ),
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "attachments": attachments_by_post.get(p.id, []),
            }
        )

    return {
        "id": str(thread.id),
        "event_id": str(thread.event_id) if thread.event_id else None,
        "target_type": thread.target_type or "event",
        "target_id": (
            str(thread.target_id or thread.event_id)
            if (thread.target_id or thread.event_id)
            else None
        ),
        "target_ref": thread.target_ref,
        "target_title": thread.target_title,
        "target_label": _question_target_label(thread),
        "target_url": _question_target_url(thread),
        "status": thread.status,
        "created_by_user_id": (
            str(thread.created_by_user_id) if thread.created_by_user_id else None
        ),
        "created_by_username": (
            users_by_id.get(thread.created_by_user_id, "")
            if thread.created_by_user_id
            else ""
        ),
        "created_at": thread.created_at.isoformat() if thread.created_at else None,
        "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
        "posts": posts_out,
    }


def _validate_file_magic(data: bytes, content_type: str, filename: str) -> bool:
    """Validate file content against declared content-type using magic bytes.

    Returns True if the file content matches the declared type, False otherwise.
    This prevents uploading malicious files with spoofed content types.
    """
    if not data or len(data) < 4:
        return False

    # Define magic byte signatures for allowed file types
    # Format: (magic_bytes, content_type, [extensions])
    magic_signatures = [
        # PDF
        (b"%PDF-", ["application/pdf"], [".pdf"]),
        # PNG
        (b"\x89PNG\r\n\x1a\n", ["image/png"], [".png"]),
        # JPEG
        (b"\xff\xd8\xff", ["image/jpeg", "image/jpg"], [".jpg", ".jpeg"]),
        # GIF
        (b"GIF87a", ["image/gif"], [".gif"]),
        (b"GIF89a", ["image/gif"], [".gif"]),
        # ZIP (including DOCX, XLSX, PPTX)
        (
            b"PK\x03\x04",
            [
                "application/zip",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ],
            [".zip", ".docx", ".xlsx", ".pptx"],
        ),
        # Microsoft Word (legacy)
        (
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
            [
                "application/msword",
                "application/vnd.ms-word.document.82",
            ],
            [".doc"],
        ),
        # Microsoft Excel (legacy)
        (
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
            [
                "application/vnd.ms-excel",
            ],
            [".xls"],
        ),
        # Plain text - no magic bytes, will be validated by content
        (
            None,
            ["text/plain", "text/csv", "text/xml", "application/json"],
            [".txt", ".csv", ".xml", ".json", ".yml", ".yaml"],
        ),
        # HTML
        (None, ["text/html"], [".html", ".htm"]),
    ]

    # Get file extension
    ext = os.path.splitext(filename)[1].lower() if filename else ""

    # Check if we have a magic signature for this content type
    matched_type = False
    for magic, allowed_types, allowed_exts in magic_signatures:
        if content_type in allowed_types:
            # For text-based types, be more permissive but validate content
            if magic is None:
                # Text files: check for binary content
                try:
                    data[:1024].decode("utf-8")
                    matched_type = True
                except (UnicodeDecodeError, AttributeError):
                    # If it's not valid UTF-8, it might be binary masquerading as text
                    # Allow it if the extension matches a text type
                    matched_type = ext.lower() in [e.lower() for e in allowed_exts]
            else:
                # Binary files: check magic bytes
                if data[: len(magic)] == magic:
                    matched_type = True
            break

    # If no content type was declared, try to infer from extension and magic
    if not matched_type and content_type in ("", "application/octet-stream"):
        for magic, allowed_types, allowed_exts in magic_signatures:
            if ext.lower() in [e.lower() for e in allowed_exts]:
                if magic is None or (magic and data[: len(magic)] == magic):
                    matched_type = True
                    break

    return matched_type


async def _store_attachments(
    db: Session,
    *,
    event_id: uuid.UUID,
    thread_id: uuid.UUID,
    post_id: uuid.UUID,
    files: list,
    captured_by: str,
) -> list[uuid.UUID]:
    """Create Artifact rows + upload to object storage. Returns artifact IDs."""

    out_ids: list[uuid.UUID] = []
    now = _utcnow()
    day = now.strftime("%Y-%m-%d")

    for f in (files or [])[:10]:
        try:
            filename = getattr(f, "filename", None) or "attachment"
            content_type = (
                getattr(f, "content_type", None) or "application/octet-stream"
            )
            ext = os.path.splitext(filename)[1] or ""

            # Starlette's UploadFile exposes an async .read(); fall back to the underlying file.
            data = None
            try:
                read = getattr(f, "read", None)
                if read and asyncio.iscoroutinefunction(read):
                    data = await read()
                elif hasattr(f, "file") and hasattr(f.file, "read"):
                    data = f.file.read()
            except Exception:
                data = None

            if not data:
                continue

            # Validate file content against declared content-type
            if not _validate_file_magic(data, content_type, filename):
                import logging

                logging.warning(
                    f"File upload validation failed for {filename}: "
                    f"content-type={content_type}, size={len(data)} bytes"
                )
                raise HTTPException(
                    status_code=400,
                    detail="Invalid file type: file content does not match declared content-type",
                )

            key = (
                f"questions/{day}/{event_id}/{thread_id}/{post_id}/{uuid.uuid4()}{ext}"
            )
            stored = put_bytes(key, data, content_type)

            a = Artifact(
                event_id=event_id,
                kind="question_attachment",
                storage_uri=stored.uri,
                sha256=stored.sha256,
                content_type=content_type,
                size_bytes=stored.size_bytes,
                captured_at=now,
                captured_by=captured_by,
                meta={
                    "filename": filename,
                    "thread_id": str(thread_id),
                    "post_id": str(post_id),
                },
            )
            db.add(a)
            db.flush()

            out_ids.append(a.id)
        except Exception:
            # If one attachment fails, continue the rest.
            continue

    return out_ids


@router.get("/v1/events/{event_id}/questions")
def list_event_questions(
    event_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_user(request)
    eid = _try_uuid(event_id)
    if not eid:
        raise HTTPException(status_code=400, detail="Invalid event id")

    _ensure_event_visible(db, user, eid)

    threads = (
        db.query(EventQuestionThread)
        .filter(EventQuestionThread.event_id == eid)
        .order_by(EventQuestionThread.created_at.asc())
        .all()
    )
    if not threads:
        return {"event_id": str(eid), "threads": []}

    thread_ids = [t.id for t in threads]
    posts = (
        db.query(EventQuestionPost)
        .filter(EventQuestionPost.thread_id.in_(thread_ids))
        .order_by(EventQuestionPost.created_at.asc())
        .all()
    )

    posts_by_thread: dict[uuid.UUID, list[EventQuestionPost]] = {}
    post_ids: list[uuid.UUID] = []
    user_ids: set[uuid.UUID] = {
        t.created_by_user_id for t in threads if t.created_by_user_id
    }

    for p in posts:
        posts_by_thread.setdefault(p.thread_id, []).append(p)
        post_ids.append(p.id)
        if p.author_user_id:
            user_ids.add(p.author_user_id)

    # Attachments
    attachments_by_post: dict[uuid.UUID, list[dict]] = {pid: [] for pid in post_ids}
    if post_ids:
        rows = (
            db.query(EventQuestionPostAttachment, Artifact)
            .join(Artifact, Artifact.id == EventQuestionPostAttachment.artifact_id)
            .filter(EventQuestionPostAttachment.post_id.in_(post_ids))
            .order_by(EventQuestionPostAttachment.created_at.asc())
            .all()
        )
        for att, art in rows:
            filename = None
            try:
                filename = (art.meta or {}).get("filename")
            except Exception:
                filename = None
            attachments_by_post.setdefault(att.post_id, []).append(
                {
                    "artifact_id": str(art.id),
                    "filename": filename,
                    "content_type": art.content_type,
                    "size_bytes": art.size_bytes,
                    "download_url": f"/api/v1/artifacts/{art.id}/download",
                }
            )

    # Users
    users = db.query(User).filter(User.id.in_(list(user_ids))).all()
    users_by_id = {u.id: u.username for u in users}

    out = [
        _thread_to_dict(t, users_by_id, posts_by_thread, attachments_by_post)
        for t in threads
    ]
    return {"event_id": str(eid), "threads": out}


@router.post("/v1/events/{event_id}/questions")
async def create_event_question(
    event_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_user(request)
    eid = _try_uuid(event_id)
    if not eid:
        raise HTTPException(status_code=400, detail="Invalid event id")

    # Permission gate:
    # - effective admins have all permissions
    # - non-admins must hold question.create
    if not is_effective_admin(db, user) and not has_permission(
        db, user, QUESTION_CREATE_PERMISSION
    ):
        raise HTTPException(
            status_code=403, detail="question.create permission required"
        )

    ev = _ensure_event_visible(db, user, eid)

    ctype = request.headers.get("content-type", "")
    body_text = ""
    files = []

    if ctype.startswith("application/json"):
        data = await request.json()
        body_text = str((data or {}).get("body") or "").strip()
    else:
        form = await request.form()
        body_text = str(form.get("body") or "").strip()
        files = list(form.getlist("attachments") or [])

    if not body_text:
        raise HTTPException(status_code=400, detail="Body is required")

    now = _utcnow()

    thread = EventQuestionThread(
        event_id=ev.id,
        target_type="event",
        target_id=ev.id,
        target_ref=(ev.source or "")[:128],
        target_title=(ev.summary or "")[:512],
        created_by_user_id=user.id,
        status="unanswered",
        created_at=now,
        updated_at=now,
        author_last_seen_at=now,
    )
    db.add(thread)
    db.flush()

    post = EventQuestionPost(
        thread_id=thread.id,
        author_user_id=user.id,
        body=body_text,
        created_at=now,
    )
    db.add(post)
    db.flush()

    artifact_ids = await _store_attachments(
        db,
        event_id=ev.id,
        thread_id=thread.id,
        post_id=post.id,
        files=files,
        captured_by=f"keen:question:{user.username}",
    )
    for aid in artifact_ids:
        db.add(
            EventQuestionPostAttachment(
                post_id=post.id, artifact_id=aid, created_at=now
            )
        )

    _audit_action(
        db,
        request,
        user,
        "event_question_created",
        {
            "event_id": ev.id,
            "thread_id": thread.id,
            "post_id": post.id,
            "attachments": len(artifact_ids),
            "status": thread.status,
        },
    )

    db.commit()

    # Push updated badge count to connected admins.
    await _broadcast_open_count(db)

    # Fanout to outbound webhooks (Slack/Teams/Chat/generic) if configured.
    # Best-effort only: webhook failures must not block question creation.
    try:
        from app.worker.tasks import send_question_webhooks_task

        send_question_webhooks_task.delay(str(thread.id), str(post.id))
    except Exception:
        pass

    return {"thread_id": str(thread.id), "post_id": str(post.id)}


@router.post("/v1/questions/{thread_id}/posts")
async def reply_to_question(
    thread_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_user(request)
    tid = _try_uuid(thread_id)
    if not tid:
        raise HTTPException(status_code=400, detail="Invalid thread id")

    thread = db.query(EventQuestionThread).filter(EventQuestionThread.id == tid).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    target_type = (thread.target_type or "event").strip().lower()
    if target_type == "event":
        if not thread.event_id:
            raise HTTPException(status_code=404, detail="Event not found")
        _ensure_event_visible(db, user, thread.event_id)
    elif thread.target_id:
        _ensure_question_target_visible(db, user, target_type, thread.target_id)

    is_admin = is_effective_admin(db, user)
    is_author = user.id == thread.created_by_user_id

    if not is_admin and not is_author:
        raise HTTPException(
            status_code=403, detail="Only admins or the thread author can reply"
        )

    ctype = request.headers.get("content-type", "")
    body_text = ""
    files = []

    if ctype.startswith("application/json"):
        data = await request.json()
        body_text = str((data or {}).get("body") or "").strip()
    else:
        form = await request.form()
        body_text = str(form.get("body") or "").strip()
        files = list(form.getlist("attachments") or [])

    if not body_text:
        raise HTTPException(status_code=400, detail="Body is required")

    if (thread.target_type or "event") != "event" and files:
        raise HTTPException(
            status_code=400,
            detail="Attachments are currently only supported for evidence questions",
        )

    now = _utcnow()

    post = EventQuestionPost(
        thread_id=thread.id,
        author_user_id=user.id,
        body=body_text,
        created_at=now,
    )
    db.add(post)
    db.flush()

    artifact_ids = await _store_attachments(
        db,
        event_id=thread.event_id,
        thread_id=thread.id,
        post_id=post.id,
        files=files,
        captured_by=f"keen:question:{user.username}",
    )
    for aid in artifact_ids:
        db.add(
            EventQuestionPostAttachment(
                post_id=post.id, artifact_id=aid, created_at=now
            )
        )

    # Status transitions + notification fields:
    # - admin reply -> answered + last_admin_reply_at
    # - author reply -> unanswered (re-opens thread) + mark as seen
    if is_admin:
        thread.status = "answered"
        thread.last_admin_reply_at = now
    else:
        thread.status = "unanswered"
        thread.author_last_seen_at = now

    thread.updated_at = now

    _audit_action(
        db,
        request,
        user,
        "event_question_replied",
        {
            "event_id": thread.event_id,
            "target_type": thread.target_type or "event",
            "target_id": thread.target_id or thread.event_id,
            "thread_id": thread.id,
            "post_id": post.id,
            "attachments": len(artifact_ids),
            "as": "admin" if is_admin else "author",
            "new_status": thread.status,
        },
    )

    db.commit()

    # Push updated badge count to connected admins.
    await _broadcast_open_count(db)

    # Notify the thread author when an admin replies (auditor bell).
    if is_admin:
        if thread.created_by_user_id:
            await _broadcast_user_unread_count(db, thread.created_by_user_id)
    else:
        # Author activity marks their own thread as seen; update their badge.
        await _broadcast_user_unread_count(db, user.id)

    # Fanout to outbound webhooks (Slack/Teams/Chat/generic) if configured.
    # Best-effort only: webhook failures must not block replies.
    if (thread.target_type or "event") == "event":
        try:
            from app.worker.tasks import send_question_reply_webhooks_task

            send_question_reply_webhooks_task.delay(str(thread.id), str(post.id))
        except Exception:
            pass

    return {
        "thread_id": str(thread.id),
        "post_id": str(post.id),
        "status": thread.status,
    }


@router.delete("/v1/questions/{thread_id}")
async def delete_question_thread(
    thread_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_user(request)
    tid = _try_uuid(thread_id)
    if not tid:
        raise HTTPException(status_code=400, detail="Invalid thread id")

    thread = db.query(EventQuestionThread).filter(EventQuestionThread.id == tid).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    if not _can_delete_question(db, user):
        raise HTTPException(
            status_code=403, detail="question.delete permission required"
        )

    if not is_effective_admin(db, user):
        _ensure_thread_visible_for_user(db, user, thread)

    author_user_id = thread.created_by_user_id
    target_type = thread.target_type or "event"
    target_id = thread.target_id or thread.event_id
    event_id = thread.event_id
    old_status = thread.status

    attachment_rows = (
        db.query(EventQuestionPostAttachment, Artifact)
        .join(
            EventQuestionPost,
            EventQuestionPost.id == EventQuestionPostAttachment.post_id,
        )
        .join(Artifact, Artifact.id == EventQuestionPostAttachment.artifact_id)
        .filter(EventQuestionPost.thread_id == tid)
        .all()
    )
    artifact_by_id = {}
    for att, art in attachment_rows:
        db.delete(att)
        if art and art.kind == "question_attachment":
            artifact_by_id[art.id] = art
    if attachment_rows:
        db.flush()
    for art in artifact_by_id.values():
        db.delete(art)

    _audit_action(
        db,
        request,
        user,
        "event_question_deleted",
        {
            "thread_id": thread.id,
            "event_id": event_id,
            "target_type": target_type,
            "target_id": target_id,
            "old_status": old_status,
            "posts": len(thread.posts or []),
            "attachments": len(attachment_rows),
        },
    )

    db.delete(thread)
    db.commit()

    await _broadcast_open_count(db)
    if author_user_id:
        await _broadcast_user_unread_count(db, author_user_id)

    return {"thread_id": str(tid), "deleted": True}


@router.get("/v1/me/questions/summary")
def my_questions_summary(request: Request, db: Session = Depends(get_db)):
    user = _get_user(request)
    return {"unread_count": _count_unread_replies_for_user(db, user.id)}


@router.get("/v1/me/questions")
def my_questions_list(request: Request, db: Session = Depends(get_db)):
    user = _get_user(request)

    threads = (
        db.query(EventQuestionThread)
        .filter(EventQuestionThread.created_by_user_id == user.id)
        .order_by(desc(EventQuestionThread.updated_at))
        .limit(200)
        .all()
    )

    items = []
    event_ids = [t.event_id for t in threads if t.event_id]
    events_by_id = {}
    if event_ids:
        events_by_id = {
            ev.id: ev for ev in db.query(Event).filter(Event.id.in_(event_ids)).all()
        }

    for t in threads:
        ev = events_by_id.get(t.event_id) if t.event_id else None
        unread = bool(
            t.last_admin_reply_at
            and (
                t.author_last_seen_at is None
                or t.author_last_seen_at < t.last_admin_reply_at
            )
        )
        target_url = _question_target_url(t)
        items.append(
            {
                "thread_id": str(t.id),
                "event_id": str(ev.id) if ev else None,
                "event_summary": ev.summary if ev else None,
                "event_source": ev.source if ev else None,
                "event_timestamp": (
                    ev.timestamp.isoformat() if ev and ev.timestamp else None
                ),
                "target_type": t.target_type or "event",
                "target_id": (
                    str(t.target_id or t.event_id)
                    if (t.target_id or t.event_id)
                    else None
                ),
                "target_ref": t.target_ref,
                "target_title": t.target_title,
                "target_label": _question_target_label(t),
                "target_url": target_url,
                "status": t.status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                "last_admin_reply_at": (
                    t.last_admin_reply_at.isoformat() if t.last_admin_reply_at else None
                ),
                "author_last_seen_at": (
                    t.author_last_seen_at.isoformat() if t.author_last_seen_at else None
                ),
                "unread": unread,
            }
        )

    return {"items": items}


@router.get("/v1/questions/entities/{entity_type}/{entity_id}")
def list_entity_questions(
    entity_type: str, entity_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_user(request)
    eid = _try_uuid(entity_id)
    if not eid:
        raise HTTPException(status_code=400, detail="Invalid entity id")
    ref, title, url, event_id = _ensure_question_target_visible(
        db, user, entity_type, eid
    )
    target_type = (entity_type or "").strip().lower().replace("-", "_")
    if target_type in {"evidence"}:
        target_type = "event"
    if target_type in {"cia_risk", "cia_triad_risk"}:
        target_type = "risk"
    if target_type in {"pestle", "pestle_risk"}:
        target_type = "pestle_item"

    threads = (
        db.query(EventQuestionThread)
        .filter(EventQuestionThread.target_type == target_type)
        .filter(EventQuestionThread.target_id == eid)
        .order_by(EventQuestionThread.created_at.asc())
        .all()
    )
    if not threads:
        return {"target_type": target_type, "target_id": str(eid), "threads": []}

    thread_ids = [t.id for t in threads]
    posts = (
        db.query(EventQuestionPost)
        .filter(EventQuestionPost.thread_id.in_(thread_ids))
        .order_by(EventQuestionPost.created_at.asc())
        .all()
    )
    posts_by_thread: dict[uuid.UUID, list[EventQuestionPost]] = {}
    user_ids: set[uuid.UUID] = {
        t.created_by_user_id for t in threads if t.created_by_user_id
    }
    for p in posts:
        posts_by_thread.setdefault(p.thread_id, []).append(p)
        if p.author_user_id:
            user_ids.add(p.author_user_id)
    users = db.query(User).filter(User.id.in_(list(user_ids))).all() if user_ids else []
    users_by_id = {u.id: u.username for u in users}
    out = [_thread_to_dict(t, users_by_id, posts_by_thread, {}) for t in threads]
    return {"target_type": target_type, "target_id": str(eid), "threads": out}


@router.post("/v1/questions/entities/{entity_type}/{entity_id}")
async def create_entity_question(
    entity_type: str, entity_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_user(request)
    eid = _try_uuid(entity_id)
    if not eid:
        raise HTTPException(status_code=400, detail="Invalid entity id")
    if not is_effective_admin(db, user) and not has_permission(
        db, user, QUESTION_CREATE_PERMISSION
    ):
        raise HTTPException(
            status_code=403, detail="question.create permission required"
        )

    ref, title, url, event_id = _ensure_question_target_visible(
        db, user, entity_type, eid
    )
    target_type = (entity_type or "").strip().lower().replace("-", "_")
    if target_type in {"evidence"}:
        target_type = "event"
    if target_type in {"cia_risk", "cia_triad_risk"}:
        target_type = "risk"
    if target_type in {"pestle", "pestle_risk"}:
        target_type = "pestle_item"

    ctype = request.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        data = await request.json()
        body_text = str((data or {}).get("body") or "").strip()
    else:
        form = await request.form()
        body_text = str(form.get("body") or "").strip()
        files = list(form.getlist("attachments") or [])
        if files and target_type != "event":
            raise HTTPException(
                status_code=400,
                detail="Attachments are currently only supported for evidence questions",
            )
    if not body_text:
        raise HTTPException(status_code=400, detail="Body is required")

    now = _utcnow()
    thread = EventQuestionThread(
        event_id=event_id,
        target_type=target_type,
        target_id=eid,
        target_ref=ref,
        target_title=title,
        created_by_user_id=user.id,
        status="unanswered",
        created_at=now,
        updated_at=now,
        author_last_seen_at=now,
    )
    db.add(thread)
    db.flush()
    post = EventQuestionPost(
        thread_id=thread.id,
        author_user_id=user.id,
        body=body_text,
        created_at=now,
    )
    db.add(post)
    db.flush()
    _audit_action(
        db,
        request,
        user,
        f"{target_type}_question_created",
        {
            "target_type": target_type,
            "target_id": eid,
            "thread_id": thread.id,
            "post_id": post.id,
            "status": thread.status,
        },
    )
    db.commit()
    await _broadcast_open_count(db)
    return {"thread_id": str(thread.id), "post_id": str(post.id)}


@router.post("/v1/questions/entities/{entity_type}/{entity_id}/mark-seen")
async def mark_entity_questions_seen(
    entity_type: str, entity_id: str, request: Request, db: Session = Depends(get_db)
):
    user = _get_user(request)
    eid = _try_uuid(entity_id)
    if not eid:
        raise HTTPException(status_code=400, detail="Invalid entity id")
    _ensure_question_target_visible(db, user, entity_type, eid)
    target_type = (entity_type or "").strip().lower().replace("-", "_")
    if target_type in {"evidence"}:
        target_type = "event"
    if target_type in {"cia_risk", "cia_triad_risk"}:
        target_type = "risk"
    if target_type in {"pestle", "pestle_risk"}:
        target_type = "pestle_item"

    now = _utcnow()
    q = (
        db.query(EventQuestionThread)
        .filter(EventQuestionThread.target_type == target_type)
        .filter(EventQuestionThread.target_id == eid)
        .filter(EventQuestionThread.created_by_user_id == user.id)
        .filter(EventQuestionThread.last_admin_reply_at.isnot(None))
        .filter(
            (EventQuestionThread.author_last_seen_at.is_(None))
            | (
                EventQuestionThread.author_last_seen_at
                < EventQuestionThread.last_admin_reply_at
            )
        )
    )
    updated = 0
    for t in q.all():
        t.author_last_seen_at = now
        updated += 1
    if updated:
        db.commit()
    await _broadcast_user_unread_count(db, user.id)
    return {
        "marked": updated,
        "unread_count": _count_unread_replies_for_user(db, user.id),
    }


@router.post("/v1/events/{event_id}/questions/mark-seen")
async def mark_event_questions_seen(
    event_id: str, request: Request, db: Session = Depends(get_db)
):
    """Mark all questions for this event as seen by the current user (thread author)."""
    user = _get_user(request)
    eid = _try_uuid(event_id)
    if not eid:
        raise HTTPException(status_code=400, detail="Invalid event id")

    _ensure_event_visible(db, user, eid)

    now = _utcnow()
    q = (
        db.query(EventQuestionThread)
        .filter(EventQuestionThread.event_id == eid)
        .filter(EventQuestionThread.created_by_user_id == user.id)
        .filter(EventQuestionThread.last_admin_reply_at.isnot(None))
        .filter(
            (EventQuestionThread.author_last_seen_at.is_(None))
            | (
                EventQuestionThread.author_last_seen_at
                < EventQuestionThread.last_admin_reply_at
            )
        )
    )

    updated = 0
    for t in q.all():
        t.author_last_seen_at = now
        updated += 1

    if updated:
        db.commit()

    # Update the auditor's bell badge in real time.
    await _broadcast_user_unread_count(db, user.id)

    return {
        "marked": updated,
        "unread_count": _count_unread_replies_for_user(db, user.id),
    }


@router.get("/v1/admin/questions/summary", dependencies=[Depends(require_admin)])
def admin_questions_summary(db: Session = Depends(get_db)):
    return {"open_count": _count_open_questions(db)}


@router.get("/v1/admin/questions", dependencies=[Depends(require_admin)])
def admin_list_questions(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(EventQuestionThread, User).outerjoin(
        User, User.id == EventQuestionThread.created_by_user_id
    )

    st = (status or "").strip().lower()

    if st == "all":
        pass
    elif st:
        q = q.filter(EventQuestionThread.status == st)
    else:
        q = q.filter(EventQuestionThread.status.in_(["unanswered", "reviewing"]))

    rows = q.order_by(desc(EventQuestionThread.updated_at)).limit(200).all()

    event_ids = [t.event_id for t, _u in rows if t.event_id]
    events_by_id = {}
    if event_ids:
        events_by_id = {
            ev.id: ev for ev in db.query(Event).filter(Event.id.in_(event_ids)).all()
        }

    items = []
    for t, u in rows:
        ev = events_by_id.get(t.event_id) if t.event_id else None
        items.append(
            {
                "thread_id": str(t.id),
                "status": t.status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                "event_id": str(ev.id) if ev else None,
                "event_summary": ev.summary if ev else None,
                "event_source": ev.source if ev else None,
                "event_timestamp": (
                    ev.timestamp.isoformat() if ev and ev.timestamp else None
                ),
                "target_type": t.target_type or "event",
                "target_id": (
                    str(t.target_id or t.event_id)
                    if (t.target_id or t.event_id)
                    else None
                ),
                "target_ref": t.target_ref,
                "target_title": t.target_title,
                "target_label": _question_target_label(t),
                "target_url": _question_target_url(t),
                "created_by_username": u.username if u else "",
            }
        )

    return {"items": items}


@router.patch("/v1/admin/questions/{thread_id}", dependencies=[Depends(require_admin)])
async def admin_set_question_status(
    thread_id: str, request: Request, db: Session = Depends(get_db)
):
    tid = _try_uuid(thread_id)
    user = _get_user(request)
    if not tid:
        raise HTTPException(status_code=400, detail="Invalid thread id")

    data = await request.json()
    status = str((data or {}).get("status") or "").strip().lower()
    if status not in {"unanswered", "reviewing", "answered"}:
        raise HTTPException(status_code=400, detail="Invalid status")

    t = db.query(EventQuestionThread).filter(EventQuestionThread.id == tid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Thread not found")

    old_status = t.status
    t.status = status
    t.updated_at = _utcnow()

    _audit_action(
        db,
        request,
        user,
        "event_question_status_set",
        {
            "thread_id": t.id,
            "event_id": t.event_id,
            "target_type": t.target_type or "event",
            "target_id": t.target_id or t.event_id,
            "old_status": old_status,
            "new_status": t.status,
        },
    )

    db.commit()

    # Push updated badge count to connected admins.
    await _broadcast_open_count(db)

    return {"thread_id": str(t.id), "status": t.status}


@router.websocket("/ws/notifications")
async def ws_notifications(websocket: WebSocket, db: Session = Depends(get_db)):
    """Push notifications to connected clients.

    Emits:
      - Admins: {type: "questions.open_count", open_count: <int>}
      - Any user: {type: "questions.unread_replies_count", unread_count: <int>}

    Auth uses the same cookie session / trusted REMOTE_USER header as HTTP.
    """

    origin = websocket.headers.get("origin", "")
    if origin:
        parsed = urlparse(origin)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            try:
                await websocket.accept()
            except Exception:
                pass
            await websocket.close(code=1008, reason="Invalid origin")
            return
        # Whitelist specific allowed origins from settings (enforced if configured)
        if _ALLOWED_WS_ORIGINS and origin not in _ALLOWED_WS_ORIGINS:
            try:
                await websocket.accept()
            except Exception:
                pass
            await websocket.close(code=1008, reason="Origin not allowed")
            return

    user = get_current_user_from_headers_cookies(
        websocket.headers, websocket.cookies, db
    )
    if not user:
        try:
            await websocket.close(code=1008)
        except Exception:
            try:
                await websocket.accept()
            except Exception:
                return
            await websocket.close(code=1008)
        return

    is_admin = is_effective_admin(db, user)

    await websocket.accept()
    await notification_hub.connect(websocket, user_id=str(user.id), is_admin=is_admin)

    # Initial payloads
    try:
        if is_admin:
            await websocket.send_json(
                {
                    "scope": "admin",
                    "type": "questions.open_count",
                    "open_count": _count_open_questions(db),
                }
            )
        await websocket.send_json(
            {
                "scope": "user",
                "type": "questions.unread_replies_count",
                "user_id": str(user.id),
                "unread_count": _count_unread_replies_for_user(db, user.id),
            }
        )
    except Exception:
        await notification_hub.disconnect(websocket)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
        return

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=120)
            except asyncio.TimeoutError:
                # Keepalive tick.
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
            except Exception:
                # Handle any other receive errors gracefully
                break
    except WebSocketDisconnect:
        pass
    finally:
        await notification_hub.disconnect(websocket)
        try:
            await websocket.close()
        except Exception:
            pass
