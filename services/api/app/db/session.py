from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import DateTime, Engine, bindparam, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.sql_trace import install_statement_labels

engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
if settings.sql_trace:
    install_statement_labels(engine)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


log = logging.getLogger(__name__)

# Session.info key for evidence objects stored by the current transaction.
_UNPUBLISHED_OBJECTS = "keen_unpublished_objects"
# A commit whose outcome the client never saw can still become visible, so
# objects from a transaction that ended without committing wait this long.
UNPUBLISHED_OBJECT_GRACE = timedelta(hours=1)
_QUEUE_STORED_OBJECTS = text(
    "INSERT INTO evidence_object_cleanup (storage_uri, next_attempt_at) "
    "VALUES (:uri, :not_before) ON CONFLICT (storage_uri) DO NOTHING"
).bindparams(bindparam("not_before", type_=DateTime))


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@event.listens_for(Session, "after_begin")
def _coordinate_evidence_purge(session, transaction, connection):
    if transaction.nested or connection.dialect.name != "postgresql":
        return
    from app.services.ingestion_pause import INGESTION_LOCK_ID

    if session.info.get("evidence_purge_batch"):
        # Wait only a bounded time for in-flight transactions to drain. The
        # already-committed job pause prevents collectors starting another write.
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": INGESTION_LOCK_ID}
        )
    else:
        connection.execute(
            text("SELECT pg_advisory_xact_lock_shared(:key)"),
            {"key": INGESTION_LOCK_ID},
        )


@event.listens_for(Session, "before_flush")
def _pause_evidence_writers(session, flush_context, instances):
    if (
        session.info.get("evidence_purge_batch")
        or session.get_bind().dialect.name != "postgresql"
    ):
        return
    # Covers adapters using the ORM directly as well as the common intake path.
    from app.db.models import Artifact, Event, Mapping

    if any(
        isinstance(row, (Event, Mapping, Artifact))
        for row in session.new | session.dirty
    ):
        from app.services.ingestion_pause import require_purge_receiving

        require_purge_receiving(session)


def queue_stored_objects(connection, uris: list[str], not_before: datetime) -> None:
    """Queue stored objects for the retention worker.

    From not_before onwards, the worker deletes each object that no record
    references.
    """
    connection.execute(
        _QUEUE_STORED_OBJECTS,
        [{"uri": uri, "not_before": not_before} for uri in uris],
    )


def track_unpublished_object(session: Session, uri: str) -> None:
    """Remember an object stored for this transaction until it commits.

    Each stored object has its own key, so an object written by a transaction
    that rolls back (or is closed without committing) would otherwise stay in
    storage with no record pointing at it, outside retention. Such objects are
    queued for the retention worker, which deletes each one after a grace period
    if nothing references it.
    """
    session.info.setdefault(_UNPUBLISHED_OBJECTS, []).append(uri)


@event.listens_for(Session, "after_commit")
def _publish_stored_objects(session):
    # Releasing a savepoint leaves its objects to the outer transaction.
    if not session.in_nested_transaction():
        session.info.pop(_UNPUBLISHED_OBJECTS, None)


@event.listens_for(Session, "after_transaction_end")
def _queue_unpublished_objects(session, transaction):
    if transaction.parent is not None:
        return  # A savepoint ended; the outer transaction decides.
    uris = session.info.pop(_UNPUBLISHED_OBJECTS, None)
    if not uris:
        return
    not_before = utc_now_naive() + UNPUBLISHED_OBJECT_GRACE
    try:
        bind = session.get_bind()
        if isinstance(bind, Engine):
            with bind.begin() as connection:
                queue_stored_objects(connection, uris, not_before)
        else:
            # A session joined to an externally managed connection queues the
            # objects in that connection's transaction.
            queue_stored_objects(bind, uris, not_before)
    except Exception:
        # The database is likely unavailable; the objects stay unreferenced.
        log.warning("Could not queue %d unpublished evidence objects", len(uris))
