"""Bounded, resumable backfill of one saved mapping rule."""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import ControlItem, Event, Mapping, ManagedConfiguration, RuleBackfillJob
from app.db.session import SessionLocal
from app.mapping.rules import evaluate_by_framework, parse_rules
from app.mapping.collector import collector_pointer_filter
from app.services.control_evidence_stats import clear_stats_caches

log = logging.getLogger(__name__)
BATCH_SIZE = 200


def process_batch(job_id: uuid.UUID) -> bool:
    """Commit mappings and cursor together; return True if more work remains."""
    with SessionLocal() as db:
        job = (db.query(RuleBackfillJob).filter_by(id=job_id)
               .with_for_update(skip_locked=True).one_or_none())
        if job is None or job.status in ("completed", "cancelled", "failed", "superseded"):
            return False
        current = db.get(ManagedConfiguration, "rules")
        if current is None or current.version != job.rules_version:
            active = next((item for item in (current.document.get("rules") or [])
                           if item.get("id") == job.rule_id), None) if current else None
            if active != job.rule_document:
                job.status = "superseded"
                db.commit()
                return False
            job.rules_version = current.version
        identity = job.rule_document.get("when", {}).get("collector")
        if job.total_estimate == 0:
            count_q = db.query(func.count(Event.id)).filter(
                Event.source == job.source, Event.created_at <= job.cutoff)
            if identity:
                count_q = count_q.filter(Event.raw_pointer.contains(collector_pointer_filter(identity)))
            job.total_estimate = int(count_q.scalar() or 0)
        rule = parse_rules({"rules": [job.rule_document]})
        if len(rule) != 1:
            raise ValueError("The saved rule cannot be parsed")
        q = (db.query(Event)
             .filter(Event.source == job.source, Event.created_at <= job.cutoff)
             .order_by(Event.timestamp.asc(), Event.id.asc()))
        if identity:
            q = q.filter(Event.raw_pointer.contains(collector_pointer_filter(identity)))
        if job.cursor_timestamp is not None:
            q = q.filter(or_(Event.timestamp > job.cursor_timestamp,
                             and_(Event.timestamp == job.cursor_timestamp, Event.id > job.cursor_id)))
        events = q.limit(BATCH_SIZE).all()
        if not events:
            job.status = "completed"
            db.commit()
            return False
        targets = {(row.framework_slug, row.ref): row.id
                   for row in db.query(ControlItem).filter(
                       or_(*[and_(ControlItem.framework_slug == t.framework_slug,
                                  ControlItem.ref == t.ref) for t in rule[0].targets])).all()}
        missing = {(t.framework_slug, t.ref) for t in rule[0].targets} - set(targets)
        if missing:
            raise ValueError(f"Framework targets were removed: {sorted(missing)}")
        event_ids = [event.id for event in events]
        existing = {(event_id, control_id) for event_id, control_id in
                    db.query(Mapping.event_id, Mapping.control_item_id)
                    .filter(Mapping.event_id.in_(event_ids)).all()}
        created = 0
        matched = 0
        for event in events:
            result = evaluate_by_framework({
                "source": event.source, "system": event.system, "actor": event.actor,
                "action": event.action, "outcome": event.outcome, "severity": event.severity,
                "summary": event.summary, "raw_pointer": event.raw_pointer,
                "normalized_payload": event.normalized_payload}, rule, details=True)
            if result:
                matched += 1
            for framework, hits in result.items():
                for hit in hits:
                    control_id = targets[(framework, hit["ref"])]
                    key = (event.id, control_id)
                    if key in existing:
                        continue
                    stmt = (pg_insert(Mapping).values(
                        id=uuid.uuid4(), event_id=event.id, control_item_id=control_id,
                        confidence=hit["confidence"], method="rule",
                        rationale=hit["rationale"], mapped_by="system")
                        .on_conflict_do_nothing(index_elements=["event_id", "control_item_id"])
                        .returning(Mapping.id))
                    if db.execute(stmt).scalar() is not None:
                        created += 1
                    existing.add(key)
        job.cursor_timestamp = events[-1].timestamp
        job.cursor_id = events[-1].id
        job.examined += len(events)
        job.matched += matched
        job.created_mappings += created
        job.status = "running" if len(events) == BATCH_SIZE else "completed"
        db.commit()
        if created:
            try:
                clear_stats_caches()
            except Exception:
                log.warning("Could not clear stats cache after backfill batch", exc_info=True)
        return job.status == "running"


def recover_jobs():
    """Reschedule jobs after a worker or broker interruption."""
    with SessionLocal() as db:
        cutoff = datetime.utcnow() - timedelta(minutes=5)
        return [job.id for job in db.query(RuleBackfillJob)
                .filter(or_(RuleBackfillJob.status == "queued",
                            and_(RuleBackfillJob.status == "running",
                                 RuleBackfillJob.updated_at < cutoff)))
                .order_by(RuleBackfillJob.created_at).limit(20).all()]
