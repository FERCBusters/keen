from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings


def _event_counter_refresh_seconds() -> int:
    return max(1, settings.aggregate_cache_ttl_seconds or 60)


celery_app = Celery(
    "keen",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)

# Internal work runs on the default queue. HTTP polling is routed to its own
# queue for scheduled, per-connection and manual runs.
HTTP_INGEST_SOURCES = (
    "elasticsearch",
    "loki",
    "cloudwatch_logs",
    "github",
    "forgejo",
    "gitea",
    "gitlab",
    "redmine",
    "riskledger",
    "jenkins",
    "taiga",
    "bookstack",
    "rss",
    "google_workspace",
)
celery_app.conf.task_routes = {
    **{
        f"app.worker.tasks.ingest_{source}_all_task": {"queue": "ingest-http"}
        for source in HTTP_INGEST_SOURCES
    },
    "keen.ingest_source_connection": {"queue": "ingest-http"},
    "app.worker.tasks.cloud_posture_refresh_task": {"queue": "ingest-http"},
    "app.worker.tasks.cloud_activity_task": {"queue": "ingest-http"},
    "app.worker.tasks.integration_run_task": {"queue": "ingest-http"},
    "keen.ingest_imap_connection": {"queue": "imap"},
}

# Beat schedules
celery_app.conf.beat_schedule = {
    "dispatch-imap": {"task": "keen.dispatch_imap", "schedule": 300.0},
    "ingest-gitea": {
        "task": "app.worker.tasks.ingest_gitea_all_task",
        "schedule": 5700.0,
    },
    "ingest-riskledger": {
        "task": "app.worker.tasks.ingest_riskledger_all_task",
        "schedule": float(settings.riskledger_poll_seconds),
    },
    "ingest-redmine": {
        "task": "app.worker.tasks.ingest_redmine_all_task",
        "schedule": float(settings.redmine_poll_seconds),
    },
    "ingest-gitlab": {
        "task": "app.worker.tasks.ingest_gitlab_all_task",
        "schedule": 5700.0,
    },
    "ingest-elasticsearch-every-30m": {
        "task": "app.worker.tasks.ingest_elasticsearch_all_task",
        "schedule": crontab(minute="*/30"),
    },
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
    # Each provider refreshes once KEEN_CLOUD_POSTURE_POLL_SECONDS have passed.
    "cloud-posture-refresh": {
        "task": "app.worker.tasks.cloud_posture_refresh_task",
        "schedule": 900.0,
    },
    "cloud-activity": {
        "task": "app.worker.tasks.cloud_activity_task",
        "schedule": float(settings.cloud_activity_poll_seconds),
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
    "quiz-rounds-daily": {
        "task": "app.worker.tasks.quiz_rounds_task",
        "schedule": crontab(hour=0, minute=20),
    },
    # Each audit attendee and quiz participant is reminded at most once.
    "send-reminders-daily": {
        "task": "app.worker.tasks.send_reminders_task",
        "schedule": crontab(hour=6, minute=45),
    },
    "materialize-effectiveness-auto-zero-metrics-daily": {
        "task": "app.worker.tasks.materialize_missing_monthly_zero_metrics_task",
        "schedule": crontab(hour=0, minute=35),
    },
}
celery_app.conf.timezone = "UTC"

celery_app.conf.beat_schedule["integration-schedules"] = {
    "task": "app.worker.tasks.integration_tick_task",
    "schedule": 60.0,
}

celery_app.conf.beat_schedule["evidence-retention"] = {
    "task": "app.worker.tasks.evidence_retention_task",
    "schedule": 60.0,
}

celery_app.conf.beat_schedule["security-notifications"] = {
    "task": "app.worker.tasks.security_notifications_task",
    "schedule": 60.0,
}
