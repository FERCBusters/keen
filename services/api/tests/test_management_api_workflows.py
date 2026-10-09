"""Management workflows through HTTP, real authorization, and persisted SQL state."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import isms, risks, pestle, interested_parties, audits, questions
from app.core.config import settings
from app.db.models import User, Framework, ControlItem, FrameworkClause
from app.db.session import get_db


@pytest.fixture
def api(contract_db, monkeypatch):
    db = contract_db
    monkeypatch.setattr(settings, 'aggregate_cache_ttl_seconds', 0)
    admin = User(username='manager', password_hash='unused', role='admin', is_active=True)
    reader = User(username='reader', password_hash='unused', role='normal', is_active=True)
    controls = [ControlItem(framework_slug=fw, ref='1', title=f'{fw} control', type='control') for fw in ['A', 'B']]
    clause = FrameworkClause(framework_slug='A', ref='4.1', title='Context')
    db.add_all([admin, reader, Framework(slug='A'), Framework(slug='B'), clause, *controls])
    db.commit()
    identity = {'user': admin}
    app = FastAPI()
    for module in [isms, risks, pestle, interested_parties, audits, questions]:
        app.include_router(module.router, prefix='/api')
    @app.middleware('http')
    async def authenticate(request, call_next):
        request.state.user = identity['user']
        return await call_next(request)
    def database():
        try:
            yield db
        except Exception:
            db.rollback()
            raise
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, db=db, identity=identity, admin=admin,
                              reader=reader, controls=controls, clause=clause)


def request(api, method, path, body=None, status=200, **params):
    response = api.client.request(method, '/api/v1/' + path, params={'framework':'A', **params},
                                  **({'json': body} if body is not None else {}))
    assert response.status_code == status, response.text
    return response.json() if response.content else None


ENTITIES = [
    ('isms/objectives', {'requirement':'Protect records', 'goal':'All records protected'}, 'goal', 'Better protection'),
    ('isms/documents', {'title':'Security policy'}, 'title', 'Updated policy'),
    ('isms/org-nodes', {'name':'Security team'}, 'name', 'Security role'),
    ('isms/licenses', {'name':'License A'}, 'name', 'License B'),
    ('isms/aws-accounts', {'name':'Production', 'account_id':'123456789012'}, 'name', 'Staging'),
    ('isms/meetings', {'title':'Review', 'date':'2026-01-01'}, 'title', 'Annual review'),
    ('isms/effectiveness-measures', {'summary':'Coverage', 'metric':'Percentage', 'effectiveness_measure':'Measure all agents'}, 'summary', 'Agent coverage'),
    ('interested-parties', {'framework':'A','name':'Customers','nature':'External'}, 'note', 'Reviewed'),
    ('pestle/items', {'framework':'A','type':'Legal','lens':'External','item':'New regulation'}, 'item', 'Updated regulation'),
    ('audits', {'framework_slug':'A','title':'Annual audit'}, 'title', 'Updated audit'),
]


@pytest.mark.parametrize('path,payload,field,value', ENTITIES)
def test_create_update_list_delete(api, path, payload, field, value):
    created = request(api, 'POST', path, payload)
    identifier = created['id']
    listed = request(api, 'GET', path)
    assert identifier in {row['id'] for row in listed['items']}
    patch = {field:value}
    if path == 'pestle/items':
        patch = {**payload, **patch}
    changed = request(api, 'PATCH', f'{path}/{identifier}', patch)
    assert changed[field] == value
    request(api, 'DELETE', f'{path}/{identifier}')
    assert identifier not in {row['id'] for row in request(api, 'GET', path)['items']}


@pytest.mark.parametrize('path,payload,field,value', ENTITIES)
def test_access_is_denied_without_permission(api, path, payload, field, value):
    api.identity['user'] = api.reader
    request(api, 'GET', path, status=403)
    request(api, 'POST', path, payload, status=403)
    request(api, 'PATCH', f'{path}/{uuid.uuid4()}', {field:value}, status=403)
    request(api, 'DELETE', f'{path}/{uuid.uuid4()}', status=403)
    api.identity['user'] = None
    request(api, 'GET', path, status=401)


@pytest.mark.parametrize('path,permission', [
    ('isms/objectives','isms.read'), ('isms/documents','isms.read'),
    ('risks','risk.read'), ('pestle/items','pestle.read'),
    ('interested-parties','interested_parties.read'), ('audits','audits.read'),
])
def test_read_permission_does_not_grant_write(api, path, permission):
    api.reader.effective_permission_codes = {permission}
    api.identity['user'] = api.reader
    request(api, 'GET', path)
    request(api, 'DELETE', f'{path}/{uuid.uuid4()}', status=403)


@pytest.mark.parametrize('path,payload', [
    ('isms/objectives', {'requirement':'', 'goal':'x'}),
    ('isms/objectives', {'requirement':'x','goal':'y','status':'unknown'}),
    ('isms/objectives', {'requirement':'x','goal':'y','owner_user_id':str(uuid.uuid4())}),
    ('isms/documents', {'title':'x','document_type':'unknown'}),
    ('isms/org-nodes', {'name':'x','parent_id':str(uuid.uuid4())}),
    ('isms/meetings', {'title':'Missing date'}),
    ('pestle/items', {'type':'Invalid','lens':'External','item':'x'}),
    ('pestle/items', {'type':'Legal','lens':'Invalid','item':'x'}),
    ('audits', {'title':'x','start_date':'2026-02-01','end_date':'2026-01-01'}),
    ('audits', {'title':'x','audit_type':'unknown'}),
])
def test_invalid_create_is_rejected_without_adding_row(api, path, payload):
    before = request(api, 'GET', path)['items']
    request(api, 'POST', path, payload, status=400)
    assert request(api, 'GET', path)['items'] == before


def test_document_revision_scope_and_optimistic_lock(api):
    first = request(api, 'POST', 'isms/documents', {'title':'One','content_html':'<p>Version one</p>'})
    second = request(api, 'POST', 'isms/documents', {'title':'Two','content_html':'<p>Unrelated</p>'})
    path = 'isms/documents/' + first['id']
    updated = request(api, 'PATCH', path, {'content_html':'<p>Version two</p>','expected_content_version':1})
    assert updated['content_version'] == 2
    request(api, 'PATCH', path, {'content_html':'<p>Stale</p>','expected_content_version':1}, status=409)
    revisions = request(api, 'GET', path + '/revisions')
    assert len(revisions) == 2
    assert len(request(api, 'GET', 'isms/documents/' + second['id'] + '/revisions')) == 1
    assert 'Version one' in request(api, 'GET', path + '/revisions/1')['content_html']
    assert 'Version two' in request(api, 'GET', path)['content_html']


def test_interested_party_communications_controls_and_changelog(api):
    body = {'framework':'A','name':'Customers','nature':'External','controls':['1'],
            'communications':[{'event':'Incident','when':'Within 24 hours','with_whom':'Individual member','methods':['Email']}]}
    party = request(api, 'POST', 'interested-parties', body)
    path = 'interested-parties/' + party['id']
    assert len(party['controls']) == 1
    assert party['communications'][0]['methods'] == ['Email']
    request(api, 'POST', 'interested-parties', body, status=400)
    request(api, 'DELETE', 'interested-parties/names/' + party['name']['id'], status=400)
    request(api, 'DELETE', 'interested-parties/natures/' + party['nature']['id'], status=400)
    request(api, 'PATCH', path, {'framework':'B'}, status=400)
    request(api, 'PATCH', path + '/controls', {'controls':[str(api.controls[1].id)]}, status=400)
    request(api, 'PATCH', path + '/communications', {'items':[]})
    assert request(api, 'GET', path + '/communications')['items'] == []
    request(api, 'PATCH', path + '/controls', {'controls':[]})
    assert request(api, 'GET', path + '/controls')['controls'] == []
    assert len(request(api, 'GET', path + '/changelog')['items']) >= 3


@pytest.mark.parametrize('lookup', ['names','natures'])
def test_party_lookup_normalization_duplicate_and_deletion(api, lookup):
    path = 'interested-parties/' + lookup
    row = request(api, 'POST', path, {'name':'  Supplier  '})
    assert row['name'] == 'Supplier'
    assert request(api, 'POST', path, {'name':'supplier'})['id'] == row['id']
    other = request(api, 'POST', path, {'name':'Other'})
    request(api, 'PATCH', path + '/' + other['id'], {'name':'SUPPLIER'}, status=400)
    assert request(api, 'GET', path, q='supp')['items'][0]['id'] == row['id']
    request(api, 'DELETE', path + '/' + row['id'])


def test_risk_scores_controls_owner_and_change_history(api):
    risk = request(api, 'POST', 'risks', {'framework':'A','asset':'Database','category_name':'Information',
        'subcategory_name':'Records','risk_types':['Confidentiality'], 'threat_summary':'Disclosure',
        'risk_owner_user_id':str(api.admin.id),'register_likelihood':4,'register_impact':5,
        'register_residual_likelihood':2,'register_residual_impact':3,'controls':['1']})
    assert risk['risk_score'] == 20
    path = 'risks/' + risk['id']
    updated = request(api, 'PATCH', path, {'register_likelihood':1})
    assert updated['risk_score'] == 5
    assert len(request(api, 'GET', path + '/controls')['items']) == 1
    request(api, 'PATCH', path + '/controls', {'controls':[], 'framework':'A'})
    assert request(api, 'GET', path + '/controls')['items'] == []
    assert len(request(api, 'GET', path + '/changelog')['items']) >= 2
    assert request(api, 'GET', 'risks', q='Disclosure')['total'] == 1
    request(api, 'DELETE', path)
    request(api, 'GET', path, status=404)


def test_asset_license_and_access_matrix_relationship_lifecycle(api):
    role = request(api, 'POST', 'isms/org-nodes', {'name':'Operator','node_type':'role','user_ids':[str(api.admin.id)]})
    license_row = request(api, 'POST', 'isms/licenses', {'name':'Commercial'})
    asset = request(api, 'POST', 'isms/assets', {'asset':'Service','category_name':'Software',
        'subcategory_name':'Applications','license_id':license_row['id'],'owner_org_node_id':role['id']})
    account = request(api, 'POST', 'isms/aws-accounts', {'name':'Hosting','account_id':'account-1'})
    entry = request(api, 'POST', 'isms/access-control-matrix', {'task_action':'Deploy',
        'service_asset_id':asset['id'],'aws_account_ids':[account['id']], 'role_org_node_ids':[role['id']]})
    path = 'isms/access-control-matrix/' + entry['id']
    assert request(api, 'GET', path)['task_action'] == 'Deploy'
    assert request(api, 'PATCH', path, {'notes':'Reviewed'})['notes'] == 'Reviewed'
    request(api, 'DELETE', path)
    request(api, 'DELETE', 'isms/aws-accounts/' + account['id'])
    request(api, 'PATCH', 'isms/assets/' + asset['id'], {'description':'Production database'})
    assert request(api, 'GET', 'isms/assets/' + asset['id'])['description'] == 'Production database'
    request(api, 'DELETE', 'isms/assets/' + asset['id'])
    request(api, 'DELETE', 'isms/licenses/' + license_row['id'])


def test_effectiveness_measure_metric_entry_scoping_and_updates(api):
    measure = request(api, 'POST', 'isms/effectiveness-measures', {'summary':'Agent coverage',
        'effectiveness_measure':'Count active agents','metric':'Coverage','target_value':95,'target_unit':'%','threshold_operator':'gte'})
    path = 'isms/effectiveness-measures/' + measure['id']
    entry = request(api, 'POST', path + '/metrics', {'metric_value':96, 'source_type':'other',
        'period_start':'2026-01-01','period_end':'2026-01-31'})
    assert entry['metric_unit'] == '%'
    assert request(api, 'GET', path + '/metrics')['items'][0]['metric_value'] == 96
    request(api, 'GET', path, framework='B', status=404)
    request(api, 'POST', path + '/metrics', {'metric_value':100}, framework='B', status=404)
    metric_path = 'isms/effectiveness-metrics/' + entry['id']
    assert request(api, 'PATCH', metric_path, {'metric_value':97})['metric_value'] == 97
    request(api, 'DELETE', metric_path)
    assert request(api, 'GET', path + '/metrics')['items'] == []


def test_audit_child_resources_are_scoped_and_completed_audits_lock(api):
    audit = request(api, 'POST', 'audits', {'title':'Annual','framework_slug':'A','controls':['1'],'clauses':['4.1']})
    other = request(api, 'POST', 'audits', {'title':'Other','framework_slug':'A'})
    path = 'audits/' + audit['id']
    attendee = request(api, 'POST', path + '/attendees', {'user_id':str(api.reader.id),'role':'Observer'})
    finding = request(api, 'POST', path + '/findings', {'kind':'minor_nc','title':'Missing review','control':'1'})
    evidence = request(api, 'POST', path + '/evidence', {'evidence_url':'https://example.org/evidence','title':'Review record'})
    for collection,row in [('attendees',attendee),('findings',finding),('evidence',evidence)]:
        request(api, 'DELETE', f'audits/{other["id"]}/{collection}/{row["id"]}', status=404)
    request(api, 'PATCH', path + '/findings/' + finding['id'], {'status':'closed','description':'Resolved'})
    request(api, 'PATCH', path + '/evidence/' + evidence['id'], {'notes':'Reviewed'})
    detail = request(api, 'GET', path)
    assert len(detail['attendees']) == len(detail['findings']) == len(detail['evidence']) == 1
    assert detail['findings'][0]['status'] == 'closed'
    request(api, 'PATCH', path, {'status':'completed'})
    request(api, 'POST', path + '/findings', {'kind':'ofi','title':'New'}, status=409)
    request(api, 'PATCH', path, {'status':'open'})
    for collection,row in [('attendees',attendee),('findings',finding),('evidence',evidence)]:
        request(api, 'DELETE', f'{path}/{collection}/{row["id"]}')
    detail = request(api, 'GET', path)
    assert not detail['attendees'] and not detail['findings'] and not detail['evidence']


@pytest.mark.parametrize('kind', ['major_nc','minor_nc','ofi','best_practice'])
def test_audit_finding_kinds_roundtrip(api, kind):
    audit = request(api, 'POST', 'audits', {'title':'Review','framework_slug':'A'})
    path = f'audits/{audit["id"]}/findings'
    finding = request(api, 'POST', path, {'kind':kind,'title':'Observation','description':'<p>Safe<script>evil()</script></p>'})
    assert finding['kind'] == kind
    assert '<script>' not in finding['description']
    request(api, 'PATCH', path + '/' + finding['id'], {'status':'invalid'}, status=400)


@pytest.mark.parametrize('recurrence', ['once','monthly','quarterly','yearly'])
def test_scheduled_audit_template_roundtrip(api, recurrence):
    body = {'title':'Scheduled review','framework_slug':'A','start_date':'2027-01-15',
            'schedule_recurrence':recurrence,'schedule_interval':1,
            'attendees':[{'user_id':str(api.reader.id),'role':'Auditor'}]}
    row = request(api, 'POST', 'audits/scheduled', body)
    path = 'audits/scheduled/' + row['id']
    assert row['schedule_recurrence'] == recurrence
    assert row['id'] in {r['id'] for r in request(api, 'GET', 'audits/scheduled')['items']}
    assert request(api, 'PATCH', path, {'title':'Rescheduled'})['title'] == 'Rescheduled'
    request(api, 'DELETE', path)


@pytest.mark.parametrize('path', ['isms/objectives','isms/documents','isms/assets','isms/effectiveness-measures','isms/meetings','risks','pestle/items','interested-parties','audits'])
@pytest.mark.parametrize('identifier,status', [('invalid',400),(str(uuid.uuid4()),404)])
def test_detail_identifiers_do_not_produce_server_errors(api,path,identifier,status):
    request(api,'GET',f'{path}/{identifier}',status=status)
