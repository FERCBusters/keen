"""Audit-trail retention: removes request and action records past their age.

Records older than ``KEEN_AUDIT_LOG_RETENTION_DAYS`` are deleted oldest first,
a bounded batch per short transaction, so no deletion holds locks or a
transaction open for long. 0 keeps every record.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.datetime_utils import utc_now_naive
from app.db.models import AuditLog
from app.db.session import SessionLocal

BATCH_SIZE = 1000
# A run stops after this many full batches and asks to be queued again.
BATCHES_PER_RUN = 20


def remove_expired(now: datetime | None = None) -> tuple[int, bool]:
    """Delete expired records; return how many, and whether more remain."""
    days = settings.audit_log_retention_days
    if not days:
        return 0, False
    cutoff = (now or utc_now_naive()) - timedelta(days=days)
    removed = 0
    batch = (
        select(AuditLog.id)
        .where(AuditLog.ts < cutoff)
        .order_by(AuditLog.ts)
        .limit(BATCH_SIZE)
        .scalar_subquery()
    )
    for _ in range(BATCHES_PER_RUN):
        with SessionLocal() as db:
            deleted = (
                db.query(AuditLog)
                .filter(AuditLog.id.in_(batch))
                .delete(synchronize_session=False)
            )
            db.commit()
        removed += deleted
        if deleted < BATCH_SIZE:
            return removed, False
    return removed, True
