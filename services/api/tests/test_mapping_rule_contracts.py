"""Rule parsing and evaluation contracts across formats, filters and provenance."""
import pytest
from app.mapping.rules import parse_rules,evaluate,evaluate_by_framework
from app.mapping.collector import collector_id,event_collector,matches_collector,collector_pointer_filter


def rule(**kwargs):
    return {'id':'test','framework':'A','when':{},'map_to':['C1'],**kwargs}


@pytest.mark.parametrize('wrap',[
 lambda r:[r],lambda r:{'rules':[r]},lambda r:{'frameworks':{'A':[r]}},
 lambda r:{'frameworks':{'A':{'rules':[r]}}},lambda r:{'A':[r]},lambda r:{'A':{'rules':[r]}},
])
def test_supported_rule_layouts_preserve_targets(wrap):
    parsed=parse_rules(wrap(rule()))
    assert evaluate_by_framework({},parsed)=={'A':['C1']}


@pytest.mark.parametrize('field',['source','system','actor','action','outcome'])
def test_exact_and_regex_conditions_are_combined(field):
    parsed=parse_rules([rule(when={field:'package.upgrade',field+'_regex':r'^package\.'})])
    assert evaluate_by_framework({field:'package.upgrade'},parsed)=={'A':['C1']}
    assert not evaluate_by_framework({field:'package.install'},parsed)
    assert not evaluate_by_framework({},parsed)
    parsed=parse_rules([rule(when={field:'package.upgrade',field+'_regex':r'^other\.'})])
    assert not evaluate_by_framework({field:'package.upgrade'},parsed)


def test_severity_zero_and_missing_are_distinct():
    parsed=parse_rules([rule(when={'severity':0})])
    assert evaluate({'severity':0},parsed)==['C1']
    assert evaluate({},parsed)==[]
    assert evaluate({'severity':'not a number'},parsed)==[]


def test_nested_labels_and_summary_fallback():
    parsed=parse_rules([rule(when={'label':'app=sshd','summary_regex':'failure'})])
    event={'normalized_payload':{'labels':{'app':'sshd'},'message':'failure'}}
    assert evaluate(event,parsed)==['C1']
    assert evaluate({**event,'summary':'success'},parsed)==[]


def test_disabled_rules_missing_targets_and_nonobjects_are_ignored():
    assert parse_rules([None,42,rule(enabled=False),rule(map_to=[])])==[]


def test_multi_framework_and_duplicate_provenance_choose_highest_confidence():
    rules=parse_rules([rule(id='low',frameworks=['A','B'],map_to=['C1','C1'],confidence=.2),
                       rule(id='high',confidence=.9)])
    assert evaluate_by_framework({},rules)=={'A':['C1'],'B':['C1']}
    details=evaluate_by_framework({},rules,details=True)
    assert details['A'][0]['confidence']==.9 and 'high' in details['A'][0]['rationale']
    assert details['B'][0]['confidence']==.2
    assert evaluate({},rules)==['C1'] and evaluate({},rules,'missing')==[]


@pytest.mark.parametrize('roles,expected',[(None,False),(set(),False),({'viewer'},False),({' ADMIN '},True)])
def test_target_role_requirements(roles,expected):
    rules=parse_rules([rule(map_to=[{'framework':'A','ref':'C1','roles_any':['admin']}])])
    assert bool(evaluate({},rules,active_roles=roles)) is expected


@pytest.mark.parametrize('source,section,key,info',[
 ('loki','queries','q',{'query_name':'q'}),('cloudwatch_logs','queries','q',{'name':'q'}),
 ('jenkins','jobs','folder/job',{'job':'folder/job'}),('rss','feeds','https://feed',{'feed_url':'https://feed'}),
 ('forgejo','feeds','public',{'feed':'public'}),('gitea','feeds','public',{'feed':'public'}),
 ('riskledger','organizations','org',{'org':'org','section':'organizations'}),
 ('redmine','projects','p',{'project_selector':'p','section':'projects'}),
 ('gitlab','projects','p',{'section':'projects','key':'p'}),
 ('taiga','projects',42,{'project_id':42}),('google_workspace','streams','login',{'stream':'login'}),
 ('bookstack','selected_pages',12,{'page_id':12}),
 ('github','repos','owner/repo',{'owner':'owner','repo':'repo','endpoint':'repo_events'}),
 ('github','organizations','org',{'org':'org'}),('github','feeds','public',{'feed':'public'}),
])
def test_collector_identity_roundtrip_and_other_collection_rejection(source,section,key,info):
    identity=collector_id(source,section,key)
    event={'source':source,'raw_pointer':{source:info}}
    assert event_collector(event)==identity
    assert matches_collector(identity,event)
    assert not matches_collector(collector_id(source,section,'other'),event)
    pointer=collector_pointer_filter(identity)
    assert all(info[k]==v for k,v in pointer[source].items())


def test_webhook_and_owner_collection_identity():
    identity=collector_id('webhooks','providers','monitor')
    event={'source':'webhook:monitor','raw_pointer':{'webhook':{'provider':'monitor'}}}
    assert matches_collector(identity,event)
    assert collector_pointer_filter(identity)==event['raw_pointer']
    for source in ['forgejo','gitea']:
        identity=collector_id(source,'organizations','org')
        assert matches_collector(identity,{'source':source,'raw_pointer':{source:{'owner':'org'}}})
        assert not matches_collector(identity,{'source':'other','raw_pointer':{source:{'owner':'org'}}})


@pytest.mark.parametrize('event',[{}, {'source':'unknown'},{'source':'loki','raw_pointer':[]},{'source':'loki','raw_pointer':{'loki':'bad'}}])
def test_missing_provenance_is_not_an_identity(event):
    assert event_collector(event) is None
