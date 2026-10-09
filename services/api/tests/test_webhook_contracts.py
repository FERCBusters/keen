"""Webhook secrets, replay windows and payload limits without external services."""
from unittest.mock import Mock
import fakeredis
import pytest
from app.ingest import webhooks
from app.core.config import settings

@pytest.fixture
def policy(monkeypatch):
    monkeypatch.setattr(settings,'webhooks_require_secret',True)
    monkeypatch.setattr(webhooks,'load_webhook_policies',lambda path:{'providers':{'monitor':{'secret_header':'X-Monitor-Secret','secret_env':'KEEN_TEST_WEBHOOK_SECRET'}}})
    monkeypatch.setenv('KEEN_TEST_WEBHOOK_SECRET','synthetic-secret')

@pytest.mark.parametrize('headers,expected',[
 ({},False),({'X-Monitor-Secret':'wrong'},False),
 ({'x-monitor-secret':'synthetic-secret'},True),({'X-MONITOR-SECRET':'synthetic-secret'},True),
 ({'other':'synthetic-secret'},False),({'X-Monitor-Secret':''},False),
])
def test_secret_header_validation(policy,headers,expected):
    assert webhooks.verify_secret('monitor',headers) is expected


def test_unconfigured_and_missing_secret_fail_closed(policy,monkeypatch):
    assert not webhooks.verify_secret('unknown',{})
    monkeypatch.delenv('KEEN_TEST_WEBHOOK_SECRET')
    assert not webhooks.verify_secret('monitor',{'X-Monitor-Secret':'synthetic-secret'})
    monkeypatch.setattr(settings,'webhooks_require_secret',False)
    assert webhooks.verify_secret('unknown',{})
    assert not webhooks.verify_secret('monitor',{})

@pytest.mark.parametrize('age,rejected',[(0,False),(899,False),(900,False),(901,True),(-1,True)])
def test_replay_timestamp_boundaries(monkeypatch,age,rejected):
    redis=fakeredis.FakeRedis();monkeypatch.setattr(webhooks,'get_valkey',lambda:redis)
    monkeypatch.setattr('time.time',lambda:2000)
    assert webhooks._check_replay_protection('monitor','alert',b'body',{'x-webhook-timestamp':str(2000-age)}) is rejected


def test_replay_deduplication_is_provider_and_type_scoped(monkeypatch):
    redis=fakeredis.FakeRedis();monkeypatch.setattr(webhooks,'get_valkey',lambda:redis)
    assert not webhooks._check_replay_protection('a','alert',b'body',{})
    assert webhooks._check_replay_protection('a','alert',b'body',{})
    assert not webhooks._check_replay_protection('b','alert',b'body',{})
    assert not webhooks._check_replay_protection('a','other',b'body',{})
    assert all(0<redis.ttl(key)<=960 for key in redis.keys())


def test_oversized_or_replayed_payload_never_reaches_storage(monkeypatch):
    store=Mock();monkeypatch.setattr(webhooks,'store_event_with_artifact',store)
    with pytest.raises(ValueError,match='too large'):
        webhooks.ingest_webhook(None,'a','alert',b'x'*(10*1024*1024+1),{})
    monkeypatch.setattr(webhooks,'_check_replay_protection',lambda *args:True)
    with pytest.raises(ValueError,match='replay'):
        webhooks.ingest_webhook(None,'a','alert',b'{}',{})
    store.assert_not_called()
