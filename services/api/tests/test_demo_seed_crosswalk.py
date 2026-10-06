"""Offline ORM/SQL semantics. PostgreSQL migrations still need a deployment rehearsal."""
import importlib.util
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

os.environ.setdefault('KEEN_DATABASE_URL','postgresql://test:test@localhost/unused')
os.environ.setdefault('KEEN_BOOTSTRAP_ADMIN_USERNAME','test')
os.environ.setdefault('KEEN_BOOTSTRAP_ADMIN_PASSWORD','test-password')
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from app.db.models import Base, User, ControlItem, FrameworkClause, CrossFrameworkControlLink, Event, Mapping
from app.services.control_inheritance import evidence_pairs
from seed_demo_data import DemoSeeder, DemoConfig

@compiles(JSONB,'sqlite')
def sqlite_json(*a,**k):return 'JSON'

@pytest.fixture
def db():
    engine=create_engine('sqlite://')
    @event.listens_for(engine,'connect')
    def functions(conn,record):conn.create_function('NOW',0,lambda:datetime.utcnow().isoformat(' '))
    Base.metadata.create_all(engine)
    session=Session(engine)
    session.add(User(username='demo',password_hash='disabled',role='admin',is_active=True))
    data=json.loads((ROOT/'alembic/seed_frameworks.json').read_text())
    for slug,f in data.items():
        for c in f['controls']:
            session.add(ControlItem(framework_slug=slug,type=c['type'],ref=c['ref'],title=c['title'],meta=c.get('metadata',{})))
        for c in f.get('clauses',[]):
            session.add(FrameworkClause(framework_slug=slug,ref=c['ref'],title=c['title']))
    session.commit()
    yield session
    session.close();engine.dispose()


def migrate(db,monkeypatch):
    spec=importlib.util.spec_from_file_location('crosswalk',ROOT/'alembic/versions/0079_seed_control_crosswalk.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module.op,'get_bind',lambda:db.connection())
    module.upgrade();db.flush()


def test_all_framework_seed_is_idempotent_and_has_no_extra_login_users(db):
    config=DemoConfig('ISO27001:2022','Demo',None,False,True)
    DemoSeeder(db,config).run();db.flush()
    counts={t.name:db.query(t).count() for t in Base.metadata.sorted_tables}
    seeder=DemoSeeder(db,config)
    assert seeder.seed_date==db.query(Event).filter_by(source='demo-seed').one().timestamp.date()
    seeder.run();db.flush()
    assert counts=={t.name:db.query(t).count() for t in Base.metadata.sorted_tables}
    assert counts['users']==1 and counts['isms_people']==6 and counts['risks']>=12
    assert {f for f, in db.query(ControlItem.framework_slug).join(Mapping).distinct()}=={'ISO27001:2022','UK-DVSTF:1.0','KEEN-AF:1.0','CYBER-ESSENTIALS:2026'}
    assert not seeder.warnings


def test_crosswalk_migration_references_existing_controls_and_preserves_edits(db,monkeypatch):
    migrate(db,monkeypatch)
    assert db.query(CrossFrameworkControlLink).count()==312
    row=db.query(CrossFrameworkControlLink).first();row.rationale='Administrator reviewed';db.flush()
    migrate(db,monkeypatch)
    assert db.query(CrossFrameworkControlLink).count()==312
    assert db.get(CrossFrameworkControlLink,row.id).rationale=='Administrator reviewed'


def test_customised_catalogue_is_not_silently_linked(db,monkeypatch):
    control=db.query(ControlItem).filter_by(framework_slug='ISO27001:2022',ref='A.8.7').one()
    control.title='Repurposed control';db.flush();migrate(db,monkeypatch)
    assert not db.query(CrossFrameworkControlLink).filter((CrossFrameworkControlLink.source_control_id==control.id)|(CrossFrameworkControlLink.target_control_id==control.id)).count()


def test_evidence_inherits_one_hop_without_false_reverse_ce_mapping(db,monkeypatch):
    migrate(db,monkeypatch)
    ce=db.query(ControlItem).filter_by(framework_slug='CYBER-ESSENTIALS:2026',ref='A8.1').one()
    iso=db.query(ControlItem).filter_by(framework_slug='ISO27001:2022',ref='A.8.7').one()
    e=Event(source='test',external_id='one',timestamp=datetime.utcnow(),summary='Synthetic malware check')
    db.add(e);db.flush();db.add(Mapping(event_id=e.id,control_item_id=ce.id,confidence=1,method='test',rationale='test'));db.flush()
    pairs=evidence_pairs('ISO27001:2022')
    assert db.scalar(select(pairs.c.event_id).where(pairs.c.control_id==iso.id))==e.id
    e2=Event(source='test',external_id='two',timestamp=datetime.utcnow(),summary='General ISO evidence')
    db.add(e2);db.flush();db.add(Mapping(event_id=e2.id,control_item_id=iso.id,confidence=1,method='test',rationale='test'));db.flush()
    pairs=evidence_pairs('CYBER-ESSENTIALS:2026')
    assert db.scalar(select(pairs.c.event_id).where(pairs.c.control_id==ce.id,pairs.c.event_id==e2.id)) is None
    assert db.query(Mapping).count()==2


@pytest.mark.parametrize('framework',['KEEN-AF:1.0','CYBER-ESSENTIALS:2026','UK-DVSTF:1.0'])
def test_single_non_iso_framework_seeds_without_iso_refs(db,framework):
    seeder=DemoSeeder(db,DemoConfig(framework,'Demo',None,False,False))
    seeder.run();db.flush()
    assert not seeder.warnings
    assert {f for f, in db.query(ControlItem.framework_slug).join(Mapping).distinct()}=={framework}


def test_measured_assurance_has_thresholds_sources_and_chronological_results(db):
    from app.db.models import IsmsEffectivenessMeasure, IsmsEffectivenessMetricEntry, AuditEvidence
    from demo_metrics import LINKS
    seeder=DemoSeeder(db,DemoConfig('ISO27001:2022','Demo',None,False,True))
    seeder.run();db.flush()
    measures=db.query(IsmsEffectivenessMeasure).filter(IsmsEffectivenessMeasure.metric_key.startswith(seeder.account_code('SLA')+'-')).all()
    assert len(measures)==sum(map(len,LINKS.values()))==8
    for measure in measures:
        rows=db.query(IsmsEffectivenessMetricEntry).filter_by(measure_id=measure.id).order_by(IsmsEffectivenessMetricEntry.period_start).all()
        assert len(rows)==6
        assert rows[-1].period_end < seeder.seed_date
        assert {r.raw_payload['assessment'] for r in rows}=={'met','breach'}
        for row in rows:
            source=db.get(Event,row.source_event_id)
            assert source.normalized_payload['value']==row.metric_value
            assert source.normalized_payload['synthetic'] is True
            expected=row.metric_value>=measure.target_value if measure.threshold_operator=='gte' else row.metric_value<=measure.target_value
            assert (row.raw_payload['assessment']=='met')==expected
        assert db.query(AuditEvidence).filter_by(entity_type='isms_effectiveness_measure',entity_id=measure.id).count()==1


def test_live_rss_preset_is_idempotent_and_preserves_rules(db, monkeypatch):
    from app.ingest.demo_rss import seed_preset, FEED, RULE
    from app.core.config import settings
    from app.db.models import ManagedConfiguration, ManagedConfigurationRevision
    monkeypatch.setattr(settings, 'demo_mode', True)
    db.add(ManagedConfiguration(name='rules', version=1, document={'rules':[{'id':'existing','when':{'source':'test'},'map_to':['A.5.1']}]}))
    db.flush()
    seed_preset(db)
    assert db.get(ManagedConfiguration,'rss').document == {'feeds':[FEED]}
    assert [r['id'] for r in db.get(ManagedConfiguration,'rules').document['rules']] == ['existing', RULE['id']]
    count=db.query(ManagedConfigurationRevision).count()
    seed_preset(db)
    assert db.query(ManagedConfigurationRevision).count() == count
