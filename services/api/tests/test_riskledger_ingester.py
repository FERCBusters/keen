"""API contract, import identity and trust-boundary regression tests."""
import copy, hashlib, json
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from tests.db_helpers import create_sqlite_schema
from app.core.config import settings
from app.db.models import Event, FrameworkClause, ControlItem, IsmsVendor, Risk, RiskControlLink, IngestionCursor, SourceIngestionState
from app.ingest import riskledger as rl, common
from app.mapping.collector import collector_id, event_collector, collector_pointer_filter
from app.api.routes import managed_configurations as managed
ORG,SUP,RID,CTL,DOM,LEVEL=[f'00000000-0000-4000-8000-00000000000{i}' for i in range(1,7)]
@pytest.fixture
def db(monkeypatch):
    engine=create_engine('sqlite://');create_sqlite_schema(engine)
    monkeypatch.setattr(settings,'demo_mode',False);monkeypatch.setattr(settings,'event_data_masking','false')
    monkeypatch.setattr(common,'put_bytes',lambda **kw:SimpleNamespace(uri='local://'+kw['key'],sha256=hashlib.sha256(kw['data']).hexdigest(),size_bytes=len(kw['data'])))
    monkeypatch.setattr(common,'apply_rules',lambda db,event:0)
    with Session(engine) as session:yield session
    engine.dispose()
@pytest.fixture
def records():
    return {'organisation':{'id':ORG,'name':'Customer'},
        'supplier':{'id':SUP,'name':'Supplier','website':'https://example.org','numEvidence':3,
            'framework':{'level':{'id':LEVEL,'name':'Core'},'addOnDomains':[]},
            'complianceByDomain':[{'domainID':DOM,'percentageCompliance':40}]},
        'risk':{'id':RID,'name':'Patching risk','description':'Unpatched services','supplierID':SUP,
            'associatedType':'control','associatedID':CTL,'likelihood':'low','impact':'high',
            'riskScore':2,'riskOwner':'Upstream owner','status':1},
        'domain':{'id':DOM,'letter':'D','name':'Governance','description':'Governance controls'},
        'control':{'id':CTL,'domainID':DOM,'number':3,'question':'Are patches applied?',
            'description':'Describe patch management','deprecated':False}}
class FakeAPI:
    def __init__(self,records):self.records=records
    def request(self,path):assert path=='/organisations/me';return self.records['organisation']
    def one(self,kind,ident):return copy.deepcopy(self.records[kind])
    def pages(self,kind,cfg):yield copy.deepcopy(self.records['supplier' if kind=='suppliers' else 'risk'])

def test_import_repeat_preserve_edits_and_snapshot_changes(db,records):
    cfg={'organizations':[{'org':'*'}]};client=FakeAPI(records)
    assert rl._collect(db,client,cfg)['created_events']==4
    assert db.query(IsmsVendor).count()==1 and db.query(Risk).count()==1
    assert db.query(ControlItem).count()==2 and db.query(FrameworkClause).count()==1
    assert db.query(RiskControlLink).count()==1
    risk=db.query(Risk).one();vendor=db.query(IsmsVendor).one()
    assert risk.register_likelihood is None and risk.risk_owner_user_id is None
    assert risk.asset.vendor_id==vendor.id and risk.mitigator_context['riskledger']['riskScore']==2
    risk.threat_summary='Local risk';vendor.name='Local supplier'
    db.query(ControlItem).filter_by(type='control').one().title='Local control';db.commit()
    assert rl._collect(db,client,cfg)['created_events']==0 and db.query(Event).count()==4
    records['supplier']['numEvidence']=4;records['risk']['description']='Changed upstream'
    assert rl._collect(db,client,cfg)['created_events']==2
    assert db.query(Risk).one().threat_summary=='Local risk'
    assert db.query(IsmsVendor).one().name=='Local supplier'
    assert db.query(ControlItem).filter_by(type='control').one().title=='Local control'
    assert db.query(IngestionCursor).count()==1

def test_account_pin_and_framework_level_filter(db,records):
    with pytest.raises(rl.RiskLedgerError,match='does not match'):
        rl._collect(db,FakeAPI(records),{'organizations':[{'org':SUP}]})
    assert db.query(Event).count()==0
    result=rl._collect(db,FakeAPI(records),{'organizations':[{'org':'*'}],'framework_level_ids':[SUP]})
    assert result['examined']==0 and db.query(Risk).count()==0 and db.query(IsmsVendor).count()==0

def test_explicit_control_discovery_without_native_imports(db,records):
    result=rl._collect(db,FakeAPI(records),{'organizations':[{'org':'*'}],
        'import_suppliers':False,'import_risks':False,'control_ids':[CTL]})
    assert result['controls_discovered']==1 and db.query(Event).count()==2
    assert db.query(IsmsVendor).count()==0

def test_same_vendor_name_does_not_merge_local_entities(db,records):
    db.add(IsmsVendor(name='Supplier'));db.commit()
    rl._collect(db,FakeAPI(records),{'organizations':[{'org':'*'}]})
    assert db.query(IsmsVendor).count()==2 and '[RL ' in db.query(Risk).one().asset.vendor.name

def test_collector_identity_and_registration(db,records):
    managed._validate_connector('riskledger',{'organizations':[{'org':'*'}]})
    assert managed._editable_collector('riskledger','organizations')
    assert managed._collector_key('riskledger','organizations',{'org':'*'})=='*'
    rl._collect(db,FakeAPI(records),{'organizations':[{'org':'*'}]})
    event=db.query(Event).filter_by(action='risk.snapshot').one()
    assert event_collector({'source':event.source,'raw_pointer':event.raw_pointer})==collector_id('riskledger','organizations','*')
    assert collector_pointer_filter(collector_id('riskledger','organizations','*'))=={'riskledger':{'section':'organizations','org':'*'}}
    assert event.normalized_payload['fields']['supplier.id']==SUP and event.outcome=='info'

@pytest.mark.parametrize('cfg',[
 {'organizations':[{'org':'../../secrets'}]}, {'organizations':[{'org':'*'},{'org':ORG}]},
 {'control_ids':['https://other.example']}, {'page_size':True}, {'max_pages':0},
 {'import_suppliers':'false'}, {'framework_level_ids':['no']}, {'max_run_seconds':1}])
def test_invalid_config(cfg):
    with pytest.raises(rl.RiskLedgerError):rl.validate_config(cfg)

def test_post_supplier_pagination_and_get_risks():
    requests=[]
    def handler(req):
        requests.append(req);assert req.headers['Authorization']=='Bearer test-key'
        if req.url.path.endswith('/suppliers'):
            assert req.method=='POST';offset=json.loads(req.content)['page']['offset'];key='suppliers'
        else:
            assert req.method=='GET';offset=int(req.url.params['offset']);key='risks'
        return httpx.Response(200,json={key:[{'id':SUP if offset==0 else RID}], 'page':{'offset':offset,'limit':1,'total':2}})
    with httpx.Client(transport=httpx.MockTransport(handler),headers={'Authorization':'Bearer test-key'}) as http:
        client=rl.Client(http)
        assert len(list(client.pages('suppliers',{'page_size':1})))==2
        assert len(list(client.pages('risks',{'page_size':1})))==2
    assert len(requests)==4

@pytest.mark.parametrize('status',[301,401,403,429,500])
def test_no_redirects_inline_retries_or_response_secret_leaks(status):
    calls=[]
    def handler(req):calls.append(req);return httpx.Response(status,headers={'Location':'https://evil.test'},text='SECRET server output')
    with httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=False) as http:
        with pytest.raises(rl.RiskLedgerError) as error:rl.Client(http).request('/organisations/me')
    assert 'SECRET' not in str(error.value) and len(calls)==1

def test_repeated_page_and_page_limit_fail_visibly():
    def handler(req):return httpx.Response(200,json={'risks':[{'id':RID}], 'page':{'offset':int(req.url.params['offset']),'total':3}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(rl.RiskLedgerError,match='repeated'):list(rl.Client(http).pages('risks',{'page_size':1}))
        with pytest.raises(rl.RiskLedgerError,match='page limit'):list(rl.Client(http).pages('risks',{'page_size':1,'max_pages':1}))

def test_pause_demo_and_missing_secret(db,monkeypatch):
    monkeypatch.setattr(settings,'riskledger_enabled',True);monkeypatch.setattr(settings,'riskledger_api_key','')
    assert 'API_KEY' in rl.ingest_riskledger_all(db)[0]['error']
    monkeypatch.setattr(settings,'demo_mode',True)
    assert rl.ingest_riskledger_all(db)[0]['skipped']
    monkeypatch.setattr(settings,'demo_mode',False)
    db.add(SourceIngestionState(source='riskledger',paused=True));db.commit()
    assert rl.ingest_riskledger_all(db)[0]['paused']

def test_run_bound_and_record_identity_validation():
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'id':RID}))) as http:
        with pytest.raises(rl.RiskLedgerError,match='mismatched'):rl.Client(http).one('control',CTL)
        with pytest.raises(rl.RiskLedgerError,match='time limit'):rl.Client(http,max_run_seconds=-1).request('/risks')

def test_redaction_precedes_native_import_and_unsafe_website_removed(db,records):
    records['risk']['description']='password=verysecret';records['supplier']['website']='javascript:alert(1)'
    rl._collect(db,FakeAPI(records),{'organizations':[{'org':'*'}]})
    assert 'verysecret' not in db.query(Risk).one().threat_summary and db.query(IsmsVendor).one().website==''
