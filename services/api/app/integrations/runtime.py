"""Background runs, progress, failure budgets and evidence provenance."""
from app.core.datetime_utils import utc_now_naive
from app.services.ingestion_pause import is_paused
import json
import uuid
import socket
import ssl
from cryptography.fernet import InvalidToken
from datetime import datetime, timedelta, date
from sqlalchemy import select, update
from app.db.models import (IntegrationCollector as Collector, IntegrationConnection as Connection,
    IntegrationRevision as Revision, IntegrationRun as Run, IsmsEffectivenessMeasure as Measure,
    IsmsEffectivenessMetricEntry as Metric)
from app.db.session import SessionLocal
from app.mapping.rules import load_rules, evaluate_by_framework
from app.core.config import settings
from app.security.redaction import redact_obj
from app.ingest.common import store_event_with_artifact
from app.ingest.connections import Connection as SourceOrigin, connection_scope
from .errors import IntegrationError
from .engine import collect, external_id, timestamp
from .schema import Definition
from .transport import connection_auth


def scrub(value, secrets):
    if isinstance(value,dict):
        return {scrub(k,secrets):scrub(v,secrets) for k,v in value.items()}
    if isinstance(value,list):
        return [scrub(v,secrets) for v in value]
    if isinstance(value,str):
        for secret in secrets:
            if secret:
                value = value.replace(secret,'[REDACTED]')
    return value


def queue_run(db, collector, preview=False):
    if is_paused(db, 'api-ingesters'):
        raise ValueError('API ingester collection is paused in Sources & Evidence Mapping')
    if db.query(Run).filter(Run.collector_id==collector.id, Run.status.in_(['queued','running'])).first():
        raise ValueError('This integration already has a queued or running job')
    rev = db.get(Revision,(collector.id,collector.live_revision)) if collector.live_revision else None
    if not preview and not rev:
        raise ValueError('Publish a revision before collecting evidence')
    run = Run(id=str(uuid.uuid4()),collector_id=collector.id,revision=collector.version if preview else rev.revision,
        preview=preview,definition=collector.draft if preview else rev.definition,
        connection_id=collector.connection_id if preview else rev.connection_id)
    db.add(run)
    db.flush()
    return run


def fail(db, run, message):
    # Only the first failure transition consumes budget, including overlapping ticks.
    changed = db.execute(update(Run).where(Run.id==run.id, Run.status.in_(['queued','running']))
        .values(status='failed',message=message,finished_at=utc_now_naive()))
    if not changed.rowcount:
        db.rollback()
        return
    if not run.preview:
        c = db.scalar(select(Collector).where(Collector.id==run.collector_id).with_for_update())
        c.failures += 1
        if c.failures >= 3:
            c.enabled = False
            run.message = message + ' Collection paused after three failed runs. Review and explicitly resume.'
    db.commit()


def run_job(run_id):
    db = SessionLocal()
    try:
        claimed = db.execute(update(Run).where(Run.id==run_id,Run.status=='queued').values(status='running',started_at=utc_now_naive()))
        db.commit()
        if not claimed.rowcount:
            return
        run = db.get(Run,run_id)
        if is_paused(db, 'api-ingesters'):
            run.status = 'cancelled'
            run.message = 'Source ingestion paused before collection began'
            run.finished_at = utc_now_naive()
            db.commit()
            return
        c = db.get(Collector,run.collector_id)
        conn = db.get(Connection,run.connection_id)
        d = Definition.model_validate(run.definition)
        auth, credential = connection_auth(conn)
        run.message = 'Fetching records and checking field mappings…'
        db.commit()
        records, cursor, pages = collect(d,conn.base_url,auth,c.cursor,run.preview)
        measure = db.get(Measure,uuid.UUID(d.measure_id)) if d.output=='measurement' else None
        if d.output=='measurement' and (not measure or measure.target_unit != d.unit):
            raise IntegrationError('Measurement destination no longer exists or its unit differs')
        source = 'integration:' + c.id
        rules = load_rules(settings.rules_path,db=db) if run.preview else None
        samples = []
        for index,item in enumerate(records):
            # Remove the actual credential even if a service echoes it under an unexpected key.
            item = scrub(item,(credential,auth.get('secret')))
            f = item['fields']
            pointer = {source:{'collector_id':c.id,'revision':run.revision,'run_id':run.id,'record_id':f['id'],'url':f.get('url')}}
            event = {k:f.get(k) for k in ('system','actor','action','outcome','severity','summary')}
            event.update(source=source,raw_pointer=pointer,normalized_payload=redact_obj(item['record']))
            if run.preview:
                samples.append({'evidence':redact_obj({**event,'timestamp':f['timestamp'],'connection_id':'api:'+str(conn.id),'connection_name':conn.name}),
                    'matches':evaluate_by_framework({**event,'connection_id':'api:'+str(conn.id)},rules,details=True),
                    'measurement':{k:f.get(k) for k in ('value','period_start','period_end')} if measure else None})
                continue
            eid = external_id(c.id,f['id'])
            origin = SourceOrigin('api:' + str(conn.id), source, conn.name, {}, {}, {})
            with connection_scope(origin):
                result = store_event_with_artifact(db, timestamp=timestamp(f['timestamp']).replace(tzinfo=None),
                    **event,external_id=eid,artifact_kind='integration_json',
                    artifact_bytes=json.dumps(item['record']).encode(),artifact_content_type='application/json',
                    artifact_key=f'integrations/{c.id}/{eid}.json',captured_by='integration:'+run.id)
            if measure:
                ref = f'integration:{c.id}:{eid}'
                # A retried evidence commit can still complete its measurement without duplicating it.
                existing = db.query(Metric).filter(Metric.measure_id==measure.id,Metric.source_reference==ref).first()
                if not existing:
                    db.add(Metric(measure_id=measure.id,recorded_at=timestamp(f['timestamp']).replace(tzinfo=None),
                        period_start=date.fromisoformat(f['period_start']),period_end=date.fromisoformat(f['period_end']),
                        metric_value=f['value'],metric_unit=d.unit,source_type='event',source_reference=ref,
                        source_event_id=uuid.UUID(result['event_id']),source_url=f.get('url'),raw_payload=redact_obj(item['record']),
                        notes=f'Collected by {c.name}; revision {run.revision}; run {run.id}'))
            if result.get('deduped'):
                run.duplicates += 1
            else:
                run.new_records += 1
            run.result = {'processed':index+1,'records':len(records),'pages':pages}
            run.message = f'Storing evidence: {index+1} of {len(records)} records processed.'
            db.commit()
        run.status,run.finished_at = 'succeeded',utc_now_naive()
        run.result = {'pages':pages,'samples':samples,'records':len(records),
            'request': {'method':d.method,'path':d.path,'query_parameters':list(d.query),'headers':list(d.headers),'has_body':d.body is not None}}
        run.message = 'Preview only: no evidence stored.' if run.preview else f'{run.new_records} new records; {run.duplicates} duplicates.'
        if not run.preview:
            c.cursor,c.last_success,c.failures = cursor,utc_now_naive(),0
        db.commit()
    except Exception as exc:
        db.rollback()
        run = db.get(Run,run_id)
        if run:
            # Exception text from arbitrary providers/libraries may contain credentials.
            safe = str(exc) if isinstance(exc,IntegrationError) else 'Collection failed; verify definition, connection and worker configuration.'
            if isinstance(exc,InvalidToken):
                safe = 'Saved credentials could not be decrypted. Check that API and worker use the original shared integration secret key.'
            elif isinstance(exc,socket.gaierror):
                safe = 'DNS lookup failed for the configured API host.'
            elif isinstance(exc,ssl.SSLError):
                safe = 'TLS verification failed. Check the upstream certificate and trust configuration.'
            elif isinstance(exc,TimeoutError):
                safe = 'The upstream API timed out. Narrow the query or check service availability.'
            elif isinstance(exc,json.JSONDecodeError):
                safe = 'The API response was not valid JSON. Check the endpoint and authentication.'
            # Redact even validation errors before persisting.
            fail(db,run,str(redact_obj(safe))[:500])
    finally:
        db.close()


def dispatch(run_id):
    from app.worker.tasks import integration_run_task
    integration_run_task.delay(run_id)


def tick():
    db = SessionLocal()
    try:
        now = utc_now_naive()
        if is_paused(db, 'api-ingesters'):
            db.execute(update(Run).where(Run.status == 'queued').values(
                status='cancelled', message='Source ingestion paused before collection began', finished_at=now))
            db.commit()
            return
        # A lost worker is surfaced, never automatically requeued in a tight loop.
        cutoff = now-timedelta(minutes=10)
        stale = db.query(Run).filter(((Run.status=='queued') & (Run.created_at<cutoff)) |
            ((Run.status=='running') & (Run.started_at<cutoff))).all()
        for run in stale:
            fail(db,run,'Worker did not finish within ten minutes. Check worker and broker health.')
        collectors = db.scalars(select(Collector).where(Collector.enabled==True,Collector.next_run<=now).with_for_update(skip_locked=True)).all()
        pending = []
        for c in collectors:
            revision = db.get(Revision,(c.id,c.live_revision))
            c.next_run = now + timedelta(minutes=revision.definition['interval_minutes'])
            try:
                pending.append(queue_run(db,c).id)
            except ValueError:
                pass
        db.commit()
        for run_id in pending:
            try:
                dispatch(run_id)
            except Exception:
                fail(db,db.get(Run,run_id),'Could not queue run; check broker health.')
    finally:
        db.close()
