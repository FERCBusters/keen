"""Event retention with transactional deletion intents and persistent audit holds.

Database deletion and the storage outbox commit together. Storage is removed only
when no retained record references it. Object Lock is never bypassed.
"""
from datetime import datetime, timedelta, timezone
import uuid
import time

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.control_evidence_stats import clear_stats_caches
from app.storage.s3 import delete_stored_object

LOCK_ID = 836210083
BATCH_SIZE = 200

# Additional references protect lineage and question attachments belonging to
# other records. They can become eligible after those records are removed.
UNPROTECTED = """
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


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def lock(db):
    # Shared by requests and worker batches; prevents duplicate jobs/settings races.
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': LOCK_ID})


def policy(db):
    return dict(db.execute(text('SELECT * FROM evidence_retention_policy WHERE id=1')).mappings().one())


def candidates(mode, value, cutoff):
    params = {'cutoff': cutoff}
    predicate = f'e.created_at <= :cutoff AND {UNPROTECTED}'
    if mode == 'age':
        params['oldest'] = cutoff - timedelta(days=value)
        predicate += ' AND e.timestamp < :oldest'
    if mode == 'count':
        params['keep'] = value
        # Count all unsampled events, even those additionally held by lineage.
        # Audit-held events are additional to the count allowance.
        predicate += ''' AND e.id NOT IN (
            SELECT newest.id FROM events newest
            WHERE newest.created_at <= :cutoff AND NOT EXISTS (
                SELECT 1 FROM audit_event_retention_holds h WHERE h.event_id=newest.id)
            ORDER BY newest.timestamp DESC, newest.id DESC LIMIT :keep)'''
    return predicate, params


def preview(db, mode, value, cutoff=None):
    cutoff = cutoff or now()
    total = db.execute(text('SELECT count(*) FROM events')).scalar_one()
    held = db.execute(text('SELECT count(DISTINCT event_id) FROM audit_event_retention_holds')).scalar_one()
    count = 0
    if mode != 'disabled':
        where, params = candidates(mode, value, cutoff)
        count = db.execute(text(f'SELECT count(*) FROM events e WHERE {where}'), params).scalar_one()
    return {'total_events': total, 'audit_protected_events': held,
            'eligible_events': count, 'cutoff': cutoff}


def enqueue(db, mode, value, automatic, username):
    lock(db)
    active = db.execute(text("SELECT id FROM evidence_purge_jobs WHERE status IN ('queued','running')")).scalar()
    if active:
        return str(active), False
    job_id = uuid.uuid4()
    db.execute(text('''INSERT INTO evidence_purge_jobs
        (id,mode,value,automatic,cutoff,requested_by) VALUES (:id,:mode,:value,:automatic,:cutoff,:user)'''),
        {'id': job_id, 'mode': mode, 'value': value, 'automatic': automatic,
         'cutoff': now(), 'user': username})
    return str(job_id), True


def queue_object(db, uri):
    if uri:
        db.execute(text('''INSERT INTO evidence_object_cleanup (storage_uri) VALUES (:uri)
            ON CONFLICT (storage_uri) DO NOTHING'''), {'uri': uri})


def database_batch():
    """Delete a bounded batch; FK holds arbitrate concurrent audit sampling."""
    with SessionLocal() as db:
        db.info["evidence_purge_batch"] = True
        lock(db)
        job = db.execute(text("SELECT * FROM evidence_purge_jobs WHERE status IN ('queued','running')")).mappings().first()
        if not job:
            p = policy(db)
            if p['mode'] == 'disabled':
                return False
            enqueue(db, p['mode'], p['value'], True, 'automatic retention')
            job = db.execute(text("SELECT * FROM evidence_purge_jobs WHERE status='queued'")).mappings().one()
        job = dict(job)
        where, params = candidates(job['mode'], job['value'], job['cutoff'])
        # Lock rows before collecting storage URIs. FK insertion by audit holds
        # conflicts with this lock; a second protection check uses a fresh snapshot.
        ids = list(db.execute(text(f'''SELECT e.id FROM events e WHERE {where}
            ORDER BY e.timestamp,e.id LIMIT :batch FOR UPDATE OF e'''),
            {**params, 'batch': BATCH_SIZE}).scalars())
        deleted = 0
        for event_id in ids:
            if not db.execute(text(f'SELECT 1 FROM events e WHERE e.id=:id AND {UNPROTECTED}'), {'id': event_id}).scalar():
                continue
            # Queue every artifact (raw, rendered, question attachments). The
            # transaction rolls back both these intents and deletion on failure.
            for uri in db.execute(text('SELECT storage_uri FROM artifacts WHERE event_id=:id'), {'id': event_id}).scalars():
                queue_object(db, uri)
            db.execute(text('DELETE FROM mappings WHERE event_id=:id'), {'id': event_id})
            db.execute(text('UPDATE artifacts SET parent_artifact_id=NULL WHERE event_id=:id'), {'id': event_id})
            db.execute(text('DELETE FROM artifacts WHERE event_id=:id'), {'id': event_id})
            db.execute(text('DELETE FROM events WHERE id=:id'), {'id': event_id})
            deleted += 1
        db.execute(text('''UPDATE evidence_purge_jobs SET deleted=deleted+:deleted,
            status=:status, updated_at=:now,last_error=NULL WHERE id=:id'''),
            {'deleted': deleted, 'status': 'running' if ids else 'complete', 'now': now(), 'id': job['id']})
        # Keep bounded history for automatic jobs; manual purge history is retained.
        db.execute(text("""DELETE FROM evidence_purge_jobs WHERE automatic AND status IN ('complete','cancelled')
            AND updated_at < :before"""), {'before': now()-timedelta(days=30)})
        db.commit()
    if deleted:
        clear_stats_caches()  # DB aggregate counters are maintained by existing triggers.
    return bool(ids)


def object_referenced(db, uri):
    # Preserve shared objects, including ISMS documents and exported audit reports.
    return bool(db.execute(text('''SELECT EXISTS (
        SELECT 1 FROM artifacts WHERE storage_uri=:uri
        UNION ALL SELECT 1 FROM isms_documents WHERE storage_uri=:uri
        UNION ALL SELECT 1 FROM bookstack_section_evidence WHERE storage_uri=:uri
        UNION ALL SELECT 1 FROM audits WHERE final_report_storage_uri=:uri
    )'''), {'uri': uri}).scalar())


def storage_batch():
    processed = 0
    started = time.monotonic()
    for _ in range(BATCH_SIZE):
        if time.monotonic() - started > 10:
            return True
        with SessionLocal() as db:
            row = db.execute(text('''SELECT * FROM evidence_object_cleanup
                WHERE next_attempt_at<=:now ORDER BY next_attempt_at,id
                LIMIT 1 FOR UPDATE SKIP LOCKED'''), {'now': now()}).mappings().first()
            if not row:
                break
            if object_referenced(db, row['storage_uri']):
                db.execute(text('''UPDATE evidence_object_cleanup SET next_attempt_at=:later,
                    last_error='Object still referenced by retained evidence or a document' WHERE id=:id'''),
                    {'later': now()+timedelta(hours=1), 'id': row['id']})
            else:
                try:
                    delete_stored_object(row['storage_uri'])
                except Exception as exc:
                    # Do not persist provider responses, credentials or signed URLs.
                    code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
                    error = ('Legacy versioned S3 URI has no version ID; operator must resolve the exact object version'
                             if type(exc).__name__ == 'UnpinnedVersionedObject' else
                             f'Storage deletion failed ({code or type(exc).__name__}); check permissions, Object Lock and connectivity')
                    attempts = row['attempts'] + 1
                    db.execute(text('''UPDATE evidence_object_cleanup SET attempts=:n,
                        next_attempt_at=:later,last_error=:error WHERE id=:id'''),
                        {'n': attempts, 'later': now()+timedelta(seconds=min(86400, 60*2**min(attempts,11))),
                         'error': error[:300], 'id': row['id']})
                else:
                    db.execute(text('DELETE FROM evidence_object_cleanup WHERE id=:id'), {'id': row['id']})
            db.commit()
            processed += 1
    return processed == BATCH_SIZE


def run_retention():
    more = False
    try:
        more = database_batch()
    except Exception as exc:
        with SessionLocal() as db:
            db.execute(text('''UPDATE evidence_purge_jobs SET last_error=:error,updated_at=:now
                WHERE status IN ('queued','running')'''),
                {'error': f'Database cleanup failed ({type(exc).__name__}); worker will retry', 'now': now()})
            db.commit()
        # Continue storage cleanup even if the current DB batch needs attention.
    more_storage = storage_batch()
    return more or more_storage
