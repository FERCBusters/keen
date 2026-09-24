from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.worker.celery_app import celery_app

from app.ingest.loki import ingest_loki_all
from app.ingest.cloudwatch_logs import ingest_cloudwatch_logs_all
from app.ingest.github import ingest_github_all
from app.ingest.forgejo import ingest_forgejo_all
from app.ingest.jenkins import ingest_jenkins_all
from app.ingest.taiga import ingest_taiga_all
from app.ingest.bookstack import ingest_bookstack_all
from app.ingest.rss import ingest_rss_all
from app.ingest.google_workspace import ingest_google_workspace_all
from app.services.audit_schedules import materialize_due_scheduled_audits
from app.services.control_evidence_stats import refresh_framework_event_counts_cache
from app.services.effectiveness_metrics import materialize_missing_monthly_zero_metrics

from app.outbound.question_webhooks import (
    OutboundWebhookConfig,
    build_generic_payload,
    build_question_links,
    send_question_created_webhooks,
    send_question_reply_webhooks,
)

from app.db.models import (
    Event,
    User,
    Mapping,
    ControlItem,
    EventQuestionThread,
    EventQuestionPost,
    EventQuestionPostAttachment,
    Artifact,
)

import uuid
import logging

log = logging.getLogger(__name__)


@celery_app.task(name="app.worker.tasks.ingest_loki_all_task")
def ingest_loki_all_task() -> list[dict]:
    db: Session = SessionLocal()
    try:
        return ingest_loki_all(db)
    finally:
        db.close()


@celery_app.task(name="app.worker.tasks.ingest_cloudwatch_logs_all_task")
def ingest_cloudwatch_logs_all_task() -> list[dict]:
    db: Session = SessionLocal()
    try:
        return ingest_cloudwatch_logs_all(db)
    finally:
        db.close()


@celery_app.task(name="app.worker.tasks.ingest_github_all_task")
def ingest_github_all_task() -> list[dict]:
    db: Session = SessionLocal()
    try:
        return ingest_github_all(db)
    finally:
        db.close()


@celery_app.task(name="app.worker.tasks.ingest_forgejo_all_task")
def ingest_forgejo_all_task() -> list[dict]:
    db: Session = SessionLocal()
    try:
        return ingest_forgejo_all(db)
    finally:
        db.close()


@celery_app.task(name="app.worker.tasks.ingest_jenkins_all_task")
def ingest_jenkins_all_task() -> list[dict]:
    db: Session = SessionLocal()
    try:
        return ingest_jenkins_all(db)
    finally:
        db.close()


@celery_app.task(name="app.worker.tasks.ingest_taiga_all_task")
def ingest_taiga_all_task() -> list[dict]:
    db: Session = SessionLocal()
    try:
        return ingest_taiga_all(db)
    finally:
        db.close()


@celery_app.task(name="app.worker.tasks.ingest_bookstack_all_task")
def ingest_bookstack_all_task() -> list[dict]:
    db: Session = SessionLocal()
    try:
        return ingest_bookstack_all(db)
    finally:
        db.close()


@celery_app.task(name="app.worker.tasks.ingest_rss_all_task")
def ingest_rss_all_task() -> list[dict]:
    db: Session = SessionLocal()
    try:
        return ingest_rss_all(db)
    finally:
        db.close()


@celery_app.task(name="app.worker.tasks.ingest_google_workspace_all_task")
def ingest_google_workspace_all_task() -> list[dict]:
    db: Session = SessionLocal()
    try:
        return ingest_google_workspace_all(db)
    finally:
        db.close()


@celery_app.task(name="app.worker.tasks.materialize_scheduled_audits_task")
def materialize_scheduled_audits_task() -> dict:
    db: Session = SessionLocal()
    try:
        return materialize_due_scheduled_audits(db)
    finally:
        db.close()


@celery_app.task(name="app.worker.tasks.materialize_missing_monthly_zero_metrics_task")
def materialize_missing_monthly_zero_metrics_task() -> dict:
    db: Session = SessionLocal()
    try:
        return materialize_missing_monthly_zero_metrics(db)
    finally:
        db.close()


@celery_app.task(name="app.worker.tasks.refresh_event_counter_cache_task")
def refresh_event_counter_cache_task() -> dict:
    db: Session = SessionLocal()
    try:
        return {"frameworks": refresh_framework_event_counts_cache(db)}
    finally:
        db.close()


@celery_app.task(name="app.worker.tasks.send_question_webhooks_task")
def send_question_webhooks_task(thread_id: str, post_id: str) -> dict:
    """Fanout new auditor questions to external webhook destinations.

    This task is best-effort: failures are logged but do not raise by default.
    """

    cfg = OutboundWebhookConfig.from_settings()
    if not cfg.any_enabled():
        return {"enabled": False}

    tid = None
    pid = None
    try:
        tid = uuid.UUID(str(thread_id))
        pid = uuid.UUID(str(post_id))
    except Exception:
        return {"enabled": False, "error": "invalid ids"}

    db: Session = SessionLocal()
    try:
        thread = (
            db.query(EventQuestionThread).filter(EventQuestionThread.id == tid).first()
        )
        post = db.query(EventQuestionPost).filter(EventQuestionPost.id == pid).first()
        if not thread or not post:
            return {"enabled": False, "error": "thread/post not found"}

        ev = db.query(Event).filter(Event.id == thread.event_id).first()
        user = db.query(User).filter(User.id == thread.created_by_user_id).first()

        # Attachments for the first post.
        atts: list[dict] = []
        rows = (
            db.query(EventQuestionPostAttachment, Artifact)
            .join(Artifact, Artifact.id == EventQuestionPostAttachment.artifact_id)
            .filter(EventQuestionPostAttachment.post_id == post.id)
            .order_by(EventQuestionPostAttachment.created_at.asc())
            .all()
        )
        for att, art in rows:
            filename = None
            try:
                filename = (art.meta or {}).get("filename")
            except Exception:
                filename = None
            atts.append(
                {
                    "artifact_id": str(art.id),
                    "filename": filename,
                    "content_type": art.content_type,
                    "size_bytes": art.size_bytes,
                    "download_url": f"/api/v1/artifacts/{art.id}/download",
                }
            )

        # Control mappings for this event (prefer the configured default framework, but include others too).
        controls: list[dict] = []
        try:
            q = (
                db.query(ControlItem)
                .join(Mapping, Mapping.control_item_id == ControlItem.id)
                .filter(Mapping.event_id == thread.event_id)
            )

            iso = (
                q.filter(ControlItem.framework_slug == settings.default_framework_slug)
                .order_by(ControlItem.ref.asc())
                .all()
            )
            rows = iso or (
                q.order_by(
                    ControlItem.framework_slug.asc(), ControlItem.ref.asc()
                ).all()
            )
            # Keep payload bounded.
            for c in rows[:200]:
                controls.append(
                    {
                        "framework_slug": c.framework_slug,
                        "type": c.type,
                        "ref": c.ref,
                        "title": c.title,
                        "in_scope": bool(c.in_scope),
                    }
                )
        except Exception:
            controls = []

        links = build_question_links(
            event_id=str(thread.event_id), thread_id=str(thread.id)
        )

        generic = build_generic_payload(
            event={
                "id": str(ev.id) if ev else str(thread.event_id),
                "timestamp": (
                    ev.timestamp.isoformat() if (ev and ev.timestamp) else None
                ),
                "source": ev.source if ev else None,
                "system": ev.system if ev else None,
                "actor": ev.actor if ev else None,
                "action": ev.action if ev else None,
                "outcome": ev.outcome if ev else None,
                "severity": ev.severity if ev else None,
                "summary": ev.summary if ev else None,
            },
            thread={
                "id": str(thread.id),
                "event_id": str(thread.event_id),
                "status": thread.status,
                "created_by_user_id": str(thread.created_by_user_id),
                "created_by_username": user.username if user else None,
                "created_at": (
                    thread.created_at.isoformat() if thread.created_at else None
                ),
                "updated_at": (
                    thread.updated_at.isoformat() if thread.updated_at else None
                ),
            },
            post={
                "id": str(post.id),
                "thread_id": str(post.thread_id),
                "author_user_id": str(post.author_user_id),
                "author_username": user.username if user else None,
                "body": post.body,
                "created_at": post.created_at.isoformat() if post.created_at else None,
                "attachments": atts,
            },
            controls=controls,
            links=links,
        )

        try:
            res = send_question_created_webhooks(cfg, generic)
            return res
        except Exception as e:
            log.warning("question webhook fanout failed: %s", e)
            return {"enabled": True, "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="app.worker.tasks.send_question_reply_webhooks_task")
def send_question_reply_webhooks_task(thread_id: str, post_id: str) -> dict:
    """Fanout question replies to external webhook destinations.

    This is triggered for *any* reply (admin or thread author). The canonical
    payload uses type: keen.question.replied and includes a small `reply` object
    describing who replied.

    Best-effort: failures are logged but do not raise by default.
    """

    cfg = OutboundWebhookConfig.from_settings(event="reply")
    if not cfg.any_enabled():
        return {"enabled": False}

    tid = None
    pid = None
    try:
        tid = uuid.UUID(str(thread_id))
        pid = uuid.UUID(str(post_id))
    except Exception:
        return {"enabled": False, "error": "invalid ids"}

    db: Session = SessionLocal()
    try:
        thread = (
            db.query(EventQuestionThread).filter(EventQuestionThread.id == tid).first()
        )
        post = db.query(EventQuestionPost).filter(EventQuestionPost.id == pid).first()
        if not thread or not post:
            return {"enabled": False, "error": "thread/post not found"}

        ev = db.query(Event).filter(Event.id == thread.event_id).first()
        thread_author = (
            db.query(User).filter(User.id == thread.created_by_user_id).first()
        )
        post_author = db.query(User).filter(User.id == post.author_user_id).first()

        # Attachments for this reply post.
        atts: list[dict] = []
        rows = (
            db.query(EventQuestionPostAttachment, Artifact)
            .join(Artifact, Artifact.id == EventQuestionPostAttachment.artifact_id)
            .filter(EventQuestionPostAttachment.post_id == post.id)
            .order_by(EventQuestionPostAttachment.created_at.asc())
            .all()
        )
        for att, art in rows:
            filename = None
            try:
                filename = (art.meta or {}).get("filename")
            except Exception:
                filename = None
            atts.append(
                {
                    "artifact_id": str(art.id),
                    "filename": filename,
                    "content_type": art.content_type,
                    "size_bytes": art.size_bytes,
                    "download_url": f"/api/v1/artifacts/{art.id}/download",
                }
            )

        # Control mappings for this event.
        controls: list[dict] = []
        try:
            q = (
                db.query(ControlItem)
                .join(Mapping, Mapping.control_item_id == ControlItem.id)
                .filter(Mapping.event_id == thread.event_id)
            )

            iso = (
                q.filter(ControlItem.framework_slug == settings.default_framework_slug)
                .order_by(ControlItem.ref.asc())
                .all()
            )
            rows = (
                iso
                or q.order_by(
                    ControlItem.framework_slug.asc(), ControlItem.ref.asc()
                ).all()
            )
            for c in rows[:200]:
                controls.append(
                    {
                        "framework_slug": c.framework_slug,
                        "type": c.type,
                        "ref": c.ref,
                        "title": c.title,
                        "in_scope": bool(c.in_scope),
                    }
                )
        except Exception:
            controls = []

        links = build_question_links(
            event_id=str(thread.event_id), thread_id=str(thread.id)
        )

        as_role = (
            "admin"
            if (post_author and str(post_author.role).lower() == "admin")
            else "author"
        )

        generic = build_generic_payload(
            event={
                "id": str(ev.id) if ev else str(thread.event_id),
                "timestamp": (
                    ev.timestamp.isoformat() if (ev and ev.timestamp) else None
                ),
                "source": ev.source if ev else None,
                "system": ev.system if ev else None,
                "actor": ev.actor if ev else None,
                "action": ev.action if ev else None,
                "outcome": ev.outcome if ev else None,
                "severity": ev.severity if ev else None,
                "summary": ev.summary if ev else None,
            },
            thread={
                "id": str(thread.id),
                "event_id": str(thread.event_id),
                "status": thread.status,
                "created_by_user_id": str(thread.created_by_user_id),
                "created_by_username": (
                    thread_author.username if thread_author else None
                ),
                "created_at": (
                    thread.created_at.isoformat() if thread.created_at else None
                ),
                "updated_at": (
                    thread.updated_at.isoformat() if thread.updated_at else None
                ),
                "last_admin_reply_at": (
                    thread.last_admin_reply_at.isoformat()
                    if thread.last_admin_reply_at
                    else None
                ),
            },
            post={
                "id": str(post.id),
                "thread_id": str(post.thread_id),
                "author_user_id": str(post.author_user_id),
                "author_username": post_author.username if post_author else None,
                "body": post.body,
                "created_at": post.created_at.isoformat() if post.created_at else None,
                "attachments": atts,
            },
            controls=controls,
            links=links,
            event_type="keen.question.replied",
            extra={
                "reply": {
                    "as": as_role,
                }
            },
        )

        try:
            res = send_question_reply_webhooks(cfg, generic)
            return res
        except Exception as e:
            log.warning("question reply webhook fanout failed: %s", e)
            return {"enabled": True, "error": str(e)}
    finally:
        db.close()

@celery_app.task(name="app.worker.tasks.backfill_rule_task")
def backfill_rule_task(job_id: str) -> None:
    from app.db.models import RuleBackfillJob
    from app.worker.rule_backfill import process_batch
    try:
        for _ in range(20):
            if not process_batch(uuid.UUID(job_id)):
                return
        backfill_rule_task.delay(job_id)
    except Exception as exc:
        log.exception("Rule backfill %s failed", job_id)
        with SessionLocal() as db:
            job = db.get(RuleBackfillJob, uuid.UUID(job_id))
            if job and job.status not in ("completed", "cancelled"):
                job.status = "failed"
                job.error = str(exc)[:1000]
                db.commit()


@celery_app.task(name="app.worker.tasks.recover_rule_backfills_task")
def recover_rule_backfills_task() -> None:
    from app.worker.rule_backfill import recover_jobs
    for job_id in recover_jobs():
        backfill_rule_task.delay(str(job_id))
