"""The release snapshot contains attributed, consolidated KEEN controls."""
import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]/'alembic'
SEEDS = json.loads(gzip.decompress((ROOT/'release_seeds.json.gz').read_bytes()))
PAIRS = json.loads((ROOT/'catalogues/keen-af-consolidation.json').read_text())
SLUG = 'KEEN-AF:1.0'


def test_exact_pairs_are_consolidated_in_release_seed():
    controls = {c['ref']:c for c in SEEDS['control_items'] if c['framework_slug']==SLUG}
    assert len(controls) == 247
    assert len({(c['title'], c['metadata']['description']) for c in controls.values()}) == 247
    removed_ids = set()
    for pair in PAIRS:
        assert pair['remove'] not in controls
        keep = controls[pair['keep']]
        assert (keep['title'], keep['metadata']['description']) == (pair['title'],pair['description'])
        assert keep['metadata']['consolidated_sources'] == pair['sources']
        original = keep['metadata']['consolidated_controls'][0]
        assert original['ref'] == pair['remove']
        removed_ids.add(original['id'])
    for table, rows in SEEDS.items():
        if table != 'control_items':
            assert not any(ident in json.dumps(rows) for ident in removed_ids), table
    assert {'IAC-7', 'RSK-36', 'RSK-37', 'BCD-2', 'CFG-1', 'CHG-1'} <= controls.keys()


def test_release_attribution_and_risk_references():
    controls = {c['ref']:c for c in SEEDS['control_items'] if c['framework_slug']==SLUG}
    assert all(c['metadata']['license']=='Apache-2.0' for c in controls.values())
    fw = next(f for f in SEEDS['frameworks'] if f['slug']==SLUG)
    assert 'Vanta' in fw['description'] and 'Apache' in fw['description']
    assert fw['upstream_url']=='https://github.com/VantaInc/vanta-control-set'
    import re
    for risk in SEEDS['risk_library_entries']:
        assessment = risk['suggested_assessment']
        refs = set(assessment.get('keen_af_control_refs', []))
        refs.update(re.findall(r'\b[A-Z]+-\d+\b',assessment.get('auditor_control_refs','')))
        assert refs <= controls.keys()


def test_semantic_consolidation_preserves_requirements_and_provenance():
    pairs = json.loads((ROOT/'catalogues/keen-af-semantic-consolidation.json').read_text())
    assert {(p['remove'], p['keep']) for p in pairs} == {
        ('IAC-84', 'IAC-7'), ('RSK-38', 'RSK-36'), ('RSK-39', 'RSK-37'),
        ('BCD-23', 'BCD-2'), ('CFG-44', 'CFG-1'), ('CHG-39', 'CHG-1'),
    }
    controls = {c['ref']: c for c in SEEDS['control_items'] if c['framework_slug'] == SLUG}
    for pair in pairs:
        assert pair['remove'] not in controls
        keep = controls[pair['keep']]
        assert keep['id'] == pair['keep_id']
        assert keep['metadata']['description'] == pair['description']
        archived = keep['metadata']['consolidated_controls']
        assert any(c['id'] == pair['remove_id'] and c['ref'] == pair['remove'] for c in archived)
        assert keep['metadata']['pre_consolidation_control']['id'] == keep['id']
        # Retired IDs may appear in provenance, never in active linked records.
        for table, rows in SEEDS.items():
            if table != 'control_items':
                assert pair['remove_id'] not in json.dumps(rows), table
    assert 'at least quarterly' in controls['IAC-7']['metadata']['description']
    assert 'at least annually' in controls['BCD-2']['metadata']['description']
    assert 'planned intervals' in controls['RSK-36']['metadata']['description']
    assert 'significant changes' in controls['RSK-36']['metadata']['description']
    assert 'documented results' in controls['RSK-36']['metadata']['description']
    assert 'documented results' in controls['RSK-37']['metadata']['description']
    assert 'monitored and reviewed' in controls['CFG-1']['metadata']['description']


def test_consolidated_seed_links_are_unique_and_resolve():
    ids = {c['id'] for c in SEEDS['control_items']}
    links = [(r['source_control_id'], r['target_control_id']) for r in SEEDS['cross_framework_control_links']]
    assert len(links) == len(set(links))
    assert all(source != target and source in ids and target in ids for source, target in links)
    osa = [(r['control_id'], r['nist_ref']) for r in SEEDS['osa_control_mappings']]
    assert len(osa) == len(set(osa))
    assert all(control in ids for control, _ in osa)
    for risk in SEEDS['risk_library_entries']:
        refs = risk['suggested_assessment'].get('keen_af_control_refs', [])
        assert len(refs) == len(set(refs))
