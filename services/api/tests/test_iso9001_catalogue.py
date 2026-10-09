"""Edition identity, hierarchy and frozen release seed consistency."""
import gzip
import json
from pathlib import Path
from app.mapping.rules import parse_rules, evaluate_by_framework

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT/'alembic/catalogues/iso-9001.json').read_text())
SEEDS = json.loads(gzip.decompress((ROOT/'alembic/release_seeds.json.gz').read_bytes()))

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



def test_release_seeds_include_both_complete_iso_editions():
    frameworks = {f['slug']:f for f in SEEDS['frameworks']}
    for slug, fw in DATA.items():
        assert all(frameworks[slug][k] == fw[k] for k in ('name','version','description','upstream_url'))
        clauses = {c['ref']:c for c in SEEDS['framework_clauses'] if c['framework_slug']==slug}
        controls = {c['ref']:c for c in SEEDS['control_items'] if c['framework_slug']==slug}
        assert len(clauses) == len(controls) == len(fw['clauses'])
        for item in fw['clauses']:
            ref = item['ref']
            assert controls[ref]['metadata'] == clauses[ref]['metadata'] == item['metadata']
            assert controls[ref]['title'] == clauses[ref]['title'] == item['title']
            parent = item['metadata']['parent_ref']
            assert clauses[ref]['parent_clause_id'] == (clauses[parent]['id'] if parent else None)
    rules = parse_rules({'rules':[{'id':'quality-check','when':{'source':'test'},'map_to':[{'framework':'iso_9001_2015','ref':'10.3'},{'framework':'iso_9001_2026','ref':'10.1'}]}]})
    assert set(evaluate_by_framework({'source':'test'},rules)) == set(DATA)
