"""Attack regressions and security contracts against the assembled application."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import fakeredis
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from app.security import sessions, csrf, regex, oidc
from app.security.route_policy import is_public_request, PUBLIC_HTTP_ROUTES
from app.core.config import settings


def request(path='/', method='GET', origin=None):
    return Request({'type':'http','method':method,'scheme':'https','server':('keen.example',443),
                    'path':path,'query_string':b'',
                    'headers':[] if origin is None else [(b'origin',origin.encode())]})


def test_session_refresh_cannot_resurrect_concurrent_logout(monkeypatch):
    redis=fakeredis.FakeRedis();sid=sessions.create_session(redis,user_id='one',ttl_seconds=300)
    original=redis.pipeline
    def pipeline(*args,**kwargs):
        pipe=original(*args,**kwargs);execute=pipe.execute
        def race(*args,**kwargs):
            redis.delete(sessions.session_key(sid));return execute(*args,**kwargs)
        pipe.execute=race;return pipe
    monkeypatch.setattr(redis,'pipeline',pipeline)
    sessions.update_session_authz(redis,sid,effective_role='normal',permission_codes=[],authz_version=2,ttl_seconds=300)
    assert sessions.get_session(redis,sid) is None


def test_regex_accepted_polynomial_pattern_has_runtime_deadline():
    pattern=regex.compile_pattern('(a+)(a+)$');started=time.monotonic()
    with pytest.raises(ValueError,match='time budget'):pattern.search('a'*20000+'!')
    assert time.monotonic()-started < 2


def test_mapping_runtime_uses_regex_boundary():
    from app.mapping.rules import _match_regex
    with pytest.raises(ValueError,match='Nested repetition'):_match_regex('(a+)+$', 'a'*100+'!')
    assert _match_regex(r'(?i)\bfailed\b', 'Operation FAILED')
    with pytest.raises(ValueError,match='65536'):_match_regex('x','x'*65537)


def test_regex_cache_is_bounded():
    regex.compile_pattern.cache_clear()
    for n in range(300):regex.compile_pattern('literal'+str(n))
    assert regex.compile_pattern.cache_info().currsize == 256


@pytest.mark.parametrize('pattern',['a{1,1000000}', '(a+)+$', '(a|aa)+$', '(a+)\\1', '(?>a+)+$'])
def test_unsafe_patterns_rejected_for_all_configuration_sources(pattern):
    with pytest.raises(ValueError):regex.compile_pattern(pattern)


@pytest.mark.parametrize('path,method',[
    ('/v1/auth/mfa/new-admin-action','POST'),('/v1/auth/sso/provider/delete','DELETE'),
    ('/v1/webhooks-admin','GET'),('/docs-sensitive','GET'),('/v1/auth/login','DELETE'),
    ('/v1/webhooks/github/push/extra','POST'),('/v1/auth/mfa/finish','GET')])
def test_new_routes_do_not_inherit_public_exemptions(path,method):
    assert not is_public_request(request(path,method))


@pytest.mark.parametrize('path,method',[
    ('/v1/auth/login','POST'),('/v1/auth/mfa/finish','POST'),
    ('/v1/webhooks/github/push','POST'),('/v1/auth/sso/github/start','GET'),
    ('/v1/agents/enroll','POST'),('/health','GET')])
def test_explicit_public_protocol_routes(path,method):
    assert is_public_request(request(path,method))


@pytest.mark.parametrize('origin,allowed',[
    ('https://keen.example',True),('https://keen.example:443',True),
    ('https://sibling.example',False),('https://keen.example.evil',False),
    ('null',False),('https://keen.example/path',False),('http://keen.example',False),
    ('https://keen.example\\@evil.example',False)])
def test_origin_boundary_for_self_hosted_http_and_websockets(monkeypatch,origin,allowed):
    monkeypatch.setattr(settings,'public_base_url','https://keen.example')
    monkeypatch.setattr(settings,'hosted_mode',False)
    assert csrf.request_origin_allowed(request(origin=origin),require_origin=True) is allowed


def test_origin_fallback_and_missing_origin(monkeypatch):
    monkeypatch.setattr(settings,'public_base_url','')
    assert csrf.request_origin_allowed(request(origin='https://keen.example'),require_origin=True)
    assert not csrf.request_origin_allowed(request(origin='https://evil.example'),require_origin=True)
    assert not csrf.request_origin_allowed(request(),require_origin=True)
    assert csrf.request_origin_allowed(request())


@pytest.fixture
def production_client(contract_db, monkeypatch):
    from app import main
    from app.db.models import User
    from app.security import auth, permissions
    from sqlalchemy.orm import sessionmaker
    redis=fakeredis.FakeRedis(decode_responses=True)
    admin=User(username='security-admin',password_hash='unused',role='admin',is_active=True)
    reader=User(username='security-reader',password_hash='unused',role='normal',is_active=True)
    contract_db.add_all([admin,reader]);contract_db.commit()
    factory=sessionmaker(bind=contract_db.get_bind(),expire_on_commit=False,join_transaction_mode="create_savepoint")
    monkeypatch.setattr(main,'SessionLocal',factory)
    monkeypatch.setattr(auth,'get_valkey',lambda:redis)
    monkeypatch.setattr(permissions,'get_valkey',lambda:redis)
    monkeypatch.setattr(settings,'public_base_url','https://keen.example')
    monkeypatch.setattr(settings,'trust_remote_user',False)
    monkeypatch.setattr(settings,'mfa_policy','optional')
    client=TestClient(main.app,base_url='https://keen.example')
    yield client,redis,admin,reader
    client.close()


def test_production_middleware_protects_all_nonpublic_api_paths(production_client):
    from app import main
    client,*_=production_client
    for path,operations in main.app.openapi()['paths'].items():
        for method in operations:
            if method.upper() not in {'GET','POST','PUT','PATCH','DELETE'}:continue
            if is_public_request(request(path,method)):continue
            response=client.request(method,path)
            assert response.status_code==401,(method,path,response.status_code,response.text[:100])
            assert response.headers['cache-control']=='no-store'
            assert response.headers['x-content-type-options']=='nosniff'


def test_public_policy_matches_real_routes():
    from app import main
    paths=main.app.openapi()['paths']
    for method,patterns in PUBLIC_HTTP_ROUTES.items():
        for path in patterns:
            if path in {'/openapi.json','/docs','/docs/oauth2-redirect','/redoc',
                        '/v1/openapi.json','/v1/docs','/v1/docs/oauth2-redirect','/v1/redoc'}:continue
            assert method.lower() in paths[path],(method,path)


def test_production_csrf_and_admin_gates(production_client):
    client,redis,admin,reader=production_client
    sid=sessions.create_session(redis,user_id=str(admin.id),ttl_seconds=300)
    client.cookies.set(settings.session_cookie_name,sid)
    response=client.patch('/v1/me/preferences',json={})
    assert response.status_code==403 and 'CSRF' in response.text
    client.cookies.set(csrf.csrf_cookie_name(),'known-token')
    response=client.patch('/v1/me/preferences',json={},headers={csrf.csrf_header_name():'known-token','Origin':'https://evil.example'})
    assert response.status_code==403
    sid=sessions.create_session(redis,user_id=str(reader.id),ttl_seconds=300)
    client.cookies.set(settings.session_cookie_name,sid)
    assert client.get('/v1/admin/source-connections').status_code==403


def test_production_body_rejection_has_headers(production_client):
    client,*_=production_client
    response=client.post('/v1/auth/login',content=b'x'*17000)
    assert response.status_code==413
    assert response.headers['cache-control']=='no-store'
    assert response.headers['x-content-type-options']=='nosniff'


def test_websocket_snapshot_releases_database(production_client,monkeypatch):
    from app.api.routes.questions import _notification_snapshot
    from app.db import session as database
    from app import main
    client,redis,admin,reader=production_client
    original=main.SessionLocal;closed=[]
    def factory():
        session=original();close=session.close
        def closing():closed.append(True);close()
        session.close=closing;return session
    monkeypatch.setattr(database,'SessionLocal',factory)
    sid=sessions.create_session(redis,user_id=str(reader.id),ttl_seconds=300)
    result=_notification_snapshot({}, {settings.session_cookie_name:sid}, counts=True)
    assert result[0][0]==str(reader.id) and closed==[True]
    sessions.delete_session(redis,sid)
    assert _notification_snapshot({}, {settings.session_cookie_name:sid}) is None
    assert len(closed)==2


def test_notification_connection_limits_and_cleanup():
    from app.realtime.notifications import NotificationHub
    async def run():
        hub=NotificationHub();sockets=[Mock() for _ in range(6)]
        for sock in sockets[:5]:assert await hub.connect(sock,user_id='one',is_admin=False)
        assert not await hub.connect(sockets[5],user_id='one',is_admin=False)
        await hub.disconnect(sockets[0])
        assert await hub.connect(sockets[5],user_id='one',is_admin=False)
        for sock in sockets:await hub.disconnect(sock)
        assert not hub._user_sockets and not hub._admin_sockets
    asyncio.run(run())


def test_oauth_client_closed_on_failed_exchange(monkeypatch):
    from dataclasses import replace
    client=SimpleNamespace(fetch_token=AsyncMock(side_effect=RuntimeError('upstream failed')),aclose=AsyncMock())
    monkeypatch.setattr(oidc,'_new_client',lambda *a,**kw:client)
    provider=replace(oidc._oidc_provider(),redirect_uri='https://keen.example/callback')
    with pytest.raises(RuntimeError):
        asyncio.run(oidc._handle_oidc_callback(request(),None,provider,SimpleNamespace(code_verifier='proof')))
    client.aclose.assert_awaited_once()


def test_taiga_tokens_are_isolated_between_connections_and_runs(monkeypatch):
    from app.ingest import taiga
    from app.ingest.connections import Connection, connection_scope
    requests=[]
    class Client:
        def post(self,path,*,json):
            requests.append((taiga.settings.taiga_base_url,json['username']))
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'auth_token':'token-'+json['username']})
        def close(self):pass
    monkeypatch.setattr(taiga,'_base_client',lambda:Client())
    first=Connection('one','taiga','one',{'base_url':'https://first.example','username':'alice'},{'password':'one'}, {})
    second=Connection('two','taiga','two',{'base_url':'https://second.example','username':'bob'},{'password':'two'}, {})
    with connection_scope(first):
        assert taiga._login_and_get_token()=='token-alice'
        assert taiga._login_and_get_token()=='token-alice'
    with connection_scope(second):assert taiga._login_and_get_token()=='token-bob'
    with connection_scope(first):assert taiga._login_and_get_token()=='token-alice'
    assert requests==[('https://first.example','alice'),('https://second.example','bob'),('https://first.example','alice')]


def test_bookstack_lookup_isolated_between_connections():
    from app.ingest import bookstack
    from app.ingest.connections import Connection, connection_scope
    def client(ident):
        return SimpleNamespace(get=lambda *a,**kw:SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'data':[{'slug':'same','id':ident}]}))
    first=Connection('one','bookstack','one',{}, {}, {})
    second=Connection('two','bookstack','two',{}, {}, {})
    with connection_scope(first):assert bookstack._resolve_book_id(client(1),'same')==1
    with connection_scope(second):assert bookstack._resolve_book_id(client(2),'same')==2


def test_sso_start_rate_limit_rejects_before_creating_state(monkeypatch):
    from app.core import valkey
    redis=fakeredis.FakeRedis();monkeypatch.setattr(valkey,'get_valkey',lambda:redis)
    monkeypatch.setattr(oidc,'_require_provider_config',lambda key:object())
    redis.set('keen:rl:sso-start:unknown',30,ex=300)
    db=Mock()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:asyncio.run(oidc.build_authorize_redirect(request(),db,None,'oidc'))
    assert error.value.status_code==429
    db.add.assert_not_called()


def test_abandoned_sso_state_is_cleaned(contract_db):
    from app.db.models import OidcLoginState
    from datetime import timedelta
    contract_db.add(OidcLoginState(state='expired',nonce='n',code_verifier='v',expires_at=oidc._now()-timedelta(seconds=1)))
    contract_db.commit()
    new=oidc.create_login_state(contract_db,next_url='/')
    assert contract_db.query(OidcLoginState).filter_by(state='expired').count()==0
    assert contract_db.query(OidcLoginState).count()==1


def test_pdf_preview_concurrency_is_bounded_and_slots_survive_failure(monkeypatch):
    from app.api.routes import artifacts
    from fastapi import HTTPException
    assert artifacts._PREVIEW_SLOTS.acquire(blocking=False)
    assert artifacts._PREVIEW_SLOTS.acquire(blocking=False)
    run=Mock();monkeypatch.setattr(artifacts.subprocess,'run',run)
    try:
        with pytest.raises(HTTPException) as error:artifacts._run_preview(['renderer'])
        assert error.value.status_code==429
        run.assert_not_called()
    finally:
        artifacts._PREVIEW_SLOTS.release();artifacts._PREVIEW_SLOTS.release()
    run.side_effect=RuntimeError('renderer failure')
    with pytest.raises(RuntimeError):artifacts._run_preview(['renderer'])
    assert artifacts._PREVIEW_SLOTS.acquire(blocking=False)
    assert artifacts._PREVIEW_SLOTS.acquire(blocking=False)
    artifacts._PREVIEW_SLOTS.release();artifacts._PREVIEW_SLOTS.release()


def test_notification_webhook_does_not_buffer_hostile_response():
    import httpx
    from app.outbound.question_webhooks import _post_json
    class HostileStream(httpx.SyncByteStream):
        closed=False
        def __iter__(self):raise AssertionError('Response body should not be read')
        def close(self):self.closed=True
    stream=HostileStream()
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(204,stream=stream))) as client:
        assert _post_json(client,'https://notify.example',{})==204
    assert stream.closed


def test_builtin_transport_cannot_disable_certificate_verification(monkeypatch):
    from app.ingest import http
    constructor=Mock();monkeypatch.setattr(http.httpx,'Client',constructor)
    http.client(verify=False,follow_redirects=True,trust_env=True)
    assert constructor.call_args.kwargs['verify'] is True
    assert constructor.call_args.kwargs['follow_redirects'] is False
    assert constructor.call_args.kwargs['trust_env'] is False
    constructor.call_args.kwargs['transport'].close()


def test_ui_pages_compatible_with_external_scripts_only_policy():
    from pathlib import Path
    from html.parser import HTMLParser
    root=Path(__file__).resolve().parents[2]/'ui'
    class Check(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag=='script':assert dict(attrs).get('src'),self.path
            assert not any(key.startswith('on') for key,_ in attrs),self.path
    for path in (root/'public').rglob('*.html'):
        parser=Check();parser.path=path;parser.feed(path.read_text())
    policy=(root/'nginx/default.conf').read_text()
    assert "script-src 'self';" in policy
    assert "'unsafe-eval'" not in policy
    assert "object-src 'none'" in policy


def test_websocket_rejects_cross_origin_before_database(production_client,monkeypatch):
    from app.api.routes import questions
    from starlette.websockets import WebSocketDisconnect
    client,*_=production_client
    snapshot=Mock();monkeypatch.setattr(questions,'_notification_snapshot',snapshot)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/ws/notifications',headers={'Origin':'https://evil.example'}):pass
    snapshot.assert_not_called()


def test_live_websocket_closes_after_logout(production_client,monkeypatch):
    from app.api.routes import questions
    from app.db import session as database
    from app import main
    from starlette.websockets import WebSocketDisconnect
    client,redis,admin,reader=production_client
    monkeypatch.setattr(database,'SessionLocal',main.SessionLocal)
    monkeypatch.setattr(questions,'_NOTIFICATION_RECHECK_SECONDS',0.01)
    sid=sessions.create_session(redis,user_id=str(reader.id),ttl_seconds=300)
    client.cookies.set(settings.session_cookie_name,sid)
    with client.websocket_connect('/ws/notifications',headers={'Origin':'https://keen.example'}) as socket:
        assert socket.receive_json()['type']=='questions.unread_replies_count'
        sessions.delete_session(redis,sid)
        with pytest.raises(WebSocketDisconnect):
            for _ in range(10):socket.receive_json()
    assert not questions.notification_hub._user_sockets


def test_production_mfa_requires_verified_current_security_version(production_client,contract_db):
    client,redis,admin,reader=production_client
    reader.mfa_enabled=True;reader.mfa_version=7;contract_db.commit()
    for verified,version,expected in [(False,7,401),(True,6,401),(True,7,204)]:
        sid=sessions.create_session(redis,user_id=str(reader.id),ttl_seconds=300,
                                    mfa_verified=verified,mfa_version=version)
        client.cookies.set(settings.session_cookie_name,sid)
        assert client.get('/v1/auth/check').status_code==expected


def test_production_code_never_opts_out_of_tls_verification():
    import ast
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'app'
    for path in root.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.Call):
                for option in node.keywords:
                    if option.arg in {'verify','verify_ssl','check_hostname'}:
                        assert not (isinstance(option.value,ast.Constant) and option.value.value is False),(path,node.lineno)
            if isinstance(node,ast.Attribute):
                assert node.attr != 'CERT_NONE',(path,node.lineno)
            if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant) and node.value.value is False:
                assert not any(isinstance(target,ast.Attribute) and target.attr=='check_hostname' for target in node.targets),(path,node.lineno)
