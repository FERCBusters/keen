"""Canonical catalogue, editable nodes, and one-hop evidence crosswalks."""
import importlib.util
import json
from pathlib import Path
from datetime import datetime
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from tests.db_helpers import create_sqlite_schema
from app.db.models import (Framework, ControlItem, OsaControlMapping, CrossFrameworkControlLink,
    EffectiveCrossFrameworkControlLink, Event, Mapping)
from app.services.control_inheritance import effective_control_ids, evidence_pairs
from app.api.routes.frameworks import save_framework_node, FrameworkNodeInput
ROOT = Path(__file__).resolve().parents[1]


def migration():
    spec = importlib.util.spec_from_file_location('osa_migration', ROOT/'alembic/versions/0090_osa_catalogue.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    create_sqlite_schema(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_snapshot_has_all_frameworks_and_api_mapping_counts():
    data = json.loads(migration().DATA.read_text())
    assert len(data['frameworks']) == 88
    assert len({f['id'] for f in data['frameworks']}) == 88
    assert sum(len(f['clauses']) for f in data['frameworks']) == 3608
    assert data['license'] == 'CC-BY-SA-4.0'
    for fw in data['frameworks']:
        assert sum(len(c['controls']) for c in fw['clauses']) == fw['total_mappings']
        assert len({n for c in fw['clauses'] for n in c['controls']}) == fw['total_controls']
        assert len({c['id'] for c in fw['clauses']}) == len(fw['clauses'])
        assert all(len(c['id']) <= 64 for c in fw['clauses'])


def test_canonical_reset_preserves_retained_controls_and_editability(db):
    retained = []
    for slug in ['KEEN-AF:1.0','UK-DVSTF:1.0','CYBER-ESSENTIALS:2026','RISKLEDGER:ASSESSMENT']:
        db.add(Framework(slug=slug, name=slug))
        c=ControlItem(framework_slug=slug, type='custom', ref='retained', title='Edited')
        db.add(c);retained.append(c)
    db.add(Framework(slug='HIPAA:old', name='Old HIPAA'))
    db.add(ControlItem(framework_slug='HIPAA:old', type='custom', ref='old'))
    from app.db.models import ManagedConfiguration
    db.add(ManagedConfiguration(name='organisation-frameworks',version=1,
        document={'enabled':['UK-DVSTF:1.0','CYBER-ESSENTIALS:2026'],'default':'UK-DVSTF:1.0'}))
    db.flush()
    ids=[c.id for c in retained]
    migration().seed_catalogue(db.connection());db.commit();db.expire_all()
    assert db.query(Framework).count()==92
    selection=db.get(ManagedConfiguration,'organisation-frameworks').document
    assert selection['default']=='UK-DVSTF:1.0'
    assert set(selection['enabled'])=={'UK-DVSTF:1.0','CYBER-ESSENTIALS:2026'}
    assert db.query(OsaControlMapping).count()==23140
    assert not db.query(ControlItem).filter_by(framework_slug='HIPAA:old').count()
    assert all(db.get(ControlItem, ident).title=='Edited' for ident in ids)
    control=db.query(ControlItem).filter_by(framework_slug='soc2_tsc',ref='CC6.1').one()
    count=db.query(OsaControlMapping).filter_by(control_id=control.id).count()
    save_framework_node('soc2_tsc','annex_control','CC6.1',FrameworkNodeInput(
        kind='annex_control',ref='CC6.1',title='My edited control',description='Local guidance'),db)
    assert db.get(ControlItem,control.id).title=='My edited control'
    assert db.query(OsaControlMapping).filter_by(control_id=control.id).count()==count
    save_framework_node('soc2_tsc','custom','LOCAL/1',FrameworkNodeInput(
        kind='custom',ref='LOCAL/1',title='Additional local control'),db)
    assert db.query(ControlItem).filter_by(framework_slug='soc2_tsc',ref='LOCAL/1').one()


def test_shared_nist_dedup_explicit_precedence_and_no_transitive_evidence(db):
    a,b,c=[ControlItem(framework_slug=s,type='annex_control',ref='1') for s in ['A','B','C']]
    db.add_all([a,b,c]);db.flush()
    db.add_all([OsaControlMapping(control_id=a.id,nist_ref='AC-01'),
        OsaControlMapping(control_id=a.id,nist_ref='AC-02'),
        OsaControlMapping(control_id=b.id,nist_ref='AC-01'),
        OsaControlMapping(control_id=b.id,nist_ref='AC-02'),
        OsaControlMapping(control_id=b.id,nist_ref='SC-07'),
        OsaControlMapping(control_id=c.id,nist_ref='SC-07')])
    event=Event(source='test',external_id='test',timestamp=datetime.utcnow(),summary='Test')
    db.add(event);db.flush()
    db.add(Mapping(event_id=event.id,control_item_id=a.id,confidence=1,method='test',mapped_by='test'))
    db.flush()
    assert db.query(EffectiveCrossFrameworkControlLink).filter_by(source_control_id=a.id,target_control_id=b.id).count()==1
    assert set(db.scalars(effective_control_ids(b.id)))=={a.id,b.id,c.id}
    assert db.execute(select(evidence_pairs('B'))).all()==[(b.id,event.id)]
    assert db.execute(select(evidence_pairs('C'))).all()==[]
    db.add(CrossFrameworkControlLink(source_control_id=a.id,target_control_id=b.id,rationale='Reviewed link'))
    db.flush()
    link=db.query(EffectiveCrossFrameworkControlLink).filter_by(source_control_id=a.id,target_control_id=b.id).one()
    assert link.rationale=='Reviewed link' and not link.derived


def test_only_enabled_frameworks_contribute_inherited_evidence(db, monkeypatch):
    from app.db.models import ManagedConfiguration
    from app.core.config import settings
    from app.services.control_inheritance import effective_framework_ids, event_has_control
    monkeypatch.setattr(settings, 'enabled_frameworks', '')
    for slug in ['A','B']:
        db.add(Framework(slug=slug))
    a=ControlItem(framework_slug='A',type='custom',ref='1')
    b=ControlItem(framework_slug='B',type='custom',ref='1')
    db.add_all([a,b]);db.flush()
    db.add_all([OsaControlMapping(control_id=a.id,nist_ref='AC-01'),
                OsaControlMapping(control_id=b.id,nist_ref='AC-01')])
    event=Event(source='test',external_id='enabled',timestamp=datetime.utcnow(),summary='Enabled inheritance')
    db.add(event);db.flush()
    db.add(Mapping(event_id=event.id,control_item_id=a.id,method='test'))
    config=ManagedConfiguration(name='organisation-frameworks',version=1,
                                document={'enabled':['A','B'],'default':'B'})
    db.add(config);db.commit()
    assert db.execute(select(evidence_pairs('B',db))).all()==[(b.id,event.id)]
    config.document={'enabled':['B'],'default':'B'};db.commit()
    assert db.execute(select(evidence_pairs('B',db))).all()==[]
    assert a.id not in set(db.scalars(effective_control_ids(b.id,db)))
    assert a.id not in set(db.scalars(effective_framework_ids('B',db)))
    assert db.query(Event).filter(event_has_control(b.id,Event.id,db)).count()==0
    # Direct mappings remain; hiding a framework is not evidence deletion.
    assert db.query(Mapping).count()==1
