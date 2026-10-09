"""Bounded, resumable backfill of one saved mapping rule."""
from __future__ import annotations
from app.core.datetime_utils import utc_now_naive

import uuid
import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import ControlItem, Event, Mapping, ManagedConfiguration, RuleBackfillJob
from app.db.session import SessionLocal
from app.mapping.rules import evaluate_by_framework, parse_rules
from app.mapping.collector import collector_pointer_filter
from app.services.control_evidence_stats import clear_stats_caches

log = logging.getLogger(__name__)
BATCH_SIZE = 200


def rule_owned_event_ids(rule_id: str):
    """Identify exact system-rule provenance, never manual/imported mappings."""
    return (select(Mapping.event_id).join(ControlItem, ControlItem.id == Mapping.control_item_id)
            .where(Mapping.method == "rule", Mapping.mapped_by == "system",
                   Mapping.rationale == "auto by rule " + rule_id + " (" + ControlItem.framework_slug + ")"))


def process_batch(job_id: uuid.UUID) -> bool:
    """Commit mappings and cursor together; return True if more work remains."""
    with SessionLocal() as db:
        if db.get_bind().dialect.name == 'postgresql':
            from sqlalchemy import text
            db.execute(text('SELECT pg_advisory_xact_lock_shared(1262830926)'))
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
        scope = Event.source == job.source
        if identity:
            scope = and_(scope, Event.raw_pointer.contains(collector_pointer_filter(identity)))
        # Also revisit prior matches when the source/collector or conditions changed.
        scope = or_(scope, Event.id.in_(rule_owned_event_ids(job.rule_id)))
        if job.total_estimate == 0:
            job.total_estimate = int(db.query(func.count(Event.id)).filter(
                scope, Event.created_at <= job.cutoff).scalar() or 0)
        rule = parse_rules({"rules": [job.rule_document]})
        if len(rule) != 1:
            raise ValueError("The saved rule cannot be parsed")
        active_rules = parse_rules(current.document)
        q = (db.query(Event)
             .filter(scope, Event.created_at <= job.cutoff)
             .order_by(Event.timestamp.asc(), Event.id.asc()))
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
        mappings = (db.query(Mapping, ControlItem.framework_slug, ControlItem.ref)
                    .join(ControlItem, ControlItem.id == Mapping.control_item_id)
                    .filter(Mapping.event_id.in_(event_ids))
                    .order_by(Mapping.id).with_for_update(of=Mapping).all())
        existing = {(mapping.event_id, mapping.control_item_id)
                    for mapping, _, _ in mappings}
        owned = {}
        for mapping, framework, ref in mappings:
            if (mapping.method == "rule" and mapping.mapped_by == "system"
                    and mapping.rationale == f"auto by rule {job.rule_id} ({framework})"):
                owned.setdefault(mapping.event_id, []).append((mapping, framework, ref))
        created = 0
        changed = False
        matched = 0
        for event in events:
            event_data = {
                "source": event.source, "system": event.system, "actor": event.actor,
                "action": event.action, "outcome": event.outcome, "severity": event.severity,
                "summary": event.summary, "raw_pointer": event.raw_pointer,
                "normalized_payload": event.normalized_payload}
            result = evaluate_by_framework(event_data, rule, details=True)
            if result:
                matched += 1
            if event.id in owned:
                supported = {(framework, hit["ref"]): hit
                             for framework, hits in evaluate_by_framework(
                                 event_data, active_rules, details=True).items()
                             for hit in hits}
                for mapping, framework, ref in owned[event.id]:
                    hit = supported.get((framework, ref))
                    if hit is None:
                        db.delete(mapping)
                        existing.discard((event.id, mapping.control_item_id))
                        changed = True
                    elif (mapping.confidence, mapping.rationale) != (hit["confidence"], hit["rationale"]):
                        # Another saved rule may still justify this control.
                        mapping.confidence = hit["confidence"]
                        mapping.rationale = hit["rationale"]
                        changed = True
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
        if created or changed:
            try:
                clear_stats_caches()
            except Exception:
                log.warning("Could not clear stats cache after backfill batch", exc_info=True)
        return job.status == "running"


def recover_jobs():
    """Reschedule jobs after a worker or broker interruption."""
    with SessionLocal() as db:
        cutoff = utc_now_naive() - timedelta(minutes=5)
        return [job.id for job in db.query(RuleBackfillJob)
                .filter(or_(RuleBackfillJob.status == "queued",
                            and_(RuleBackfillJob.status == "running",
                                 RuleBackfillJob.updated_at < cutoff)))
                .order_by(RuleBackfillJob.created_at).limit(20).all()]
