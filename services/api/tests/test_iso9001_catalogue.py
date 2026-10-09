"""Edition identity, hierarchy, usable mappings and non-destructive migration."""
import importlib.util
import json
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from tests.db_helpers import create_sqlite_schema
from app.db.models import Framework, FrameworkClause, ControlItem, ManagedConfiguration
from app.api.routes.frameworks import save_framework_node, FrameworkNodeInput
from app.mapping.rules import parse_rules, evaluate_by_framework

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('iso9001_migration', ROOT/'alembic/versions/0003_iso_9001.py')
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)
DATA = json.loads((ROOT/'alembic/catalogues/iso-9001.json').read_text())

@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    create_sqlite_schema(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_edition_specific_references_and_amendment():
    old = {c['ref']:c for c in DATA['iso_9001_2015']['clauses']}
    new = {c['ref']:c for c in DATA['iso_9001_2026']['clauses']}
    assert len(old) == 37 and len(new) == 39
    assert '10.3' in old and '10.3' not in new
    assert old['10.1']['title'] == 'General'
    assert new['10.1']['title'] == 'Continual improvement'
    assert '6.1.3' in new and '6.1.3' not in old
    assert 'opportunities' in new['6.1.3']['title']
    assert 'ethical' in new['5.1']['metadata']['description']
    assert 'ethical' in new['7.3']['metadata']['description']
    assert '2024' in old['4.1']['metadata']['description']
    assert 'climate' in old['4.2']['metadata']['description']
    assert 'documented' not in old['5.3']['metadata']['description']
    for nodes in (old,new):
        assert set(map(str,range(4,11))) <= nodes.keys()
        assert {'7.1','7.2','7.3','7.4','7.5','8.3','8.4','8.5','8.6','8.7','9.1','9.2','9.3'} <= nodes.keys()
        for node in nodes.values():
            parent = node['metadata']['parent_ref']
            assert parent is None or parent in nodes
            assert node['metadata']['description']


def test_upgrade_keeps_selection_and_builds_both_clause_representations(db):
    db.add(Framework(slug='local', name='My framework'))
    db.add(ManagedConfiguration(name='organisation-frameworks', version=1, document={'enabled':['local'],'default':'local'}))
    db.flush()
    migration.seed_catalogues(db.connection()); db.commit()
    assert db.query(Framework).count() == 3
    assert db.get(ManagedConfiguration,'organisation-frameworks').document == {'enabled':['local'],'default':'local'}
    for slug,fw in DATA.items():
        clauses = {c.ref:c for c in db.query(FrameworkClause).filter_by(framework_slug=slug)}
        controls = {c.ref:c for c in db.query(ControlItem).filter_by(framework_slug=slug)}
        assert len(clauses) == len(controls) == len(fw['clauses'])
        for ref,node in controls.items():
            assert node.type == 'clause'
            assert node.meta == clauses[ref].meta
            parent = node.meta['parent_ref']
            assert clauses[ref].parent_clause_id == (clauses[parent].id if parent else None)
    rules = parse_rules({'rules':[{'id':'quality-check','when':{'source':'test'},'map_to':[{'framework':'iso_9001_2015','ref':'10.3'},{'framework':'iso_9001_2026','ref':'10.1'}]}]})
    hits = evaluate_by_framework({'source':'test'},rules)
    assert set(hits) == {'iso_9001_2015','iso_9001_2026'}


def test_seed_repeat_preserves_administrator_edits_and_ids(db):
    migration.seed_catalogues(db.connection()); db.commit()
    slug='iso_9001_2026'
    framework = db.query(Framework).filter_by(slug=slug).one()
    framework.name='Local quality programme'
    node=db.query(ControlItem).filter_by(framework_slug=slug,ref='6.1.3').one()
    ident=node.id
    save_framework_node(slug,'clause','6.1.3',FrameworkNodeInput(kind='clause',ref='6.1.3',title='Our opportunities',description='Local guidance',parent_ref='6.1'),db)
    db.commit()
    migration.seed_catalogues(db.connection()); db.commit(); db.expire_all()
    assert db.query(Framework).filter_by(slug=slug).one().name=='Local quality programme'
    assert db.get(ControlItem,ident).title=='Our opportunities'
    assert db.query(FrameworkClause).filter_by(framework_slug=slug,ref='6.1.3').one().meta['description']=='Local guidance'
    assert db.query(ControlItem).count()==76
    assert db.query(FrameworkClause).count()==76


def test_downgrade_requires_restore():
    with pytest.raises(RuntimeError,match='Restore a backup'):
        migration.downgrade()
