"""Reproducible redaction, diagnostic and response-boundary regressions."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import httpx2
import pytest
from app.security import redaction
from app.security.errors import collection_error
from app.security import sso_http


@pytest.mark.parametrize('value', [
    'api-key=SECRET_VALUE', 'client-secret: SECRET_VALUE',
    'password="first SECRET_VALUE last"', "password='first SECRET_VALUE last'",
    'https://alice:SECRET_VALUE@service.example/path',
    'https://service.example/path#access_token=SECRET_VALUE&state=public',
    'Set-Cookie: sid=SECRET_VALUE; Secure; HttpOnly',
    'refresh-token=SECRET_VALUE', 'id-token=SECRET_VALUE',
])
def test_secrets_removed_from_text_and_text_artifacts(value):
    assert 'SECRET_VALUE' not in redaction.redact_str(value)
    result, status = redaction.redact_bytes(value.encode(), 'text/plain')
    assert b'SECRET_VALUE' not in result
    assert status == 'redacted'


def test_redaction_preserves_safe_url_and_fields():
    assert redaction.redact_url('https://example.test/path?q=hello#section') == 'https://example.test/path?q=hello#section'
    assert redaction.redact_str('password="two words" outcome=success') == 'password=REDACTED outcome=success'


def test_event_columns_are_redacted_before_database_storage(contract_db, monkeypatch):
    from app.ingest import common
    from app.db.models import Event
    stored = []
    def put(**kw):
        stored.append(kw['data'])
        return SimpleNamespace(uri='local://local/test.json', sha256='a'*64, size_bytes=len(kw['data']))
    monkeypatch.setattr(common, 'put_bytes', put)
    monkeypatch.setattr(common, 'apply_rules', lambda *a: 0)
    common.store_event_with_artifact(contract_db, timestamp=datetime(2026, 1, 1),
        source='rss', system='https://name:SECRET_VALUE@example.test/', actor='api-key=SECRET_VALUE',
        action='password="some SECRET_VALUE text"', outcome='token=SECRET_VALUE',
        severity=1, summary='client-secret=SECRET_VALUE', raw_pointer={'token':'SECRET_VALUE'},
        normalized_payload={'password':'SECRET_VALUE'}, external_id='redaction-proof',
        artifact_kind='json', artifact_bytes=b'{"password":"SECRET_VALUE"}',
        artifact_content_type='application/json', artifact_key='test.json', captured_by='test')
    row=contract_db.query(Event).filter_by(external_id='redaction-proof').one()
    for value in (row.system,row.actor,row.action,row.outcome,row.summary,str(row.raw_pointer),str(row.normalized_payload)):
        assert 'SECRET_VALUE' not in value
    assert b'SECRET_VALUE' not in stored[0]


@pytest.mark.parametrize('error', [
    RuntimeError('SQL parameters password=SECRET_VALUE'),
    ValueError('Remote body SECRET_VALUE'),
    httpx.ConnectError('https://example.test/private/SECRET_VALUE'),
    httpx.HTTPStatusError('SECRET_VALUE', request=httpx.Request('GET','https://example.test/SECRET_VALUE'), response=httpx.Response(403)),
])
def test_public_diagnostics_never_include_exception_text(error):
    assert 'SECRET_VALUE' not in collection_error(error)
    if isinstance(error, httpx.HTTPStatusError):
        assert '403' in collection_error(error)


def test_rss_failure_result_hides_provider_exception(monkeypatch):
    from app.ingest import rss
    # Test the collector itself: the fanout cannot rely on catching an error
    # already swallowed into a result row by an adapter.
    monkeypatch.setattr(rss.settings, 'rss_enabled', True)
    monkeypatch.setattr(rss.settings, 'demo_mode', False)
    monkeypatch.setattr(rss, 'load_rss_config', lambda *a: {'feeds':[{'url':'https://example.test/?token=SECRET_VALUE'}]})
    monkeypatch.setattr(rss, 'ingest_rss_feed', Mock(side_effect=RuntimeError('SQL bound payload SECRET_VALUE')))
    result=rss.ingest_rss_all.__wrapped__.__wrapped__(Mock())
    assert 'SECRET_VALUE' not in str(result)
    assert result[0]['ok'] is False


class AsyncChunks(httpx2.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks, self.delay, self.closed = chunks, delay, False
    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk
    async def aclose(self):
        self.closed=True


def test_sso_response_size_limit_closes_stream(monkeypatch):
    monkeypatch.setattr(sso_http,'MAX_RESPONSE_BYTES',16)
    async def run():
        source=AsyncChunks([b'a'*8,b'b'*9])
        response=httpx2.Response(200,stream=sso_http.LimitedStream(source,asyncio.get_running_loop().time()+1))
        with pytest.raises(ValueError,match='exceeds'):
            await response.aread()
        assert source.closed
    asyncio.run(run())


def test_sso_total_deadline_stops_trickling_response():
    async def run():
        source=AsyncChunks([b'a']*100,delay=0.005)
        response=httpx2.Response(200,stream=sso_http.LimitedStream(source,asyncio.get_running_loop().time()+0.02))
        with pytest.raises(TimeoutError):
            await response.aread()
        assert source.closed
    asyncio.run(run())


def test_sso_transport_rejects_compression_before_reading(monkeypatch):
    source=AsyncChunks([b'never decode this'])
    def endpoint(request):
        assert request.headers['Accept-Encoding']=='identity'
        return httpx2.Response(200,headers={'Content-Encoding':'gzip'},stream=source)
    monkeypatch.setattr(sso_http.httpx,'AsyncHTTPTransport',lambda **kw: httpx2.MockTransport(endpoint))
    async def run():
        async with httpx2.AsyncClient(transport=sso_http.SsoTransport()) as client:
            with pytest.raises(ValueError,match='Accept-Encoding'):
                await client.get('https://issuer.example/jwks')
        assert source.closed
    asyncio.run(run())


def test_ingestion_total_deadline_closes_stream(monkeypatch):
    from app.ingest import http
    source=Mock()
    source.__iter__=Mock(return_value=iter([b'a']))
    monkeypatch.setattr(http.time,'monotonic',lambda: 101)
    with pytest.raises(TimeoutError):
        list(http.LimitedStream(source,deadline=100))
    source.close.assert_called_once()


def test_custom_transport_total_deadline_closes_connection(monkeypatch):
    from app.integrations import transport
    from urllib.parse import urlsplit
    monkeypatch.setattr(transport,'destination',lambda url:(urlsplit(url),['192.0.2.1']))
    response=Mock(status=200)
    response.getheader.return_value='identity'
    response.read1.return_value=b'a'
    connection=Mock()
    connection.getresponse.return_value=response
    monkeypatch.setattr(transport,'PinnedHTTPS',lambda *a:connection)
    ticks=iter([0,0,2])
    monkeypatch.setattr(transport.time,'monotonic',lambda:next(ticks))
    with pytest.raises(TimeoutError):
        transport.request('https://example.test/',timeout=1)
    assert response.read1.call_count==1
    connection.close.assert_called_once()


@pytest.mark.parametrize('url', [
    'https://alice:SECRET_VALUE@example.test/hook',
    'https://example.test?token=SECRET_VALUE',
    'https://example.test/SECRET_VALUE',
])
def test_outbound_log_hint_excludes_credentials(url):
    from app.outbound.question_webhooks import _safe_url_hint
    assert _safe_url_hint(url) == 'https://example.test'


def test_sso_provider_error_cannot_escape_as_traceback(monkeypatch):
    from app.security import oidc
    from fastapi import HTTPException
    from authlib.oauth2.rfc6749.errors import OAuth2Error
    from starlette.requests import Request
    from unittest.mock import AsyncMock
    provider=SimpleNamespace(key='oidc',kind='oidc')
    secret='browser-secret'
    state=oidc._browser_state('oidc',secret)
    request=Request({'type':'http','scheme':'https','server':('keen.example',443),
        'path':'/v1/auth/sso/oidc/callback','query_string':('state='+state).encode(),
        'headers':[(b'cookie',(oidc.sso_binding_cookie()+'='+secret).encode())]})
    monkeypatch.setattr(oidc,'_require_provider_config',lambda *a:provider)
    monkeypatch.setattr(oidc,'pop_login_state',lambda *a,**kw:object())
    monkeypatch.setattr(oidc,'_handle_oidc_callback',AsyncMock(side_effect=OAuth2Error(description='SECRET_VALUE')))
    with pytest.raises(HTTPException) as error:
        asyncio.run(oidc.handle_callback(request,None,'oidc'))
    assert error.value.status_code==400
    assert 'SECRET_VALUE' not in error.value.detail
    assert error.value.__suppress_context__
