"""Operator pause controls, independent of environment enablement and cursors."""

from functools import wraps

from myf.services.errors import DomainError
from sqlalchemy import select, text

from app.db.models import SourceIngestionState
from app.ingest import registry
from app.ingest.cloud import KEYS as CLOUD_SOURCES


def pausable_sources() -> frozenset[str]:
    """Every source an administrator can pause.

    Each declared source type, the cloud posture providers (pausing one pauses
    its settings checks and its activity) and the two that are not source
    modules: agents and custom API ingesters.
    """
    return (
        frozenset(source.name for source in registry.sources())
        | frozenset(CLOUD_SOURCES)
        | {"keen-agent", "api-ingesters"}
    )


# Separate from operator pause flags: completing a purge must never resume a
# source that an administrator had explicitly paused.
INGESTION_LOCK_ID = 836210090


def purge_pause_status(db):
    row = (
        db.execute(
            text("""SELECT
        EXISTS (SELECT 1 FROM evidence_purge_jobs WHERE NOT automatic
                AND status IN ('queued','running')) AS active,
        (SELECT max(updated_at) + interval '5 minutes' FROM evidence_purge_jobs
         WHERE NOT automatic AND status IN ('complete','cancelled')) AS resume_at,
        clock_timestamp() AT TIME ZONE 'UTC' AS current_time""")
        )
        .mappings()
        .one()
    )
    return {
        "paused": bool(
            row["active"]
            or (row["resume_at"] and row["resume_at"] > row["current_time"])
        ),
        "resume_at": None if row["active"] else row["resume_at"],
    }


def require_purge_receiving(db):
    if purge_pause_status(db)["paused"]:
        raise DomainError(
            503,
            "Ingestion is paused for evidence purge and its five-minute cooldown; retry later.",
            headers={"Retry-After": "60"},
        )


def is_paused(db, source):
    if purge_pause_status(db)["paused"]:
        return True
    return (
        db.scalar(
            select(SourceIngestionState.paused).where(
                SourceIngestionState.source == source
            )
        )
        is True
    )


def require_receiving(db, source):
    from app.core.config import settings

    if source == "webhooks" and not settings.webhooks_enabled:
        raise DomainError(503, "Webhook ingestion is disabled")
    if is_paused(db, source):
        raise DomainError(
            503,
            "Source ingestion is paused by an administrator; retry later.",
            headers={"Retry-After": "60"},
        )


def pausable(source):
    """Gate both scheduled and manually invoked adapter runs before any I/O."""

    def decorate(function):
        @wraps(function)
        def wrapped(db, *args, **kwargs):
            if db is not None and is_paused(db, source):
                return [
                    {
                        "source": source,
                        "skipped": True,
                        "paused": True,
                        "reason": "Ingestion paused by an administrator",
                    }
                ]
            return function(db, *args, **kwargs)

        return wrapped

    return decorate
