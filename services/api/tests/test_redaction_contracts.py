"""Secret redaction and sample masking preserve useful non-sensitive evidence."""
import json
from urllib.parse import parse_qs,urlsplit
import pytest
from app.security import redaction as r

@pytest.fixture(autouse=True)
def fresh_policy(monkeypatch):
    monkeypatch.delenv('KEEN_SENSITIVE_KEYS',raising=False)
    monkeypatch.delenv('KEEN_SENSITIVE_KEYS_MODE',raising=False)
    r._sensitive_cfg.cache_clear()
    yield
    r._sensitive_cfg.cache_clear()

@pytest.mark.parametrize('key',['token','password','api_key','api-key','Authorization','client_secret','cookie'])
def test_sensitive_object_keys_are_redacted_at_every_depth(key):
    obj={'items':[{key:'DO-NOT-LEAK','count':0,'enabled':False}],'safe':'retained'}
    result=r.redact_obj(obj)
    assert 'DO-NOT-LEAK' not in json.dumps(result)
    assert result['items'][0]['count']==0 and result['items'][0]['enabled'] is False
    assert obj['items'][0][key]=='DO-NOT-LEAK'


def test_url_redaction_keeps_path_safe_query_and_fragment():
    result=urlsplit(r.redact_url('https://example.test/path?token=DO-NOT-LEAK&safe=hello#section'))
    assert result.path=='/path' and result.fragment=='section'
    assert parse_qs(result.query)=={'token':['REDACTED'],'safe':['hello']}

@pytest.mark.parametrize('text',['token=DO-NOT-LEAK','password: DO-NOT-LEAK','https://example.test/?api_key=DO-NOT-LEAK'])
def test_free_text_secrets_are_removed(text):
    assert 'DO-NOT-LEAK' not in r.redact_str(text)


def test_custom_redaction_extends_defaults_and_replace_is_explicit(monkeypatch):
    monkeypatch.setenv('KEEN_SENSITIVE_KEYS','custom_secret');r._sensitive_cfg.cache_clear()
    assert r.redact_obj({'custom_secret':'x','password':'y'})=={'custom_secret':'REDACTED','password':'REDACTED'}
    monkeypatch.setenv('KEEN_SENSITIVE_KEYS_MODE','replace');r._sensitive_cfg.cache_clear()
    assert r.redact_obj({'custom_secret':'x','password':'y'})=={'custom_secret':'REDACTED','password':'y'}

@pytest.mark.parametrize('text,expected',[
 ('contact a@example.test','contact [MASKED_EMAIL]'),('from 192.0.2.1','from [MASKED_IP]'),
 ('from 2001:db8::1','from [MASKED_IP]'),('at 13:40:19','at 13:40:19'),
 ('version 1.2.3','version 1.2.3'),('bad 999.999.999.999','bad 999.999.999.999')])
def test_sample_masking_does_not_confuse_times_or_versions(text,expected):
    assert r.mask_event_data_str(text)==expected

@pytest.mark.parametrize('content_type',['application/json','application/problem+json','text/plain','application/xml'])
def test_text_artifact_redaction(content_type):
    data=(b'{"token":"DO-NOT-LEAK"}' if 'json' in content_type else
          b'<item token="DO-NOT-LEAK" />' if 'xml' in content_type else b'token=DO-NOT-LEAK')
    result,status=r.redact_bytes(data,content_type)
    assert b'DO-NOT-LEAK' not in result and status=='redacted'


def test_binary_artifacts_are_not_corrupted():
    data=b'\x00\xff\x80password=secret'
    assert r.redact_bytes(data,'application/octet-stream')==(data,'skipped')
    assert r.mask_event_data_bytes(data,'image/png')==(data,'skipped')


def test_json_sample_masking_preserves_structure_and_scalars():
    data=json.dumps({'ip':'192.0.2.1','nested':[{'email':'a@example.test'}],'n':0,'flag':False}).encode()
    result,status=r.mask_event_data_bytes(data,'application/json')
    assert status=='redacted'
    assert json.loads(result)=={'ip':'[MASKED_IP]','nested':[{'email':'[MASKED_EMAIL]'}],'n':0,'flag':False}

@pytest.mark.parametrize('statuses,expected',[
 ([], 'unknown'),(['clean'],'clean'),(['skipped'],'skipped'),(['clean','skipped'],'clean'),
 (['clean','redacted'],'redacted'),([None,'redacted'],'redacted')])
def test_redaction_status_composition(statuses,expected):
    assert r.combine_redaction_status(*statuses)==expected
