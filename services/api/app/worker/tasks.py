from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Artifact,
    ControlItem,
    Event,
    EventQuestionPost,
    EventQuestionPostAttachment,
    EventQuestionThread,
    Mapping,
    User,
)
from app.db.session import SessionLocal
from app.ingest.bookstack import ingest_bookstack_all
from app.ingest.cloudwatch_logs import ingest_cloudwatch_logs_all
from app.ingest.forgejo import ingest_forgejo_all
from app.ingest.gitea import ingest_gitea_all
from app.ingest.github import ingest_github_all
from app.ingest.gitlab import ingest_gitlab_all
from app.ingest.google_workspace import ingest_google_workspace_all
from app.ingest.jenkins import ingest_jenkins_all
from app.ingest.loki import ingest_loki_all
from app.ingest.redmine import ingest_redmine_all
from app.ingest.riskledger import ingest_riskledger_all
from app.ingest.rss import ingest_rss_all
from app.ingest.taiga import ingest_taiga_all
from app.outbound.question_webhooks import (
    OutboundWebhookConfig,
    build_generic_payload,
    build_question_links,
    send_question_created_webhooks,
    send_question_reply_webhooks,
)
from app.services.audit_schedules import materialize_due_scheduled_audits
from app.services.control_evidence_stats import refresh_framework_event_counts_cache
from app.services.effectiveness_metrics import materialize_missing_monthly_zero_metrics
from app.worker.celery_app import celery_app

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
        except Exception:
            log.warning("question webhook fanout failed")
            return {
                "enabled": True,
                "error": "Webhook delivery failed; check connectivity and configuration",
            }
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
        except Exception:
            log.warning("question reply webhook fanout failed")
            return {
                "enabled": True,
                "error": "Webhook delivery failed; check connectivity and configuration",
            }
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
        log.error("Rule backfill %s failed (%s)", job_id, type(exc).__name__)
        with SessionLocal() as db:
            job = db.get(RuleBackfillJob, uuid.UUID(job_id))
            if job and job.status not in ("completed", "cancelled"):
                job.status = "failed"
                job.error = "Rule backfill failed; check the saved rule, database and worker configuration"
                db.commit()


@celery_app.task(name="app.worker.tasks.recover_rule_backfills_task")
def recover_rule_backfills_task() -> None:
    from app.worker.rule_backfill import recover_jobs

    for job_id in recover_jobs():
        backfill_rule_task.delay(str(job_id))


@celery_app.task(
    name="app.worker.tasks.integration_run_task",
    soft_time_limit=140,
    time_limit=160,
    max_retries=0,
)
def integration_run_task(run_id):
    from app.integrations.runtime import run_job

    run_job(run_id)


@celery_app.task(name="app.worker.tasks.integration_tick_task")
def integration_tick_task():
    from app.integrations.runtime import tick

    tick()


@celery_app.task(name="app.worker.tasks.ingest_gitea_all_task")
def ingest_gitea_all_task():
    with SessionLocal() as db:
        return ingest_gitea_all(db)


@celery_app.task(name="app.worker.tasks.ingest_gitlab_all_task")
def ingest_gitlab_all_task():
    with SessionLocal() as db:
        return ingest_gitlab_all(db)


@celery_app.task(name="app.worker.tasks.ingest_redmine_all_task")
def ingest_redmine_all_task():
    with SessionLocal() as db:
        return ingest_redmine_all(db)


@celery_app.task(name="app.worker.tasks.evidence_retention_task")
def evidence_retention_task():
    from app.services.evidence_retention import run_retention

    if run_retention():
        evidence_retention_task.apply_async(countdown=1)


@celery_app.task
def security_notifications_task():
    from app.services.security_notifications import deliver_pending

    return deliver_pending()


@celery_app.task(name="app.worker.tasks.ingest_riskledger_all_task")
def ingest_riskledger_all_task():
    with SessionLocal() as db:
        return ingest_riskledger_all(db)


@celery_app.task(name="keen.ingest_source_connection")
def ingest_source_connection_task(source: str, connection_id: str):
    """Use the same entry point and global gates as scheduled polling."""
    from importlib import import_module

    from app.services.ingestion_pause import POLLING_SOURCES

    if source not in POLLING_SOURCES:
        raise ValueError("Unknown polling source")
    ingest = getattr(import_module("app.ingest." + source), "ingest_" + source + "_all")
    with SessionLocal() as db:
        return ingest(db, connection_id=connection_id)
