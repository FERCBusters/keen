from __future__ import annotations

import time

from celery import Celery
from celery.schedules import crontab
from celery.signals import before_task_publish
from mosp import worker as mosp_worker

from app.core.config import settings
from app.ingest.registry import HTTP_QUEUE, IMAP_QUEUE, polled


def _event_counter_refresh_seconds() -> int:
    return max(1, settings.aggregate_cache_ttl_seconds or 60)


celery_app = Celery(
    "keen",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)
# Each task's deadline, and its statement labels with KEEN_SQL_TRACE on.
mosp_worker.connect(celery_app, sql_trace=settings.sql_trace)

# Internal work runs on the default queue. HTTP polling is routed to its own
# queue for scheduled, per-connection and manual runs. Each source type
# declares itself in its own module (app.ingest.registry).
POLLED = polled()
celery_app.conf.task_routes = {
    **{source.task_name: {"queue": source.queue} for source in POLLED},
    "keen.ingest_source_connection": {"queue": HTTP_QUEUE},
    "app.worker.tasks.cloud_posture_refresh_task": {"queue": HTTP_QUEUE},
    "app.worker.tasks.cloud_activity_task": {"queue": HTTP_QUEUE},
    "app.worker.tasks.integration_run_task": {"queue": HTTP_QUEUE},
    "keen.ingest_imap_connection": {"queue": IMAP_QUEUE},
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


def run_limits(budget: int) -> dict[str, int]:
    soft = min(budget, settings.collection_run_max_seconds)
    return {"soft_time_limit": soft, "time_limit": soft + RUN_GRACE_SECONDS}


celery_app.conf.task_annotations = {
    **{source.task_name: run_limits(source.run_seconds) for source in POLLED},
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


# Beat schedules. A crontab minute step only divides the hour: "*/110" matches
# minute 0 alone, so an interval that is no divisor of 60 is given in seconds.
celery_app.conf.beat_schedule = {
    **{
        "ingest-" + source.name.replace("_", "-"): {
            "task": source.task_name,
            "schedule": (
                crontab(minute=source.schedule)
                if isinstance(source.schedule, str)
                else source.schedule
            ),
        }
        for source in POLLED
    },
    "dispatch-imap": {"task": "keen.dispatch_imap", "schedule": 300.0},
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

# The ingest-http worker runs one collection at a time, in the order they were
# queued, so sources that fall due together take their turns one after another
# and a long run delays the runs behind it without dropping them. A collection
# run notes when its source starts (connections.start_run); a queued run of a
# source that has started since it was queued finds its work done and ends at
# once, so a slow or hostile source leaves at most one effective run of each
# other source waiting, and every source keeps its turn. Each queued run notes
# when it was queued for that. A run still waiting after this long, as while
# the worker is stopped, is dropped; the source's next run collects from its
# cursor.
SCHEDULED_COLLECTION_EXPIRY = 24 * 3600
QUEUED_AT = "keen_queued_at"
COLLECTION_TASKS = frozenset(source.task_name for source in POLLED)
for _entry in celery_app.conf.beat_schedule.values():
    if (
        celery_app.conf.task_routes.get(_entry["task"], {}).get("queue")
        == "ingest-http"
    ):
        _entry.setdefault("options", {})["expires"] = SCHEDULED_COLLECTION_EXPIRY


@before_task_publish.connect
def _note_queued_at(sender=None, headers=None, **_):
    if sender in COLLECTION_TASKS:
        headers[QUEUED_AT] = time.time()
