from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.worker.sql_trace import connect_task_labels


def _event_counter_refresh_seconds() -> int:
    return max(1, settings.aggregate_cache_ttl_seconds or 60)


celery_app = Celery(
    "keen",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)
if settings.sql_trace:
    connect_task_labels()

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
# The ingest-http worker runs one task at a time, so a collection that runs on
# would hold back every other collector and custom API run, and a hostile or
# failing source would hold them back for as long as its runs last. Each
# request already ends within its transport budget (two minutes for built-in
# collectors). Each source type's scheduled run also ends within its own
# budget, KEEN_<SOURCE>_RUN_SECONDS, set below the interval between its runs,
# and KEEN_COLLECTION_RUN_MAX_SECONDS caps every budget. Collectors store and
# commit each input, page, chunk or day as they go, so a run stopped at its
# budget keeps what it collected and the next run continues from its cursor.
# The soft limit ends the whole run of a source's connections, and its next run
# starts one connection further along, so the connections a stopped run missed
# are not always last; the hard limit follows after a short grace. A manual run
# of one connection has the general cap. Cloud posture tasks and custom API
# runs keep their own, shorter limits.
RUN_GRACE_SECONDS = 120
SOURCE_RUN_SECONDS = {
    "elasticsearch": settings.elasticsearch_run_seconds,
    "loki": settings.loki_run_seconds,
    "cloudwatch_logs": settings.cloudwatch_logs_run_seconds,
    "github": settings.github_run_seconds,
    "forgejo": settings.forgejo_run_seconds,
    "gitea": settings.gitea_run_seconds,
    "gitlab": settings.gitlab_run_seconds,
    "redmine": settings.redmine_run_seconds,
    "riskledger": settings.riskledger_run_seconds,
    "jenkins": settings.jenkins_run_seconds,
    "taiga": settings.taiga_run_seconds,
    "bookstack": settings.bookstack_run_seconds,
    "rss": settings.rss_run_seconds,
    "google_workspace": settings.google_workspace_run_seconds,
}


def run_limits(budget: int) -> dict[str, int]:
    soft = min(budget, settings.collection_run_max_seconds)
    return {"soft_time_limit": soft, "time_limit": soft + RUN_GRACE_SECONDS}


celery_app.conf.task_annotations = {
    **{
        f"app.worker.tasks.ingest_{source}_all_task": run_limits(budget)
        for source, budget in SOURCE_RUN_SECONDS.items()
    },
    "keen.ingest_source_connection": run_limits(settings.collection_run_max_seconds),
}
# Every other task ends within an outer bound too. Socket timeouts restart with
# each byte, so a server that answers a byte at a time (an operator's mail or
# directory server, a cloud SDK endpoint) could otherwise hold a worker for as
# long as it keeps answering. Long internal work, such as a rule backfill, runs
# as a series of short tasks.
TASK_SECONDS = 3300
celery_app.conf.task_soft_time_limit = TASK_SECONDS
celery_app.conf.task_time_limit = TASK_SECONDS + RUN_GRACE_SECONDS

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
    # Agent traffic: rotate KEEN's encryption key and erase expired ones.
    "agent-server-keys-hourly": {
        "task": "app.worker.tasks.agent_server_keys_task",
        "schedule": crontab(minute=7),
    },
    # Suggest mapping rules from the latest unmapped events.
    "rule-suggestions-every-6h": {
        "task": "app.worker.tasks.rule_suggestions_task",
        "schedule": crontab(minute=50, hour="*/6"),
    },
    # Remove inbox notifications read long ago.
    "inbox-housekeeping-daily": {
        "task": "app.worker.tasks.inbox_housekeeping_task",
        "schedule": crontab(hour=1, minute=40),
    },
    # Remove audit-trail records older than KEEN_AUDIT_LOG_RETENTION_DAYS.
    "audit-log-retention-daily": {
        "task": "app.worker.tasks.audit_log_retention_task",
        "schedule": crontab(hour=1, minute=50),
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

# A scheduled collection waits at most this long for the ingest-http worker.
# While one long collection holds the worker, the runs scheduled behind it
# expire, and each source's next scheduled run collects from its cursor, so no
# backlog builds up behind a slow or hostile source.
SCHEDULED_COLLECTION_EXPIRY = 600
for _entry in celery_app.conf.beat_schedule.values():
    if (
        celery_app.conf.task_routes.get(_entry["task"], {}).get("queue")
        == "ingest-http"
    ):
        _entry.setdefault("options", {})["expires"] = SCHEDULED_COLLECTION_EXPIRY
