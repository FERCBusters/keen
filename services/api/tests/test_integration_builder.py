"""Collector contract, network boundary and durable lifecycle regressions."""
from tests.db_helpers import create_sqlite_schema
import json
import socket
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from fastapi import HTTPException
from cryptography.fernet import Fernet
from app.integrations.schema import Definition, pointer
from app.integrations.engine import collect, normalize, external_id
from app.integrations.errors import IntegrationError
from app.integrations import transport, runtime
from app.integrations.templates import templates
from app.db.models import (Base, IntegrationCollector as Collector, IntegrationConnection as Connection,
    IntegrationRun as Run, IntegrationRevision as Revision, Event, IsmsEffectivenessMeasure as Measure,
    IsmsEffectivenessMetricEntry as Metric)
from app.api.routes import integrations as api


def record(n=1):
    return {'id':n,'created_at':'2026-01-01T12:00:00Z','title':f'Check {n}'}


def test_json_pointer_and_normalization():
    assert pointer({'a/b':{'x~y':[4]}},'/a~1b/x~0y/0') == 4
    assert pointer({},'/missing') is None
    d=Definition()
    assert normalize(record(),d)['timestamp']=='2026-01-01T12:00:00+00:00'
    with pytest.raises(ValueError): normalize({**record(),'created_at':'2026-01-01'},d)
    with pytest.raises(ValueError): normalize({'id':1},d)
    assert external_id('a','1')!=external_id('b','1')


@pytest.mark.parametrize('paging,payloads,expected',[
    ({'mode':'page','size':1}, [[record(1)],[record(2)],[]], [1,2,3]),
    ({'mode':'offset','size':1,'start':0}, [[record(1)],[record(2)],[]], [0,1,2]),
    ({'mode':'cursor','next_path':'/next'}, [{'items':[record(1)],'next':'abc'},{'items':[record(2)]}], [None,'abc']),
])
def test_pagination_modes(paging,payloads,expected):
    calls=[]
    def fetch(url,**kw):
        calls.append(dict(kw['query']))
        return payloads[len(calls)-1],{}
    d=Definition(pagination=paging,records_path='/items' if paging['mode']=='cursor' else '')
    rows,_,_=collect(d,'https://example.org',{},fetch=fetch)
    assert len(rows)==2
    assert [x.get('page') for x in calls]==expected


def test_repeated_cursor_and_page_cap_fail_without_cursor():
    d=Definition(records_path='/items',pagination={'mode':'cursor'})
    with pytest.raises(IntegrationError,match='repeated'):
        collect(d,'https://example.org',{},fetch=lambda *a,**k:({'items':[record()],'next':'same'},{}))
    d=Definition(pagination={'mode':'page','size':1},max_pages=1)
    with pytest.raises(IntegrationError,match='Page limit'):
        collect(d,'https://example.org',{},fetch=lambda *a,**k:([record()],{}))


def test_preview_first_page_ten_rows_and_no_pagination():
    fetch=Mock(return_value=([record(x) for x in range(20)],{}))
    rows,_,pages=collect(Definition(pagination={'mode':'page'}),'https://example.org',{},preview=True,fetch=fetch)
    assert len(rows)==10 and pages==1 and fetch.call_count==1


@pytest.mark.parametrize('mode',['next_url','link'])
def test_next_url_cannot_exfiltrate_credentials(mode):
    payload={'items':[record()],'next':'https://evil.example/steal'}
    d=Definition(records_path='/items',pagination={'mode':mode})
    with pytest.raises(IntegrationError,match='origin'):
        collect(d,'https://example.org',{'secret':'private'},fetch=lambda *a,**k:(payload,{'Link':'<https://evil.example/steal>; rel="next"'}))


def test_related_details_are_encoded_and_mapped():
    calls=[]
    def fetch(url,**kw):
        calls.append(url)
        return ([{**record(),'id':'a/b'}],{}) if len(calls)==1 else ({'status':'PASS'}, {})
    d=Definition(enrichments=[{'name':'job','path':'/jobs/{/id}'}])
    d.fields['outcome']=api.Definition(fields={**d.model_dump()['fields'],'outcome':{'path':'/_related/job/status','transform':'lower'}}).fields['outcome']
    rows,_,_=collect(d,'https://example.org',{},fetch=fetch)
    assert calls[-1]=='https://example.org/jobs/a%2Fb'
    assert rows[0]['fields']['outcome']=='pass'


@pytest.mark.parametrize('url,address',[
    ('http://example.org','8.8.8.8'),('https://example.org','127.0.0.1'),
    ('https://example.org','169.254.169.254'),('https://example.org','::ffff:127.0.0.1'),
    ('https://example.org','10.0.0.1'),('https://user:pass@example.org','8.8.8.8'),
    ('https://evil.org','8.8.8.8'),
])
def test_destination_boundary(monkeypatch,url,address):
    monkeypatch.setenv('KEEN_INTEGRATION_ALLOWED_HOSTS','example.org')
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',(address,443))])
    with pytest.raises(ValueError):transport.destination(url)


def test_private_approved_and_dns_pinned(monkeypatch):
    monkeypatch.setenv('KEEN_INTEGRATION_ALLOWED_HOSTS','example.org')
    monkeypatch.setenv('KEEN_INTEGRATION_PRIVATE_CIDRS','10.2.0.0/16')
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('10.2.3.4',443))])
    assert transport.destination('https://example.org')[1]==['10.2.3.4']
    c=transport.PinnedHTTPS('example.org',443,'10.2.3.4',20)
    sock=object(); connect=Mock(return_value=sock); wrap=Mock(return_value='tls')
    monkeypatch.setattr(socket,'create_connection',connect);c._context=SimpleNamespace(wrap_socket=wrap)
    c.connect();connect.assert_called_once_with(('10.2.3.4',443),20)
    wrap.assert_called_once_with(sock,server_hostname='example.org')


def test_oauth_token_not_persisted(monkeypatch):
    monkeypatch.setenv('KEEN_INTEGRATION_SECRET_KEY',Fernet.generate_key().decode())
    c=SimpleNamespace(encrypted_secret=transport.encrypt({'secret':'client-secret'}),auth_kind='oauth_client_credentials',auth_name='',username='client',base_url='https://example.org',auth_options={'token_path':'/token','scope':'read'})
    fetch=Mock(return_value=({'access_token':'short-lived','token_type':'Bearer'},{}));monkeypatch.setattr(transport,'request',fetch)
    auth,credential=transport.connection_auth(c)
    assert auth=={'kind':'bearer','secret':'short-lived'} and credential=='client-secret'
    assert fetch.call_args.kwargs['form']=={'grant_type':'client_credentials','scope':'read'}
    assert 'short-lived' not in c.encrypted_secret


def test_all_templates_validate_and_jenkins_milliseconds():
    for t in templates():Definition.model_validate(t['definition'])
    d=Definition.model_validate(next(t['definition'] for t in templates() if t['id']=='jenkins'))
    f=normalize({'id':'1','timestamp':1767268800000,'displayName':'Build','result':'SUCCESS'},d)
    assert f['timestamp'].startswith('2026-01-01') and f['outcome']=='success'


@compiles(JSONB,'sqlite')
def sqlite_json(*a,**k):return 'JSON'

@pytest.fixture
def database(monkeypatch):
    engine=create_engine('sqlite://')
    @event.listens_for(engine,'connect')
    def functions(c,r):c.create_function('NOW',0,lambda:datetime.utcnow().isoformat(' '))
    create_sqlite_schema(engine)
    factory=sessionmaker(bind=engine)
    monkeypatch.setattr(runtime,'SessionLocal',factory)
    db=factory()
    c=Connection(id='connection',name='Checks',base_url='https://example.org',auth_kind='none')
    collector=Collector(id='collector',name='Checks',connection_id='connection',draft=Definition().model_dump())
    db.add_all([c,collector]);db.commit()
    yield db,factory
    db.close();engine.dispose()


def test_draft_publish_rollback_and_conflict(database,monkeypatch):
    db,_=database
    result=api.action_collector('collector','publish',api.Action(version=1),db)
    assert result['live_revision']==1 and result['enabled']
    api.save_collector('collector',api.CollectorInput(name='Changed',connection_id='connection',definition=Definition(path='/v2'),version=1),db)
    assert db.get(Revision,('collector',1)).definition['path']=='/'
    with pytest.raises(HTTPException) as e:api.save_collector('collector',api.CollectorInput(name='Stale',connection_id='connection',definition=Definition(),version=1),db)
    assert e.value.status_code==409
    db.rollback()
    api.action_collector('collector','rollback',api.Action(version=2,revision=1),db)
    assert db.get(Collector,'collector').draft['path']=='/'


def test_preview_no_evidence_or_cursor_writes(database,monkeypatch):
    db,factory=database
    monkeypatch.setattr(runtime,'collect',lambda *a:([{'fields':normalize(record(),Definition()),'record':record()}],'2026-01-01T12:00:00+00:00',1))
    monkeypatch.setattr(runtime,'load_rules',lambda *a,**k:[])
    monkeypatch.setattr(runtime,'evaluate_by_framework',lambda *a,**k:{'ISO':['A.1']})
    r=runtime.queue_run(db,db.get(Collector,'collector'),True);db.commit();runtime.run_job(r.id);db.expire_all()
    assert db.query(Event).count()==0 and db.get(Collector,'collector').cursor is None
    assert db.get(Run,r.id).status=='succeeded'
    assert db.get(Run,r.id).result['samples'][0]['matches']=={'ISO':['A.1']}


def test_failure_budget_and_exclusive_runs(database,monkeypatch):
    db,_=database
    api.action_collector('collector','publish',api.Action(version=1),db)
    def broken(*a):raise IntegrationError('HTTP 401: check credentials')
    monkeypatch.setattr(runtime,'collect',broken)
    for n in range(3):
        db.expire_all();r=runtime.queue_run(db,db.get(Collector,'collector'));db.commit()
        with pytest.raises(ValueError,match='already'):runtime.queue_run(db,db.get(Collector,'collector'))
        runtime.run_job(r.id)
    db.expire_all();c=db.get(Collector,'collector');assert c.failures==3 and not c.enabled and c.cursor is None
    with pytest.raises(HTTPException):api.action_collector('collector','run',api.Action(version=1),db)
    api.action_collector('collector','resume',api.Action(version=1),db)
    assert c.enabled and c.failures==0


def test_measurement_source_idempotency(database,monkeypatch):
    db,_=database
    m=Measure(framework_slug='test',summary='Uptime',target_unit='%');db.add(m);db.commit()
    d=Definition(output='measurement',measure_id=str(m.id),unit='%',fields={**Definition().model_dump()['fields'],
        'value':{'value':99.95,'transform':'number'},'period_start':{'value':'2026-01-01'},'period_end':{'value':'2026-01-31'}})
    c=db.get(Collector,'collector');c.draft=d.model_dump();db.commit()
    api.action_collector('collector','publish',api.Action(version=1),db)
    rows=[{'fields':normalize(record(),d),'record':record()}]
    monkeypatch.setattr(runtime,'collect',lambda *a:(rows,'2026-01-01T12:00:00+00:00',1))
    import app.ingest.common as common
    monkeypatch.setattr(common,'put_bytes',lambda **k:SimpleNamespace(uri='test://artifact',sha256='0'*64,size_bytes=50))
    monkeypatch.setattr(common,'apply_rules',lambda *a:0)
    for _ in range(2):
        db.expire_all();r=runtime.queue_run(db,db.get(Collector,'collector'));db.commit();runtime.run_job(r.id)
    db.expire_all()
    assert db.query(Event).count()==1 and db.query(Metric).count()==1
    assert db.query(Metric).one().source_event_id==db.query(Event).one().id
    assert db.query(Metric).one().metric_value==99.95
    assert db.get(Run,r.id).duplicates==1 and db.get(Collector,'collector').cursor


def test_connection_secrets_never_returned(database,monkeypatch):
    db,_=database;monkeypatch.setenv('KEEN_INTEGRATION_SECRET_KEY',Fernet.generate_key().decode())
    v=api.create_connection(api.ConnectionInput(name='Secret',base_url='https://example.org',auth_kind='bearer',secret='very-private'),db)
    assert v['has_secret'] and 'very-private' not in json.dumps(v)
    assert 'very-private' not in db.get(Connection,v['id']).encrypted_secret
    assert transport.decrypt(db.get(Connection,v['id']).encrypted_secret)=={'secret':'very-private'}


def test_failure_transition_consumes_budget_once(database):
    db,_=database
    api.action_collector('collector','publish',api.Action(version=1),db)
    r=runtime.queue_run(db,db.get(Collector,'collector'));db.commit()
    runtime.fail(db,r,'Failure');runtime.fail(db,r,'Duplicate failure')
    db.expire_all();assert db.get(Collector,'collector').failures==1


def test_recently_started_job_is_not_stale(database,monkeypatch):
    db,_=database
    api.action_collector('collector','publish',api.Action(version=1),db)
    r=runtime.queue_run(db,db.get(Collector,'collector'));r.created_at=datetime.utcnow()-timedelta(minutes=20)
    r.started_at=datetime.utcnow();r.status='running';db.commit()
    monkeypatch.setattr(runtime,'dispatch',lambda *a:None)
    runtime.tick();db.expire_all()
    assert db.get(Run,r.id).status=='running'
    r.started_at=datetime.utcnow()-timedelta(minutes=11);db.commit()
    runtime.tick();db.expire_all()
    assert db.get(Run,r.id).status=='failed' and db.get(Collector,'collector').failures==1


def test_scrub_handles_json_escape_characters():
    assert runtime.scrub({'text':'unicode \u2026 abc','nested':['u']},['u'])=={'text':'[REDACTED]nicode \u2026 abc','nested':['[REDACTED]']}


def test_sample_preview_never_calls_network_or_stores(database,monkeypatch):
    db,_=database
    monkeypatch.setattr(transport,'request',lambda *a,**k:pytest.fail('network request'))
    result=api.preview_sample(api.SampleInput(definition=Definition(),response=[record()],collector_id='collector'),db)
    assert result['status']=='succeeded' and len(result['result']['samples'])==1
    assert db.query(Event).count()==0 and db.query(Run).count()==0


def test_measurement_validation_rejects_nonfinite_and_reversed_periods():
    fields={**Definition().model_dump()['fields'],'value':{'value':'nan'},'period_start':{'value':'2026-02-01'},'period_end':{'value':'2026-01-01'}}
    d=Definition(output='measurement',measure_id=str(uuid.uuid4()),unit='%',fields=fields)
    with pytest.raises(ValueError):normalize(record(),d)
    d.fields['value'].value=1
    with pytest.raises(ValueError,match='period'):normalize(record(),d)


def test_http_redirects_are_not_followed(monkeypatch):
    monkeypatch.setattr(transport,'destination',lambda u:(__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(u),['8.8.8.8']))
    response=SimpleNamespace(status=302,getheader=lambda *a:'identity')
    conn=Mock();conn.getresponse.return_value=response
    monkeypatch.setattr(transport,'PinnedHTTPS',lambda *a:conn)
    with pytest.raises(IntegrationError,match='HTTP 302'):transport.request('https://example.org',auth={'kind':'bearer','secret':'sensitive'})
    assert conn.request.call_count==1 and conn.close.call_count==1


def test_response_size_limit(monkeypatch):
    monkeypatch.setattr(transport,'destination',lambda u:(__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(u),['8.8.8.8']))
    response=Mock(status=200);response.getheader.return_value='identity';response.read.return_value=b'x'*(transport.MAX_BYTES+1)
    conn=Mock();conn.getresponse.return_value=response;monkeypatch.setattr(transport,'PinnedHTTPS',lambda *a:conn)
    with pytest.raises(IntegrationError,match='4 MiB'):transport.request('https://example.org')
    conn.close.assert_called_once()


def test_record_cap_does_not_silently_truncate():
    with pytest.raises(IntegrationError,match='Record limit'):
        collect(Definition(max_records=1),'https://example.org',{},fetch=lambda *a,**k:([record(1),record(2)],{}))


def test_failed_partial_run_preserves_cursor_and_retry_deduplicates(database,monkeypatch):
    db,_=database
    api.action_collector('collector','publish',api.Action(version=1),db)
    d=Definition();rows=[{'fields':normalize(record(n),d),'record':record(n)} for n in (1,2)]
    monkeypatch.setattr(runtime,'collect',lambda *a:(rows,'2026-01-01T12:00:00+00:00',1))
    import app.ingest.common as common
    monkeypatch.setattr(common,'put_bytes',lambda **k:SimpleNamespace(uri='test://artifact',sha256='0'*64,size_bytes=50))
    monkeypatch.setattr(common,'apply_rules',lambda *a:0)
    original=runtime.store_event_with_artifact;calls=0
    def fail_second(*a,**kw):
        nonlocal calls
        calls+=1
        if calls==2:raise RuntimeError('interrupted')
        return original(*a,**kw)
    monkeypatch.setattr(runtime,'store_event_with_artifact',fail_second)
    r=runtime.queue_run(db,db.get(Collector,'collector'));db.commit();runtime.run_job(r.id);db.expire_all()
    assert db.query(Event).count()==1 and db.get(Collector,'collector').cursor is None
    assert db.get(Run,r.id).status=='failed'
    monkeypatch.setattr(runtime,'store_event_with_artifact',original)
    r=runtime.queue_run(db,db.get(Collector,'collector'));db.commit();runtime.run_job(r.id);db.expire_all()
    assert db.query(Event).count()==2 and db.get(Collector,'collector').cursor
    assert db.get(Run,r.id).duplicates==1 and db.get(Run,r.id).new_records==1



def test_pasted_sample_does_not_execute_framework_regex_rules(database,monkeypatch):
    db,_=database
    import app.mapping.rules as rules
    def forbidden(*a,**k):
        pytest.fail('Synchronous sample preview must not evaluate potentially unbounded regex')
    monkeypatch.setattr(rules,'load_rules',forbidden)
    monkeypatch.setattr(rules,'evaluate_by_framework',forbidden)
    result=api.preview_sample(api.SampleInput(definition=Definition(),response=[record()]),db)
    assert result['status']=='succeeded'
    assert result['result']['samples'][0]['matches'] is None
    assert 'background' in result['result']['samples'][0]['mapping_note']


@pytest.mark.parametrize('method', ['POST','PUT','DELETE'])
def test_demo_blocks_integration_mutations(monkeypatch, method):
    monkeypatch.setattr(api.settings, 'demo_mode', True)
    with pytest.raises(HTTPException) as exc:
        api.integration_demo_guard(SimpleNamespace(method=method))
    assert exc.value.status_code == 403


def test_demo_allows_integration_inspection(monkeypatch):
    monkeypatch.setattr(api.settings, 'demo_mode', True)
    api.integration_demo_guard(SimpleNamespace(method='GET'))
