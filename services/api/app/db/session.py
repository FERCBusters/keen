from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


from sqlalchemy import event, text
from sqlalchemy.orm import Session


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
