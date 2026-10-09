from app.core.datetime_utils import utc_now_naive
from types import SimpleNamespace
import uuid
import pytest
from fastapi import HTTPException
from tests.test_integration_builder import database
from app.api.routes import managed_configurations as api
from app.db.models import (ManagedConfiguration, IntegrationConnection, IntegrationCollector,
    IntegrationRun, IntegrationRevision, Event, Mapping, ControlItem)


def seed_rules(db):
    db.add(ManagedConfiguration(name='rules',version=1,document={'rules':[
        {'id':'owned','when':{'source':'integration:collector'},'map_to':[{'framework':'test','ref':'A.1'}]},
        {'id':'other','when':{'source':'github'},'map_to':[{'framework':'test','ref':'A.1'}]}]}))
    db.commit()


def test_connection_cascade_keeps_unrelated_rules(database,monkeypatch):
    db,_=database;seed_rules(db)
    from app.services import control_evidence_stats
    monkeypatch.setattr(control_evidence_stats,'rebuild_framework_event_stats',lambda *a,**k:None)
    monkeypatch.setattr(control_evidence_stats,'clear_stats_caches',lambda:None)
    plan=api.preview_connection_removal('api:connection',db)
    assert [r['id'] for r in plan['definitions']]==['owned']
    result=api.remove_evidence_connection('api:connection',api.ConnectionRemovalInput(fingerprint=plan['fingerprint']),SimpleNamespace(id=None),db)
    assert result['deleted_definitions']==1
    assert db.get(IntegrationConnection,'connection') is None
    assert db.get(IntegrationCollector,'collector') is None
    assert [r['id'] for r in db.get(ManagedConfiguration,'rules').document['rules']]==['other']


def test_active_run_blocks_delete(database):
    db,_=database;seed_rules(db)
    db.add(IntegrationRun(id='run',collector_id='collector',connection_id='connection',revision=1,definition={},status='running'))
    db.commit()
    plan=api.preview_connection_removal('api:connection',db)
    assert plan['blocked']
    with pytest.raises(HTTPException) as error:
        api.remove_evidence_connection('api:connection',api.ConnectionRemovalInput(fingerprint=plan['fingerprint']),SimpleNamespace(id=None),db)
    assert error.value.status_code==409
    assert db.get(IntegrationConnection,'connection') is not None


def test_stale_preview_blocks_delete(database):
    db,_=database;seed_rules(db)
    plan=api.preview_connection_removal('api:connection',db)
    c=db.get(IntegrationConnection,'connection');c.version+=1;db.commit()
    with pytest.raises(HTTPException) as error:
        api.remove_evidence_connection('api:connection',api.ConnectionRemovalInput(fingerprint=plan['fingerprint']),SimpleNamespace(id=None),db)
    assert error.value.status_code==409


def test_builtin_keeps_empty_override(database,monkeypatch):
    db,_=database;seed_rules(db)
    db.add(ManagedConfiguration(name='forgejo',version=1,document={'users':[{'user':'mig5'}],'feeds':[]}));db.commit()
    from app.services import control_evidence_stats
    monkeypatch.setattr(control_evidence_stats,'rebuild_framework_event_stats',lambda *a,**k:None)
    monkeypatch.setattr(control_evidence_stats,'clear_stats_caches',lambda:None)
    plan=api.preview_connection_removal('builtin:forgejo',db)
    api.remove_evidence_connection('builtin:forgejo',api.ConnectionRemovalInput(fingerprint=plan['fingerprint']),SimpleNamespace(id=None),db)
    assert db.get(ManagedConfiguration,'forgejo').document['users']==[]


def test_removes_automatic_links_preserves_manual_links_and_events(database,monkeypatch):
    from datetime import datetime
    db,_=database;seed_rules(db)
    from app.services import control_evidence_stats
    monkeypatch.setattr(control_evidence_stats,'rebuild_framework_event_stats',lambda *a,**k:None)
    monkeypatch.setattr(control_evidence_stats,'clear_stats_caches',lambda:None)
    ev=Event(timestamp=utc_now_naive(),source='integration:collector',summary='Keep this evidence',external_id='1')
    other=Event(timestamp=utc_now_naive(),source='github',summary='Other connection',external_id='2')
    controls=[ControlItem(framework_slug='test',type='custom',ref='A.'+str(i)) for i in range(3)]
    db.add_all([ev,other,*controls]);db.flush()
    db.add_all([Mapping(event_id=ev.id,control_item_id=controls[0].id,method='rule'),Mapping(event_id=ev.id,control_item_id=controls[1].id,method='manual'),Mapping(event_id=other.id,control_item_id=controls[2].id,method='rule')]);db.commit()
    plan=api.preview_connection_removal('api:connection',db)
    assert plan['automatic_mappings']==1
    result=api.remove_evidence_connection('api:connection',api.ConnectionRemovalInput(fingerprint=plan['fingerprint']),SimpleNamespace(id=None),db)
    assert result['deleted_automatic_mappings']==1
    assert db.query(Event).count()==2
    assert sorted(m.method for m in db.query(Mapping))==['manual','rule']


def test_individual_ingester_removal_preserves_connection_siblings_and_evidence(database, monkeypatch):
    db, _ = database
    seed_rules(db)
    from app.services import control_evidence_stats
    monkeypatch.setattr(control_evidence_stats, 'rebuild_framework_event_stats', lambda *a, **k: None)
    monkeypatch.setattr(control_evidence_stats, 'clear_stats_caches', lambda: None)
    db.add(IntegrationCollector(id='sibling', name='Other checks', connection_id='connection', draft={}))
    db.add(IntegrationRevision(collector_id='collector', revision=1, connection_id='connection', definition={}))
    db.add(IntegrationRun(id='queued', collector_id='collector', connection_id='connection', revision=1, definition={}, status='queued'))
    evidence = Event(timestamp=utc_now_naive(), source='integration:collector', summary='Retained evidence', external_id='kept')
    controls = [ControlItem(framework_slug='test', type='custom', ref=f'A.{i}') for i in range(3)]
    db.add_all([evidence, *controls]); db.flush()
    db.add_all([Mapping(event_id=evidence.id, control_item_id=c.id, method=method)
                for c, method in zip(controls, ['rule', 'manual', 'import'])])
    db.commit()
    plan = api.preview_connection_removal('collector:collector', db)
    assert plan['collectors'] == [{'id': 'collector', 'name': 'Checks'}]
    assert plan['collections'] == 1
    result = api.remove_evidence_connection('collector:collector', api.ConnectionRemovalInput(fingerprint=plan['fingerprint']), SimpleNamespace(id=None), db)
    assert result['deleted_definitions'] == result['deleted_automatic_mappings'] == 1
    assert db.get(IntegrationCollector, 'collector') is None
    assert db.get(IntegrationCollector, 'sibling') is not None
    assert db.get(IntegrationConnection, 'connection') is not None
    assert db.query(IntegrationRevision).count() == db.query(IntegrationRun).count() == 0
    assert db.query(Event).count() == 1
    assert sorted(m.method for m in db.query(Mapping)) == ['import', 'manual']
    assert [r['id'] for r in db.get(ManagedConfiguration, 'rules').document['rules']] == ['other']


@pytest.mark.parametrize('change', ['version', 'running'])
def test_individual_removal_rechecks_preview(database, change):
    db, _ = database
    seed_rules(db)
    plan = api.preview_connection_removal('collector:collector', db)
    if change == 'version':
        db.get(IntegrationCollector, 'collector').version += 1
    else:
        db.add(IntegrationRun(id='active', collector_id='collector', connection_id='connection', revision=1, definition={}, status='running'))
    db.commit()
    with pytest.raises(HTTPException) as error:
        api.remove_evidence_connection('collector:collector', api.ConnectionRemovalInput(fingerprint=plan['fingerprint']), SimpleNamespace(id=None), db)
    assert error.value.status_code == 409
    assert db.get(IntegrationCollector, 'collector') is not None


def test_individual_removal_demo_guard(database, monkeypatch):
    db, _ = database
    seed_rules(db)
    plan = api.preview_connection_removal('collector:collector', db)
    monkeypatch.setattr(api.settings, 'demo_mode', True)
    with pytest.raises(HTTPException) as error:
        api.remove_evidence_connection('collector:collector', api.ConnectionRemovalInput(fingerprint=plan['fingerprint']), SimpleNamespace(id=None), db)
    assert error.value.status_code == 403
    assert db.get(IntegrationCollector, 'collector') is not None
