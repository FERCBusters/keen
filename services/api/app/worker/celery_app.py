from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings


def _event_counter_refresh_seconds() -> int:
    try:
        ttl = int(getattr(settings, "aggregate_cache_ttl_seconds", 0) or 0)
    except Exception:
        ttl = 0
    return max(1, ttl or 60)


celery_app = Celery(
    "keen",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)

# Beat schedules
celery_app.conf.beat_schedule = {
    "ingest-loki-every-30m": {
        "task": "app.worker.tasks.ingest_loki_all_task",
        "schedule": crontab(minute="*/30"),
    },
    "ingest-cloudwatch-logs-every-30m": {
        "task": "app.worker.tasks.ingest_cloudwatch_logs_all_task",
        "schedule": crontab(minute="*/30"),
    },
    "ingest-github-every-110m": {
        "task": "app.worker.tasks.ingest_github_all_task",
        "schedule": crontab(minute="*/110"),
    },
    "ingest-forgejo-every-95m": {
        "task": "app.worker.tasks.ingest_forgejo_all_task",
        "schedule": crontab(minute="*/95"),
    },
    "ingest-jenkins-every-75m": {
        "task": "app.worker.tasks.ingest_jenkins_all_task",
        "schedule": crontab(minute="*/75"),
    },
    "ingest-taiga-every-25m": {
        "task": "app.worker.tasks.ingest_taiga_all_task",
        "schedule": crontab(minute="*/25"),
    },
    "ingest-bookstack-every-61m": {
        "task": "app.worker.tasks.ingest_bookstack_all_task",
        "schedule": crontab(minute="*/61"),
    },
    "ingest-rss-every-45m": {
        "task": "app.worker.tasks.ingest_rss_all_task",
        "schedule": crontab(minute="*/45"),
    },
    "ingest-google-workspace-every-30m": {
        "task": "app.worker.tasks.ingest_google_workspace_all_task",
        "schedule": crontab(minute="*/30"),
    },
    "recover-rule-backfills": {
        "task": "app.worker.tasks.recover_rule_backfills_task",
        "schedule": 60.0,
    },
    "refresh-event-counter-cache": {
        "task": "app.worker.tasks.refresh_event_counter_cache_task",
        "schedule": _event_counter_refresh_seconds(),
    },
    "materialize-scheduled-audits-daily": {
        "task": "app.worker.tasks.materialize_scheduled_audits_task",
        "schedule": crontab(hour=0, minute=15),
    },
    "materialize-effectiveness-auto-zero-metrics-daily": {
        "task": "app.worker.tasks.materialize_missing_monthly_zero_metrics_task",
        "schedule": crontab(hour=0, minute=35),
    },
}
celery_app.conf.timezone = "UTC"
