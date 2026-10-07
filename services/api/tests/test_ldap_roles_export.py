"""LDAP trust boundaries, role ownership, and round-trippable rule exports."""
import ssl
import uuid
from types import SimpleNamespace
import pytest
import yaml
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from tests.db_helpers import create_sqlite_schema
from app.core.config import settings
from app.db.models import (User, UserIdentity, IsmsOrgNode, IsmsOrgNodeUser, Risk,
                           RiskAsset, RiskCategory, RiskAssetSubcategory, ManagedConfiguration)
from app.security import ldap, mfa
from app.security.auth import authenticate_user
from app.api.routes import risks, managed_configurations as mappings
from app.mapping.rules import parse_rules

@pytest.fixture
def db():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    create_sqlite_schema(engine)
    with Session(engine) as session: yield session
    engine.dispose()

@pytest.fixture
def directory(monkeypatch):
    for key,value in dict(ldap_enabled=True,ldap_url='ldap://directory.example:389',
        ldap_base_dn='ou=People,dc=example',ldap_bind_dn='cn=reader,dc=example',ldap_bind_password='search-secret',
        ldap_username_attribute='uid',ldap_email_attribute='mail',ldap_id_attribute='entryUUID',ldap_auto_provision=True).items():
        monkeypatch.setattr(settings,key,value)
    events=[]; connections=[]
    state={'tls':True,'bind':True,'entries':[SimpleNamespace(entry_dn='uid=alice,ou=People,dc=example',
            entry_attributes_as_dict={'uid':['alice'],'mail':['alice@example.org'],'entryUUID':['immutable-id']})]}
    class FakeConnection:
        def __init__(self, server, **kw): self.kw=kw; self.closed=True; self.result={'result':0};connections.append(self)
        def open(self): self.closed=False;events.append('open')
        def start_tls(self):events.append('tls');return state['tls']
        def bind(self):
            events.append(('bind',self.kw['user']))
            if self.kw['user'].startswith('uid=') and not state['bind']:self.result={'result':49};return False
            return True
        def search(self,base,query,**kw):
            state['search']=(base,query,kw);self.entries=state['entries'];self.result={'result':state.get('result',0)};return True
        def unbind(self):events.append('unbind')
    monkeypatch.setattr(ldap,'Connection',FakeConnection)
    return state,events,connections


def test_starttls_filter_escaping_and_ou(directory):
    state,events,connections=directory
    result=ldap.directory_identity('alice*)(uid=*)','password')
    assert result['subject']=='immutable-id'
    assert events[:3]==['open','tls',('bind','cn=reader,dc=example')]
    assert events[3:6]==['open','tls',('bind','uid=alice,ou=People,dc=example')]
    base,query,kw=state['search']
    assert base==settings.ldap_base_dn and r'alice\2a\29\28uid=\2a\29' in query
    assert kw['size_limit']==2 and kw['dereference_aliases']==ldap.DEREF_NEVER
    assert all(c.kw['auto_referrals'] is False and c.kw['read_only'] for c in connections)


def test_ldaps_cert_validation(directory,monkeypatch):
    monkeypatch.setattr(settings,'ldap_url','ldaps://directory.example:636')
    real=ldap.Server;seen=[]
    monkeypatch.setattr(ldap,'Server',lambda *a,**kw:(seen.append(kw) or real(*a,**kw)))
    assert ldap.directory_identity('alice','secret')
    assert seen[0]['use_ssl'] and seen[0]['tls'].validate==ssl.CERT_REQUIRED
    assert 'tls' not in directory[1]


def test_failed_starttls_never_binds(directory):
    directory[0]['tls']=False
    with pytest.raises(HTTPException) as error:ldap.directory_identity('alice','secret')
    assert error.value.status_code==503
    assert not any(isinstance(e,tuple) for e in directory[1])

@pytest.mark.parametrize('case',['ambiguous','missing-id','invalid-password','partial'])
def test_directory_denials(directory,case):
    state=directory[0]
    if case=='ambiguous':state['entries']*=2
    if case=='missing-id':state['entries'][0].entry_attributes_as_dict.pop('entryUUID')
    if case=='invalid-password':state['bind']=False
    if case=='partial':state['result']=4
    assert ldap.directory_identity('alice','secret') is None


def test_empty_password_never_connects(directory):
    assert ldap.directory_identity('alice','') is None
    assert directory[1]==[]


def test_directory_cannot_take_over_local_user(db,directory):
    admin=User(username='alice',email='alice@example.org',password_hash='local-hash',role='admin')
    db.add(admin);db.commit()
    user=ldap.authenticate_ldap(db,'alice','password')
    assert user.id!=admin.id and user.role=='normal' and user.auth_backend=='ldap'
    assert user.password_hash=='!LDAP' and user.username=='ldap:alice'
    assert ldap.authenticate_ldap(db,'alice','password').id==user.id
    assert authenticate_user(db,user.username,'password') is None
    user.is_active=False;db.commit()
    assert ldap.authenticate_ldap(db,'alice','password') is None


def test_no_auto_provision(db,directory,monkeypatch):
    monkeypatch.setattr(settings,'ldap_auto_provision',False)
    assert ldap.authenticate_ldap(db,'alice','password') is None
    assert db.query(User).count()==0


def test_reauthentication_requires_same_directory_identity(db,directory):
    user=ldap.authenticate_ldap(db,'alice','password')
    assert ldap.verify_ldap_user(db,user,'password')
    directory[0]['entries'][0].entry_attributes_as_dict['entryUUID']=['different-id']
    assert not ldap.verify_ldap_user(db,user,'password')


def test_ldap_session_respects_mfa(db,directory,monkeypatch):
    user=ldap.authenticate_ldap(db,'alice','password');user.mfa_enabled=True;db.commit()
    monkeypatch.setattr(settings,'mfa_policy','all')
    assert not mfa.local_session_allowed(db,user,{'auth_method':'ldap','mfa_version':user.mfa_version})
    assert mfa.local_session_allowed(db,user,{'auth_method':'ldap','mfa_version':user.mfa_version,'mfa_verified':True})
    assert not mfa.local_session_allowed(db,user,{'auth_method':'ldap','mfa_version':user.mfa_version+1,'mfa_verified':True})

@pytest.fixture
def role_risk(db):
    user=User(username='owner',password_hash='test',role='normal',is_active=True)
    role=IsmsOrgNode(name='Security lead',node_type='role')
    category=RiskCategory(name='IT');db.add_all([user,role,category]);db.flush()
    sub=RiskAssetSubcategory(name='Servers',category_id=category.id);db.add(sub);db.flush()
    asset=RiskAsset(name='Server',category_id=category.id,subcategory_id=sub.id);db.add(asset);db.flush()
    risk=Risk(asset_id=asset.id);db.add(risk);db.commit()
    return user,role,risk


def test_role_owner_assignment_access_and_clearing(db,role_risk):
    user,role,risk=role_risk
    risks._apply_payload(db,risk,risks.RiskUpsertPayload(risk_owner_role_id=role.id),is_create=False)
    db.commit()
    assert risk.risk_owner_user_id is None and risk.risk_owner_role_id==role.id
    assert risks.risk_owner_out(risk)['type']=='role'
    assert not risks._is_risk_owner(user,risk)
    db.add(IsmsOrgNodeUser(org_node_id=role.id,user_id=user.id));db.commit()
    assert risks._is_risk_owner(user,risk)
    assert risks.list_my_owned_risks(user=user,db=db)['total']==1
    risks._apply_payload(db,risk,risks.RiskUpsertPayload(note='Keep owner'),is_create=False);db.commit()
    assert risk.risk_owner_role_id==role.id
    risks._apply_payload(db,risk,risks.RiskUpsertPayload(risk_owner_user_id=user.id),is_create=False);db.commit()
    assert risk.risk_owner_role_id is None and risk.risk_owner_user_id==user.id
    risks._apply_payload(db,risk,risks.RiskUpsertPayload(risk_owner_role_id=None),is_create=False);db.commit()
    assert not risk.owner and not risk.owner_role


def test_owner_validation(db,role_risk):
    user,role,risk=role_risk
    for fields in [dict(risk_owner_role_id=role.id,risk_owner_user_id=user.id),dict(risk_owner_role_id=uuid.uuid4())]:
        with pytest.raises(HTTPException):risks._apply_payload(db,risk,risks.RiskUpsertPayload(**fields),is_create=False)
    role.node_type='department';db.commit()
    with pytest.raises(HTTPException):risks._apply_payload(db,risk,risks.RiskUpsertPayload(risk_owner_role_id=role.id),is_create=False)


def test_yaml_export_roundtrip(db):
    rules=[{'id':'patching','description':'Package changes','when':{'source':'dpkg','action_regex':'^package\\.',
        'fields':[{'path':['fields','package'],'operator':'equals','value':'openssl'}]},
        'map_to':[{'framework':'ISO27001:2022','ref':'A.8.8'}],'confidence':0.9},
        {'id':'paused','when':{'source':'dpkg'},'enabled':False,'map_to':[{'framework':'ISO27001:2022','ref':'A.8.8'}]},
        {'id':'rss','when':{'source':'rss'},'map_to':[{'framework':'ISO27001:2022','ref':'A.5.19'}]}]
    db.add(ManagedConfiguration(name='rules',document={'rules':rules},version=1));db.commit()
    response=mappings.export_rules('dpkg',db)
    doc=yaml.safe_load(response.body)
    assert [r['id'] for r in doc['rules']]==['patching','paused']
    assert doc['rules'][1]['enabled'] is False
    assert parse_rules(doc)==parse_rules({'rules':rules[:2]})
    assert len(yaml.safe_load(mappings.export_rules('',db).body)['rules'])==3
    assert yaml.safe_load(mappings.export_rules('no-such-source',db).body)=={'rules':[]}
    assert response.headers['cache-control']=='no-store'


def test_ldap_login_route_and_mfa_with_local_disabled(db,directory,monkeypatch):
    import json
    import fakeredis
    from starlette.requests import Request
    from app.api.routes import auth
    from app.api.payloads import LoginPayload
    from app.db.models import MfaChallenge
    from app.security.sessions import get_session
    redis=fakeredis.FakeRedis()
    monkeypatch.setattr(auth,'get_valkey',lambda:redis)
    monkeypatch.setattr(settings,'local_auth_enabled',False)
    monkeypatch.setattr(settings,'mfa_policy','off')
    monkeypatch.setattr(settings,'mfa_origin','https://keen.example')
    request=Request({'type':'http','method':'POST','scheme':'https','server':('keen.example',443),
        'path':'/v1/auth/login','query_string':b'', 'headers':[(b'origin',b'https://keen.example')],'client':('192.0.2.1',1000)})
    response=auth.login(LoginPayload(username='alice',password='password',method='ldap'),request,db)
    assert response.status_code==200 and json.loads(response.body)['user']=='ldap:alice'
    from http.cookies import SimpleCookie
    cookie=SimpleCookie()
    for header in response.headers.getlist('set-cookie'):cookie.load(header)
    session=get_session(redis,cookie[settings.session_cookie_name].value)
    assert session['auth_method']=='ldap'
    user=db.query(User).filter_by(auth_backend='ldap').one();user.mfa_enabled=True;db.commit()
    response=auth.login(LoginPayload(username='alice',password='password',method='ldap'),request,db)
    assert json.loads(response.body)['mfa_required'] is True
    assert db.query(MfaChallenge).filter_by(user_id=user.id,stage='verify').count()==1
    with pytest.raises(HTTPException) as error:
        auth.login(LoginPayload(username='alice',password='password'),request,db)
    assert error.value.status_code==404


def test_organisation_framework_selection(db,monkeypatch):
    from app.api.routes import frameworks
    from app.db.models import Framework
    monkeypatch.setattr(settings,'enabled_frameworks','')
    user=User(username='admin',password_hash='test',role='admin');db.add(user)
    db.add_all([Framework(slug='ONE',name='One'),Framework(slug='TWO',name='Two')]);db.commit()
    before=frameworks.list_frameworks(db)
    assert {'ONE','TWO'} <= {item['slug'] for item in before['items']}
    selected=frameworks.save_framework_selection(frameworks.FrameworkSelectionInput(enabled=['TWO'],default='TWO',version=0),user,db)
    assert selected['version']==1
    assert frameworks.list_frameworks(db)['default']=='TWO'
    assert [item['slug'] for item in frameworks.list_frameworks(db)['items']]==['TWO']
    assert db.query(Framework).count()==2
    assert len(selected['items'])>=2
    with pytest.raises(HTTPException) as error:
        frameworks.save_framework_selection(frameworks.FrameworkSelectionInput(enabled=['ONE'],default='ONE',version=0),user,db)
    assert error.value.status_code==409
    monkeypatch.setattr(settings,'enabled_frameworks','ONE')
    # Environment change chooses a valid default without exposing excluded frameworks.
    assert frameworks.list_frameworks(db)['default']=='ONE'
    with pytest.raises(HTTPException):
        frameworks.save_framework_selection(frameworks.FrameworkSelectionInput(enabled=['TWO'],default='TWO',version=1),user,db)
    with pytest.raises(HTTPException):
        frameworks.save_framework_selection(frameworks.FrameworkSelectionInput(enabled=['ONE'],default='TWO',version=1),user,db)
    monkeypatch.setattr(settings,'enabled_frameworks','TYPO')
    assert frameworks.list_frameworks(db)['items']==[]
    assert frameworks.framework_selection(db)['unknown_environment_slugs']==['TYPO']
