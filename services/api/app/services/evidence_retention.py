"""Event retention with transactional deletion intents and persistent audit holds.

Database deletion and the storage outbox commit together. Storage is removed only
when no retained record references it. Object Lock is never bypassed.
"""

import json
import time
import uuid
from datetime import timedelta

from sqlalchemy import text

from app.core.datetime_utils import utc_now_naive as now
from app.db.session import SessionLocal
from app.services.control_evidence_stats import clear_stats_caches
from app.storage.s3 import delete_stored_object

LOCK_ID = 836210083
BATCH_SIZE = 200
# Automatic retention runs every minute. Once an automatic job completes, the
# next check waits this long unless the retention policy changes first.
RECHECK_AFTER_COMPLETION = timedelta(minutes=10)

# Audit holds keep sampled events. Additional references keep lineage and
# question attachments belonging to other records; those events can become
# eligible after the other records are removed.
UNREFERENCED = """
NOT EXISTS (SELECT 1 FROM audit_event_retention_holds h WHERE h.event_id=e.id)
AND NOT EXISTS (
    SELECT 1 FROM artifacts a JOIN artifacts child ON child.parent_artifact_id=a.id
    WHERE a.event_id=e.id AND child.event_id<>e.id)
AND NOT EXISTS (
    SELECT 1 FROM artifacts a
    JOIN event_question_post_attachments qa ON qa.artifact_id=a.id
    JOIN event_question_posts qp ON qp.id=qa.post_id
    JOIN event_question_threads qt ON qt.id=qp.thread_id
    WHERE a.event_id=e.id AND qt.event_id IS DISTINCT FROM e.id)
"""
# Diary entries are persistent records of ISMS and risk associations and
# manual observations, so no retention mode removes them.
UNPROTECTED = "\ne.source <> 'diary'\nAND " + UNREFERENCED.lstrip("\n")


def is_referenced(db, event_id) -> bool:
    """Return whether an audit hold or another record still references an event.

    Uses the retention predicate's own reference checks, so callers deleting an
    event outside retention keep the same audit and lineage guarantees.
    """
    from sqlalchemy import bindparam

    from app.db.models import Event

    statement = text(
        f"SELECT 1 FROM events e WHERE e.id = :event_id AND NOT ({UNREFERENCED})"
    ).bindparams(bindparam("event_id", type_=Event.__table__.c.id.type))
    return db.execute(statement, {"event_id": event_id}).first() is not None


def lock(db):
    # Shared by requests and worker batches; prevents duplicate jobs/settings races.
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_ID})


def policy(db):
    return dict(
        db.execute(text("SELECT * FROM evidence_retention_policy WHERE id=1"))
        .mappings()
        .one()
    )


def candidates(mode, value, cutoff, overrides=None):
    params = {"cutoff": cutoff, "global_mode": mode, "global_value": value}
    predicate = f"e.created_at <= :cutoff AND {UNPROTECTED}"
    if mode == "all":
        return predicate, {"cutoff": cutoff}
    # Keep the common global-only path index-friendly and skip disabled scans.
    if overrides == []:
        if mode == "disabled":
            return predicate + " AND false", {"cutoff": cutoff}
        if mode == "age":
            return predicate + " AND e.timestamp < :oldest", {
                "cutoff": cutoff,
                "oldest": cutoff - timedelta(days=value),
            }
        return (
            predicate
            + """ AND e.id NOT IN (
            SELECT newest.id FROM events newest
            WHERE newest.created_at<=:cutoff AND NOT EXISTS (
                SELECT 1 FROM audit_event_retention_holds h WHERE h.event_id=newest.id)
            ORDER BY newest.timestamp DESC,newest.id DESC LIMIT :keep)""",
            {"cutoff": cutoff, "keep": value},
        )
    override_table = "evidence_retention_overrides"
    if overrides is not None:
        override_table = "jsonb_to_recordset(CAST(:overrides AS jsonb)) AS supplied(scope text, key text, mode text, value bigint)"
        params["overrides"] = json.dumps(overrides)
    # Only recorded automatic mappings establish rule ownership. DISTINCT keeps
    # multiple controls/frameworks from counting the same event twice per rule.
    # Each explicit rule allowance must expire before an overlapping event goes.
    predicate += f""" AND e.id IN (
        WITH policies AS (SELECT * FROM {override_table}), rule_events AS (
            SELECT DISTINCT m.event_id, p.scope, p.key, p.mode, p.value
            FROM policies p
            JOIN mappings m ON m.method='rule' AND m.mapped_by='system'
            JOIN control_items c ON c.id=m.control_item_id
            WHERE p.scope='rule'
              AND m.rationale='auto by rule ' || p.key || ' (' || c.framework_slug || ')'
        ), assignments AS (
            SELECT event_id, scope, key, mode, value FROM rule_events
            UNION ALL
            SELECT ev.id, COALESCE(p.scope,'global'), COALESCE(p.key,''),
                   COALESCE(p.mode,:global_mode),
                   CASE WHEN p.scope IS NULL THEN CAST(:global_value AS bigint) ELSE p.value END
            FROM events ev
            LEFT JOIN policies p ON p.scope='source' AND p.key=ev.source
            WHERE NOT EXISTS (SELECT 1 FROM rule_events r WHERE r.event_id=ev.id)
        ), ranked AS (
            SELECT a.*, ev.timestamp,
                   row_number() OVER (PARTITION BY a.scope,a.key ORDER BY ev.timestamp DESC,ev.id DESC) AS position
            FROM assignments a JOIN events ev ON ev.id=a.event_id
            WHERE ev.created_at<=:cutoff AND NOT EXISTS (
                SELECT 1 FROM audit_event_retention_holds h WHERE h.event_id=ev.id)
        )
        SELECT event_id FROM ranked GROUP BY event_id HAVING bool_and(
            CASE mode WHEN 'age' THEN timestamp < CAST(:cutoff AS timestamp) - value * INTERVAL '1 day'
                      WHEN 'count' THEN position > value ELSE false END)
    )"""
    return predicate, params


def preview(db, mode, value, cutoff=None, overrides=None):
    cutoff = cutoff or now()
    total = db.execute(text("SELECT count(*) FROM events")).scalar_one()
    held = db.execute(
        text("SELECT count(DISTINCT event_id) FROM audit_event_retention_holds")
    ).scalar_one()
    where, params = candidates(mode, value, cutoff, overrides)
    count = db.execute(
        text(f"SELECT count(*) FROM events e WHERE {where}"), params
    ).scalar_one()
    return {
        "total_events": total,
        "audit_protected_events": held,
        "eligible_events": count,
        "cutoff": cutoff,
    }


def enqueue(db, mode, value, automatic, username):
    lock(db)
    active = db.execute(
        text("SELECT id FROM evidence_purge_jobs WHERE status IN ('queued','running')")
    ).scalar()
    if active:
        return str(active), False
    job_id = uuid.uuid4()
    db.execute(
        text("""INSERT INTO evidence_purge_jobs
        (id,mode,value,automatic,cutoff,requested_by,policy_overrides)
        VALUES (:id,:mode,:value,:automatic,:cutoff,:user,
        (SELECT COALESCE(jsonb_agg(jsonb_build_object('scope',scope,'key',key,'mode',mode,'value',value)), '[]'::jsonb)
         FROM evidence_retention_overrides))"""),
        {
            "id": job_id,
            "mode": mode,
            "value": value,
            "automatic": automatic,
            "cutoff": now(),
            "user": username,
        },
    )
    return str(job_id), True


def queue_object(db, uri):
    if uri:
        db.execute(
            text("""INSERT INTO evidence_object_cleanup (storage_uri) VALUES (:uri)
            ON CONFLICT (storage_uri) DO NOTHING"""),
            {"uri": uri},
        )


def _checked_since_policy_change(db, p):
    """Whether an automatic job completed recently under the current policy."""
    last = db.execute(
        text(
            "SELECT max(updated_at) FROM evidence_purge_jobs "
            "WHERE automatic AND status='complete'"
        )
    ).scalar()
    return (
        last is not None
        and last >= p["updated_at"]
        and last > now() - RECHECK_AFTER_COMPLETION
    )


def _active_job(db):
    """The queued or running purge job, enqueuing automatic retention when due.

    Called holding the retention lock. Returns None when no job is active and
    retention is disabled, or when automatic retention completed within
    ``RECHECK_AFTER_COMPLETION`` and the policy has not changed since.
    """
    job = (
        db.execute(
            text(
                "SELECT * FROM evidence_purge_jobs WHERE status IN ('queued','running')"
            )
        )
        .mappings()
        .first()
    )
    if job:
        return dict(job)
    p = policy(db)
    if (
        p["mode"] == "disabled"
        and not db.execute(
            text(
                "SELECT 1 FROM evidence_retention_overrides WHERE mode <> 'disabled' LIMIT 1"
            )
        ).scalar()
    ):
        return None
    if _checked_since_policy_change(db, p):
        return None
    enqueue(db, p["mode"], p["value"], True, "automatic retention")
    return dict(
        db.execute(text("SELECT * FROM evidence_purge_jobs WHERE status='queued'"))
        .mappings()
        .one()
    )


def _settled_without_purge(db):
    """Settle retention that needs no purge batch, under the ordinary lease.

    Called holding the retention lock. Returns True when no job is active or the
    automatic job had nothing to delete (and is now complete), and False when a
    purge batch must run. Manual jobs pause ingestion and always run under the
    exclusive lease, as does an automatic job whose last batch found events.
    """
    job = _active_job(db)
    if job is None:
        return True
    if not job["automatic"] or job["status"] == "running":
        return False
    where, params = candidates(
        job["mode"], job["value"], job["cutoff"], job["policy_overrides"]
    )
    if db.execute(
        text(f"SELECT EXISTS (SELECT 1 FROM events e WHERE {where})"), params
    ).scalar():
        return False
    db.execute(
        text("""UPDATE evidence_purge_jobs SET status='complete', updated_at=:now,
        last_error=NULL WHERE id=:id"""),
        {"now": now(), "id": job["id"]},
    )
    _prune_automatic_history(db)
    return True


def _prune_automatic_history(db):
    # Keep bounded history for automatic jobs; manual purge history is retained.
    db.execute(
        text("""DELETE FROM evidence_purge_jobs WHERE automatic AND status IN ('complete','cancelled')
        AND updated_at < :before"""),
        {"before": now() - timedelta(days=30)},
    )


def database_batch():
    """Delete a bounded batch; FK holds arbitrate concurrent audit sampling.

    Every transaction holds the shared ingestion lease, and while a batch waits
    for the exclusive lease new transactions queue behind it. Retention is
    therefore first settled under the ordinary lease: disabled retention, and an
    automatic job with nothing to delete, finish without the exclusive lease. A
    batch that proceeds selects and locks its candidates again under that lease.
    """
    with SessionLocal() as db:
        lock(db)
        settled = _settled_without_purge(db)
        db.commit()
    if settled:
        return False
    with SessionLocal() as db:
        db.info["evidence_purge_batch"] = True
        lock(db)
        job = _active_job(db)
        if job is None:
            return False
        where, params = candidates(
            job["mode"], job["value"], job["cutoff"], job["policy_overrides"]
        )
        # Lock rows before collecting storage URIs. FK insertion by audit holds
        # conflicts with this lock; a second protection check uses a fresh snapshot.
        ids = list(
            db.execute(
                text(f"""SELECT e.id FROM events e WHERE {where}
            ORDER BY e.timestamp,e.id LIMIT :batch FOR UPDATE OF e"""),
                {**params, "batch": BATCH_SIZE},
            ).scalars()
        )
        deleted = 0
        batches = set()
        for event_id in ids:
            if not db.execute(
                text(f"SELECT 1 FROM events e WHERE e.id=:id AND {UNPROTECTED}"),
                {"id": event_id},
            ).scalar():
                continue
            # Queue every artifact (raw, rendered, question attachments). The
            # transaction rolls back both these intents and deletion on failure.
            for uri in db.execute(
                text("SELECT storage_uri FROM artifacts WHERE event_id=:id"),
                {"id": event_id},
            ).scalars():
                queue_object(db, uri)
            db.execute(
                text("DELETE FROM mappings WHERE event_id=:id"), {"id": event_id}
            )
            db.execute(
                text("UPDATE artifacts SET parent_artifact_id=NULL WHERE event_id=:id"),
                {"id": event_id},
            )
            db.execute(
                text("DELETE FROM artifacts WHERE event_id=:id"), {"id": event_id}
            )
            # UEU provenance rows cascade with the event; the signed batch
            # manifests they name are removed below once no evidence uses them.
            batches.update(
                db.execute(
                    text(
                        "SELECT batch_id FROM ueu_evidence"
                        " WHERE event_id=:id AND batch_id IS NOT NULL"
                    ),
                    {"id": event_id},
                ).scalars()
            )
            db.execute(text("DELETE FROM events WHERE id=:id"), {"id": event_id})
            deleted += 1
        for batch_id in batches:
            db.execute(
                text("""DELETE FROM ueu_batches b WHERE b.id=:id
                AND NOT EXISTS (SELECT 1 FROM ueu_evidence u WHERE u.batch_id=b.id)"""),
                {"id": batch_id},
            )
        db.execute(
            text("""UPDATE evidence_purge_jobs SET deleted=deleted+:deleted,
            status=:status, updated_at=:now,last_error=NULL WHERE id=:id"""),
            {
                "deleted": deleted,
                "status": "running" if ids else "complete",
                "now": now(),
                "id": job["id"],
            },
        )
        _prune_automatic_history(db)
        db.commit()
    if deleted:
        clear_stats_caches()  # DB aggregate counters are maintained by existing triggers.
    return bool(ids)


def object_referenced(db, uri):
    # Preserve shared objects, including ISMS documents and exported audit reports.
    return bool(
        db.execute(
            text("""SELECT EXISTS (
        SELECT 1 FROM artifacts WHERE storage_uri=:uri
        UNION ALL SELECT 1 FROM isms_documents WHERE storage_uri=:uri
        UNION ALL SELECT 1 FROM bookstack_section_evidence WHERE storage_uri=:uri
        UNION ALL SELECT 1 FROM audits WHERE final_report_storage_uri=:uri
    )"""),
            {"uri": uri},
        ).scalar()
    )


def storage_batch():
    processed = 0
    started = time.monotonic()
    for _ in range(BATCH_SIZE):
        if time.monotonic() - started > 10:
            return True
        with SessionLocal() as db:
            row = (
                db.execute(
                    text("""SELECT * FROM evidence_object_cleanup
                WHERE next_attempt_at<=:now ORDER BY next_attempt_at,id
                LIMIT 1 FOR UPDATE SKIP LOCKED"""),
                    {"now": now()},
                )
                .mappings()
                .first()
            )
            if not row:
                break
            if object_referenced(db, row["storage_uri"]):
                db.execute(
                    text("""UPDATE evidence_object_cleanup SET next_attempt_at=:later,
                    last_error='Object still referenced by retained evidence or a document' WHERE id=:id"""),
                    {"later": now() + timedelta(hours=1), "id": row["id"]},
                )
            else:
                try:
                    delete_stored_object(row["storage_uri"])
                except Exception as exc:
                    # Do not persist provider responses, credentials or signed URLs.
                    code = getattr(exc, "response", {}).get("Error", {}).get("Code")
                    error = (
                        "Stored S3 URI names no version in a versioned bucket; operator must resolve the exact object version"
                        if type(exc).__name__ == "UnpinnedVersionedObject"
                        else f"Storage deletion failed ({code or type(exc).__name__}); check permissions, Object Lock and connectivity"
                    )
                    attempts = row["attempts"] + 1
                    db.execute(
                        text("""UPDATE evidence_object_cleanup SET attempts=:n,
                        next_attempt_at=:later,last_error=:error WHERE id=:id"""),
                        {
                            "n": attempts,
                            "later": now()
                            + timedelta(
                                seconds=min(86400, 60 * 2 ** min(attempts, 11))
                            ),
                            "error": error[:300],
                            "id": row["id"],
                        },
                    )
                else:
                    db.execute(
                        text("DELETE FROM evidence_object_cleanup WHERE id=:id"),
                        {"id": row["id"]},
                    )
            db.commit()
            processed += 1
    return processed == BATCH_SIZE


def run_retention():
    more = False
    try:
        more = database_batch()
    except Exception as exc:
        with SessionLocal() as db:
            db.execute(
                text("""UPDATE evidence_purge_jobs SET last_error=:error,updated_at=:now
                WHERE status IN ('queued','running')"""),
                {
                    "error": f"Database cleanup failed ({type(exc).__name__}); worker will retry",
                    "now": now(),
                },
            )
            db.commit()
        # Continue storage cleanup even if the current DB batch needs attention.
    more_storage = storage_batch()
    return more or more_storage
