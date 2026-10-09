"""Cache misses/outages never alter visibility scope or suppress real failures."""
from datetime import date,datetime
from types import SimpleNamespace
from unittest.mock import Mock
import fakeredis
import pytest
from app.core import cache
from app.core.config import settings

@pytest.fixture
def redis(monkeypatch):
    r=fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cache,'get_valkey',lambda:r)
    monkeypatch.setattr(settings,'aggregate_cache_ttl_seconds',60)
    return r


def test_cache_keys_are_stable_and_visibility_isolated():
    assert cache.make_cache_key('events',{'a':1,'b':{'x':2,'y':3}})==cache.make_cache_key('events',{'b':{'y':3,'x':2},'a':1})
    assert cache.make_cache_key('events',{'date':date(2025,1,1)})==cache.make_cache_key('events',{'date':'2025-01-01'})
    a=cache.user_cache_scope(SimpleNamespace(id='a'));b=cache.user_cache_scope(SimpleNamespace(id='b'))
    assert a!=b and a!=cache.user_cache_scope(None)
    assert cache.make_cache_key('events',{'user':a})!=cache.make_cache_key('events',{'user':b})
    assert cache.make_cache_key('events',{'framework':'A'})!=cache.make_cache_key('events',{'framework':'B'})


def test_cached_result_only_calls_factory_on_miss(redis):
    factory=Mock(return_value={'items':[1,2]})
    assert cache.cached_json('events',{},factory)=={'items':[1,2]}
    assert cache.cached_json('events',{},factory)=={'items':[1,2]}
    factory.assert_called_once()
    assert 0<redis.ttl(cache.make_cache_key('events',{}))<=60


def test_corrupt_cache_is_recomputed(redis):
    redis.set(cache.make_cache_key('events',{}),'bad json')
    assert cache.cached_json('events',{},lambda:{'ok':True})=={'ok':True}
    assert cache.cache_get_json('events',{})=={'ok':True}


def test_disabled_cache_does_not_connect(monkeypatch):
    monkeypatch.setattr(settings,'aggregate_cache_ttl_seconds',0)
    connect=Mock(side_effect=AssertionError('must not connect'));monkeypatch.setattr(cache,'get_valkey',connect)
    assert cache.cached_json('x',{},lambda:7)==7
    assert cache.cache_get_json('x',{}) is None
    cache.cache_set_json('x',{},7)
    connect.assert_not_called()


def test_redis_outage_falls_back_but_factory_errors_propagate(monkeypatch):
    monkeypatch.setattr(settings,'aggregate_cache_ttl_seconds',60)
    monkeypatch.setattr(cache,'get_valkey',Mock(side_effect=ConnectionError('offline')))
    assert cache.cached_json('x',{},lambda:7)==7
    with pytest.raises(ValueError,match='real query failed'):
        cache.cached_json('x',{},Mock(side_effect=ValueError('real query failed')))


def test_prefix_deletion_does_not_delete_sessions(redis):
    for i in range(510):cache.cache_set_json('events',{'i':i},i)
    redis.set('keen:session:keep','session')
    assert cache.cache_delete_prefix()==510
    assert redis.get('keen:session:keep')=='session'
