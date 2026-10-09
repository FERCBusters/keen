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
    assert len(controls) == 253
    assert len({(c['title'], c['metadata']['description']) for c in controls.values()}) == 253
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
    assert {'IAC-7','IAC-84','RSK-36','RSK-38','RSK-37','RSK-39'} <= controls.keys()


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
