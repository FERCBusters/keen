"""Agent/OTLP validation, deterministic identity, enrichment and batch bounds."""
import copy
import json
import uuid
from datetime import datetime,timedelta,timezone
import pytest
from pydantic import ValidationError
from app.agents.schema import AgentEvent,Health,fingerprint
from app.agents.otlp import attributes,decode,value


def payload(**overrides):
    return {'id':str(uuid.uuid4()),'timestamp':'2025-01-01T00:00:00Z',
            'source':'ossec','action':'ossec.alert','summary':'Alert',**overrides}


@pytest.mark.parametrize('change',[
 {'source':''},{'source':'space name'},{'source':'x'*65},{'action':''},{'action':'x'*129},
 {'summary':''},{'summary':'x'*4097},{'raw':'x'*65537},{'severity':-1},{'severity':11},
 {'timestamp':'2025-01-01T00:00:00'},{'timestamp':'1999-12-31T23:59:59Z'},
 {'timestamp':datetime.now(timezone.utc)+timedelta(days=1)},
 {'fields':{'key':'x'*4097}},{'fields':{'x'*257:'ok'}},
 {'fields':{str(i):'x' for i in range(129)}},
 {'fields':{str(i):'x'*4096 for i in range(9)}},
 {'unexpected':'field'},{'id':'bad'},
])
def test_invalid_event_rejected(change):
    with pytest.raises(ValidationError):AgentEvent.model_validate(payload(**change))


@pytest.mark.parametrize('severity',[0,5,10])
def test_valid_event_boundaries_and_offsets(severity):
    event=AgentEvent.model_validate(payload(severity=severity,timestamp='2025-01-01T11:00:00+11:00',
        fields={'ossec.rule.id':'31101','flag':'false'},summary='x'*4096))
    assert event.timestamp.astimezone(timezone.utc).hour==0
    assert event.fields['ossec.rule.id']=='31101'


def test_fingerprint_independent_of_field_order_but_sensitive_to_content():
    data=payload(fields={'a':'1','b':'2'})
    a=AgentEvent.model_validate(data)
    b=AgentEvent.model_validate({**data,'fields':{'b':'2','a':'1'}})
    assert fingerprint(a)==fingerprint(b)
    assert fingerprint(a)!=fingerprint(b.model_copy(update={'summary':'changed'}))


@pytest.mark.parametrize('field', ['queued','queue_bytes','rejected','delivered_since_start','delivery_batches_since_start'])
def test_negative_health_counters_rejected(field):
    with pytest.raises(ValidationError):Health.model_validate({field:-1})


@pytest.mark.parametrize('item,expected',[
 ({},None),({'stringValue':'hi'},'hi'),({'boolValue':False},False),
 ({'intValue':'42'},42),({'doubleValue':1.5},1.5),({'bytesValue':'YQ=='},{'base64':'YQ=='}),
 ({'arrayValue':{'values':[{'intValue':'1'},{'stringValue':'x'}]}},[1,'x']),
 ({'kvlistValue':{'values':[{'key':'x','value':{'boolValue':True}}]}},{'x':True}),
])
def test_otlp_anyvalue_conversion(item,expected):
    assert value(item)==expected


@pytest.mark.parametrize('item',[
 [],None,{'unknown':1},{'intValue':True},{'doubleValue':True},
 {'stringValue':1},{'boolValue':'false'},{'intValue':'not integer'},
 {'stringValue':'x','boolValue':True},{'arrayValue':{'values':'bad'}},
 {'arrayValue':{'values':[{}]*129}},
])
def test_invalid_otlp_values(item):
    with pytest.raises(ValueError):value(item)


def test_otlp_depth_and_attribute_limits():
    nested={'stringValue':'x'}
    for _ in range(10):nested={'arrayValue':{'values':[nested]}}
    with pytest.raises(ValueError):value(nested)
    for data in [[{'key':'a'},{'key':'a'}],[{'key':'x'*129}],[{'key':1}],[None],[{}]*129,{}]:
        with pytest.raises(ValueError):attributes(data)


def document(record=None):
    return {'resourceLogs':[{'resource':{'attributes':[{'key':'host.name','value':{'stringValue':'server'}}]},
        'scopeLogs':[{'scope':{'name':'agent'},'logRecords':[record or {
          'timeUnixNano':'1735689600123456789','severityNumber':10,'body':{'stringValue':'alert'},
          'attributes':[{'key':'keen.source','value':{'stringValue':'ossec'}},
                        {'key':'keen.field.ossec.rule.id','value':{'stringValue':'31101'}}]}]}]}]}


def test_otlp_identity_and_enrichment_are_stable_and_agent_scoped():
    raw=json.dumps(document())
    a=decode(raw,'a')[0];b=decode(raw,'a')[0];other=decode(raw,'b')[0]
    assert a==b and a.id!=other.id
    assert a.fields['ossec.rule.id']=='31101'
    assert a.fields['resource.host.name']=='server'
    assert a.fields['otel.time_unix_nano']=='1735689600123456789'
    assert a.timestamp.microsecond==123456 and a.severity==5
    assert a.source=='ossec' and a.action=='log.record'


def test_otlp_duplicate_retry_collapses_but_uuid_conflicts_fail():
    doc=document();records=doc['resourceLogs'][0]['scopeLogs'][0]['logRecords']
    records.append(copy.deepcopy(records[0]))
    assert len(decode(json.dumps(doc),'a'))==1
    eid=str(uuid.uuid4())
    for record in records:record['attributes'].append({'key':'keen.event.id','value':{'stringValue':eid}})
    records[1]['body']={'stringValue':'different'}
    with pytest.raises(ValueError,match='Conflicting'):decode(json.dumps(doc),'a')


@pytest.mark.parametrize('key',['keen.field.resource.host','keen.field.otel.scope'])
def test_enrichment_cannot_overwrite_reserved_provenance(key):
    doc=document();doc['resourceLogs'][0]['scopeLogs'][0]['logRecords'][0]['attributes'].append({'key':key,'value':{'stringValue':'spoof'}})
    with pytest.raises(ValueError,match='Reserved'):decode(json.dumps(doc),'a')


def test_otlp_batch_bound_is_checked_before_duplicate_collapse():
    doc=document();records=doc['resourceLogs'][0]['scopeLogs'][0]['logRecords']
    records.extend(copy.deepcopy(records[0]) for _ in range(64))
    with pytest.raises(ValueError,match='64'):decode(json.dumps(doc),'a')


@pytest.mark.parametrize('doc',[[],{'resourceLogs':{}},{'resourceLogs':[{}]*65},
    {'resourceLogs':[{'scopeLogs':[{}]*65}]},
    {'resourceLogs':[{'scopeLogs':[{'logRecords':'bad'}]}]}])
def test_otlp_invalid_container_shapes(doc):
    with pytest.raises(ValueError):decode(json.dumps(doc),'a')
