import pytest
from app.mapping.fields import validate_fields, field_matches
from app.mapping.rules import parse_rules, evaluate_by_framework
from app.agents.schema import Health

def test_literal_dotted_keys_and_missing():
    c={'path':['fields','service.name'],'operator':'equals','value':'keen-agent.service'}
    validate_fields([c])
    assert field_matches({'fields':{'service.name':'keen-agent.service'}},c)
    assert not field_matches({'fields':{'service':{'name':'keen-agent.service'}}},c)
    assert not field_matches({},c)

@pytest.mark.parametrize('op,value,expected',[('equals','package',False),('starts_with','package',True),('contains','upgrade',True),('exists','',True)])
def test_operators(op,value,expected):
    assert field_matches({'action':'package.upgrade'},{'path':['action'],'operator':op,'value':value})==expected

def test_runtime_and_historical_payload_rule():
    rules=parse_rules({'rules':[{'id':'agent-service','when':{'source':'keen-agent','fields':[{'path':['fields','_SYSTEMD_UNIT'],'operator':'equals','value':'keen-agent.service'}]},'map_to':[{'framework':'ISO27001:2022','ref':'A.8.15'}]}]})
    event={'source':'keen-agent','normalized_payload':{'fields':{'_SYSTEMD_UNIT':'keen-agent.service'}}}
    assert evaluate_by_framework(event,rules)
    event['normalized_payload']={}
    assert not evaluate_by_framework(event,rules)

@pytest.mark.parametrize('conditions',[[{'path':['fields'],'operator':'eval','value':'x'}],[{'path':'fields.name','operator':'exists'}],[{'path':['a'],'operator':'equals','value':{}}]])
def test_invalid_conditions_rejected(conditions):
    with pytest.raises(ValueError):validate_fields(conditions)

def test_health_accepts_delivery_counters():
    h=Health.model_validate({'version':'0.1.1','delivered_since_start':2496,'delivery_batches_since_start':39,'delivery_events_per_second_since_start':42.99,'last_delivery_at':'2026-10-06T04:25:45Z'})
    assert h.delivered_since_start==2496
    with pytest.raises(ValueError):Health.model_validate({'delivery_events_per_second_since_start':float('nan')})
    with pytest.raises(ValueError):Health.model_validate({'unexpected':'x'})

@pytest.mark.parametrize('ip,network,expected', [('35.221.69.202','35.221.69.0/24',True),('2001:db8::1','2001:db8::/32',True),('2001:db8::1','192.0.2.0/24',False),('invalid','192.0.2.0/24',False)])
def test_ip_cidr(ip,network,expected):
    c={'path':['fields','client.ip'],'operator':'in_cidr','value':network}
    validate_fields([c])
    assert field_matches({'fields':{'client.ip':ip}},c)==expected

def test_invalid_network_rejected():
    with pytest.raises(ValueError):validate_fields([{'path':['fields','client.ip'],'operator':'in_cidr','value':'bad'}])

def test_filtered_health_counts():
    assert Health.model_validate({'filtered_by_source':{'nginx':5}}).filtered_by_source['nginx']==5
    with pytest.raises(ValueError):Health.model_validate({'filtered_by_source':{'nginx':-1}})
