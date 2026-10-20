import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';

const d3 = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');
const plain = (v) => JSON.parse(JSON.stringify(v));

const categories = [
  {
    id: 'cat1',
    name: 'Information',
    subcategories: [
      { id: 'sub1', name: 'Records' },
      { id: 'sub2', name: 'Databases' },
    ],
  },
  { id: 'cat2', name: 'Data', subcategories: [{ id: 'sub3', name: 'PII' }] },
  { id: 'cat3', name: 'Empty', subcategories: [] },
];
const assets = [
  {
    id: 'as1',
    name: 'Payroll DB',
    category: { id: 'cat1', name: 'Information' },
    subcategory: { id: 'sub2', name: 'Databases' },
  },
  {
    id: 'as2',
    name: 'Payroll files',
    category: { id: 'cat1', name: 'Information' },
    subcategory: { id: 'sub1', name: 'Records' },
  },
];
const riskRows = [
  {
    id: 'r1',
    asset: 'Payroll DB',
    threat_summary: 'Ransomware',
    risk_types: ['Availability'],
    risk_score: 9,
    residual_risk_score: 4,
    control_count: 1,
    category: { name: 'Information' },
    subcategory: { name: 'Databases' },
  },
  {
    id: 'r2',
    asset: '<img src=x>Laptop',
    threat_summary: '',
    risk_types: [],
    risk_score: 16,
    residual_risk_score: 0,
    control_count: 0,
  },
];
const fullRisk = {
  id: 'r1',
  asset: 'Payroll DB',
  asset_entity: assets[0],
  threat_summary: 'Ransomware',
  risk_types: ['Availability', 'Integrity'],
  risk_owner: { id: 'role9', type: 'role', name: 'CISO' },
  register_likelihood: 3,
  register_impact: 3,
  register_residual_likelihood: null,
  register_residual_impact: 7,
  note: 'Backups tested',
  controls: [
    { id: 'c1', ref: 'A1' },
    { id: 'c9', ref: 'Z9', title: 'Other framework' },
  ],
};
const graph = {
  nodes: [
    {
      id: 'risk:r1',
      node_kind: 'risk',
      risk_id: 'r1',
      label:
        'Payroll DB — Ransomware with a very long descriptive label that wraps across several lines because operators like to write complete sentences into risk titles',
      category: 'Information',
      subcategory: 'Databases',
      risk_types: ['Availability'],
      risk_score: 9,
      residual_risk_score: 4,
      link_count: 2,
      color: '#f00',
    },
    {
      id: 'risk:r2',
      node_kind: 'risk',
      risk_id: 'r2',
      label: 'Laptop',
      risk_score: 16,
      residual_risk_score: 0,
      link_count: 1,
    },
    { id: 'risk:r3', node_kind: 'risk', risk_id: 'r3', label: 'Unlinked' },
    {
      id: 'control:c1',
      node_kind: 'control',
      control_id: 'c1',
      ref: 'A1',
      title: 'Backups',
      link_count: 2,
    },
    {
      id: 'control:c2',
      node_kind: 'control',
      control_id: 'c2',
      ref: 'A2',
      title: 'Encryption',
      link_count: 1,
    },
  ],
  links: [
    { source: 'risk:r1', target: 'control:c1', risk_score: 9, residual_risk_score: 4 },
    { source: 'risk:r1', target: 'control:c2', risk_score: 9, residual_risk_score: 4 },
    { source: 'risk:r2', target: 'control:c1', risk_score: 16, residual_risk_score: 0 },
  ],
};

async function risksPage(t, options = {}) {
  const state = { posted: [], patched: [], removed: [], riskQueries: [] };
  const ui = await page(t, 'risks', {
    htmlName: options.htmlName || 'risks',
    query: options.query || '',
    scripts: [d3],
    me: options.me || { is_admin: true },
    frameworks: options.frameworks,
    globals: options.globals,
    setup(window) {
      window.d3 = {};
    },
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/risks/categories') return { items: categories };
      if (p === '/api/v1/risks/assets') return { items: assets };
      if (p === '/api/v1/risks/users')
        return { items: [{ id: 'u1', username: 'dana' }], roles: [{ id: 'role9', name: 'CISO' }] };
      if (p === '/api/v1/controls')
        return {
          items: [
            { id: 'c2', ref: 'A10', title: 'Later' },
            { id: 'c1', ref: 'A2', title: 'Backups' },
            { id: 'k1', ref: '4', type: 'clause' },
          ],
        };
      if (p === '/api/v1/risks') {
        state.riskQueries.push(Object.fromEntries(u.searchParams));
        return { items: riskRows, total: 2, framework: 'A' };
      }
      if (p === '/api/v1/risks/r1') {
        if (options.openFail) throw new Error('Risk gone');
        return structuredClone(options.risk || fullRisk);
      }
      if (p === '/api/v1/risks/controls-without-risks') {
        if (options.uncoveredFail) throw new Error('Uncovered offline');
        return {
          items: u.searchParams.get('q') ? [] : [{ id: 'c3', ref: 'A3', title: '<b>Gap</b>' }],
          total: u.searchParams.get('q') ? 0 : 1,
          framework: 'A',
        };
      }
      if (p === '/api/v1/graph/risk_control') return options.graph ?? graph;
      if (p === '/api/v1/risks/library')
        return {
          items: [
            {
              id: 'tpl1',
              risk_types: ['Confidentiality', 'Integrity'],
              threat_summary: 'Phishing leads to credential theft',
              treatment_guidance: 'Train staff',
              suggested_assessment: {
                asset: 'Laptops',
                inherent_likelihood: 4,
                inherent_impact: 3,
                residual_likelihood: 9,
                residual_impact: 2,
                keen_af_control_refs: ['AC-1', 'AC-2'],
              },
            },
            { id: 'tpl2', risk_types: [], suggested_assessment: { asset: 'Payroll DB' } },
          ],
        };
      if (p === '/api/v1/audits') return { items: [{ id: 'au1', title: 'Audit', status: 'open' }] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (options.postFail?.(u.pathname)) throw new Error('Rejected ' + u.pathname);
      if (u.pathname === '/api/v1/risks/mitigator/analyse') return options.analysis;
      if (u.pathname === '/api/v1/interested-parties') return { id: 'ip' + state.posted.length };
      return { id: 'new1' };
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      return { ok: true };
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      return { ok: true };
    },
  });
  await settle(
    () =>
      ui.el('resultMeta')?.textContent ||
      ui.el('status').textContent ||
      options.htmlName === 'mitigator',
  );
  await new Promise((r) => setTimeout(r, 20));
  return { ui, state };
}

test('risk filters send selected categories, subcategories and CIA types as CSV parameters', async (t) => {
  const { ui, state } = await risksPage(t);
  assert.equal(ui.el('rows').querySelector('img'), null);
  assert.match(ui.el('rows').textContent, /No threat summary/);
  assert.match(ui.el('resultMeta').textContent, /2 risk scenarios; control mappings shown for A/);
  const pick = (value) =>
    ui.document.querySelector(`.keen-filter-pill-grid [data-value="${value}"]`).click();
  pick('cat1');
  await settle(() => state.riskQueries.some((q) => q.category_id === 'cat1'));
  pick('sub2');
  await settle(() => state.riskQueries.some((q) => q.subcategory_id === 'sub2'));
  pick('Integrity');
  pick('Availability');
  await settle(() =>
    state.riskQueries.some(
      (q) => q.risk_type === 'Integrity,Availability' || q.risk_type === 'Availability,Integrity',
    ),
  );
  ui.el('q').value = '  payroll ';
  ui.el('q').dispatchEvent(new ui.window.Event('input'));
  await settle(() => state.riskQueries.at(-1).q === 'payroll');
});

test('editing a risk reuses its asset, role owner and out-of-framework controls', async (t) => {
  const { ui, state } = await risksPage(t);
  ui.document.querySelector('[data-edit-risk="r1"]').click();
  await settle(() => ui.el('riskId').value === 'r1');
  assert.equal(ui.el('deleteRisk').style.display, '');
  assert.equal(ui.el('editorTitle').textContent, 'Edit risk scenario: Payroll DB — Ransomware');
  assert.equal(ui.el('assetId').value, 'as1');
  assert.equal(ui.el('category').disabled, true);
  assert.match(
    ui.el('selectedAssetSummary').textContent,
    /Using existing asset: Payroll DB — Information \/ Databases/,
  );
  assert.equal(ui.el('owner').value, 'role:role9');
  // Scores are clamped into the 1–5 scale; missing values default to 1.
  assert.equal(ui.el('residualLikelihood').value, '1');
  assert.equal(ui.el('residualImpact').value, '5');
  assert.equal(ui.el('riskScorePreview').textContent, '9');
  assert.equal(ui.el('residualScorePreview').textContent, '5');
  // Controls from another framework stay selected so saving does not drop them.
  assert.deepEqual([...ui.el('controls').selectedOptions].map((o) => o.value).sort(), ['c1', 'c9']);
  assert.deepEqual(
    [...ui.el('controls').options].map((o) => o.value),
    ['c1', 'c2', 'c9'],
  );
  assert.match(ui.window.location.search, /edit=r1/);
  ui.el('inherentImpact').value = '8';
  ui.el('inherentImpact').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('inherentImpact').value, '5');
  assert.equal(ui.el('riskScorePreview').textContent, '15');
  assert.equal(ui.el('riskScorePreview').className, 'score-pill score-high');
  ui.el('riskForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/risks/r1',
    body: {
      risk_types: ['Integrity', 'Availability'],
      risk_owner_user_id: null,
      risk_owner_role_id: 'role9',
      threat_summary: 'Ransomware',
      register_likelihood: 3,
      register_impact: 5,
      register_residual_likelihood: 1,
      register_residual_impact: 5,
      note: 'Backups tested',
      framework: 'A',
      controls: ['c1', 'c9'],
      asset_id: 'as1',
    },
  });
  await settle(() => ui.el('editorCard').style.display === 'none');
  assert.doesNotMatch(ui.window.location.search, /edit=/);
});

test('new risks validate asset details and can create a new asset or reuse a matching one', async (t) => {
  const { ui, state } = await risksPage(t);
  ui.el('newRisk').click();
  assert.equal(ui.el('editorTitle').textContent, 'New risk scenario');
  assert.match(ui.el('selectedAssetSummary').textContent, /Type an asset name/);
  for (const cb of ui.document.querySelectorAll('.risk-type')) cb.checked = false;
  ui.el('riskForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Select at least one risk type/.test(ui.el('status').textContent));
  ui.el('rtC').checked = true;
  ui.el('riskForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Enter an asset name/.test(ui.el('status').textContent));
  // Typing a partial name offers matching assets; choosing one reuses it.
  ui.el('asset').value = 'payroll';
  ui.el('asset').dispatchEvent(new ui.window.Event('input'));
  await settle(() => ui.document.querySelectorAll('[data-use-asset]').length === 2);
  ui.document.querySelector('[data-use-asset="as2"]').click();
  assert.equal(ui.el('assetId').value, 'as2');
  assert.equal(ui.el('subcategory').value, 'sub1');
  ui.document.querySelector('[data-clear-asset]').click();
  assert.equal(ui.el('assetId').value, '');
  assert.equal(ui.el('asset').value, '');
  // A new name with an empty category is rejected until a subcategory exists.
  ui.el('asset').value = 'Badge printer';
  ui.el('asset').dispatchEvent(new ui.window.Event('change'));
  assert.match(ui.el('selectedAssetSummary').textContent, /No existing asset matches/);
  ui.el('category').value = 'cat3';
  ui.el('category').dispatchEvent(new ui.window.Event('change'));
  ui.el('riskForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Select an asset subcategory/.test(ui.el('status').textContent));
  ui.el('category').value = 'cat2';
  ui.el('category').dispatchEvent(new ui.window.Event('change'));
  ui.el('owner').value = 'u1';
  ui.el('threatSummary').value = 'Tampering';
  ui.el('riskForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].path, '/api/v1/risks');
  assert.equal(state.posted[0].body.asset_name, 'Badge printer');
  assert.equal(state.posted[0].body.category_id, 'cat2');
  assert.equal(state.posted[0].body.subcategory_id, 'sub3');
  assert.equal(state.posted[0].body.risk_owner_user_id, 'u1');
  assert.equal(state.posted[0].body.risk_owner_role_id, null);
  assert.equal('asset_id' in state.posted[0].body, false);
  await settle(() => ui.el('status').textContent === 'Risk scenario saved');
});

test('typing an exact existing asset name selects that asset automatically', async (t) => {
  const { ui, state } = await risksPage(t);
  ui.el('newRisk').click();
  ui.el('asset').value = 'PAYROLL DB';
  ui.el('riskForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].body.asset_id, 'as1');
});

test('risks are deleted after confirmation and sampled into audits', async (t) => {
  const prompts = [];
  const { ui, state } = await risksPage(t, {
    globals: { confirm: (m) => (prompts.push(m), true) },
  });
  ui.document.querySelector('[data-edit-risk="r1"]').click();
  await settle(() => ui.el('riskId').value === 'r1');
  ui.el('deleteRisk').click();
  await settle(() => ui.el('status').textContent === 'Risk scenario deleted');
  assert.deepEqual(state.removed, ['/api/v1/risks/r1']);
  assert.equal(prompts[0], 'Delete risk scenario “Payroll DB — Ransomware”?');
  ui.document.querySelector('[data-sample-risk="r2"]').click();
  await settle(() => ui.document.getElementById('entityAuditSampleSelect')?.value === 'au1');
  ui.document.getElementById('entityAuditSampleSubmit').click();
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].body.title, '<img src=x>Laptop');
});

test('read-only users view risks without editing or sampling', async (t) => {
  const { ui, state } = await risksPage(t, { me: { can_view_risks: true }, query: '&edit=r1' });
  await settle(() => ui.el('riskId').value === 'r1');
  assert.match(ui.el('editorTitle').textContent, /^View risk scenario/);
  assert.equal(ui.el('saveRisk').style.display, 'none');
  assert.equal(ui.el('deleteRisk').style.display, 'none');
  assert.equal(ui.el('threatSummary').disabled, true);
  assert.equal(ui.el('asset').disabled, true);
  assert.equal(ui.el('newRisk').style.display, 'none');
  assert.equal(ui.el('newRiskScenarioTab').textContent, 'Risk scenario');
  assert.equal(ui.document.querySelector('[data-edit-risk]'), null);
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/risks/users')));
  ui.el('riskForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  ui.el('deleteRisk').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(state.patched.length, 0);
  assert.equal(state.removed.length, 0);
});

test('users without risk access see a permission message and nothing loads', async (t) => {
  const { ui } = await risksPage(t, { me: {} });
  assert.match(
    ui.el('status').textContent,
    /You need risk.read permission to view the risk register/,
  );
  assert.match(ui.el('rows').textContent, /risk.read permission required/);
  assert.equal(ui.el('riskVisualisationsTabItem').style.display, 'none');
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/risks')));
});

test('controls without risks load on their tab and can be searched', async (t) => {
  const { ui } = await risksPage(t, { query: '&tab=controls-without-risks' });
  await settle(() => ui.el('controlsWithoutRisksRows').querySelector('a'));
  assert.equal(ui.el('controlsWithoutRisksRows').querySelector('b'), null);
  assert.equal(
    ui.el('controlsWithoutRisksMeta').textContent,
    '1 control without associated risk scenarios for A.',
  );
  ui.el('controlsWithoutRisksQ').value = 'gap';
  ui.el('controlsWithoutRisksQ').dispatchEvent(new ui.window.Event('input'));
  await settle(() =>
    /All controls currently have at least one/.test(ui.el('controlsWithoutRisksRows').textContent),
  );
  ui.el('controlsWithoutRisksTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.match(ui.window.location.search, /tab=controls-without-risks/);
  const failed = await risksPage(t, { uncoveredFail: true });
  failed.ui.el('controlsWithoutRisksTab').dispatchEvent(new failed.ui.window.Event('shown.bs.tab'));
  await settle(() =>
    /Failed to load controls without risks/.test(failed.ui.el('status').textContent),
  );
});

test('risk visualisations draw the risk/control graph and a filterable heatmap', async (t) => {
  const { ui } = await risksPage(t);
  ui.el('riskVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('riskControlsGraph').querySelector('svg'));
  assert.match(ui.window.location.search, /tab=visualisations/);
  const svg = ui.el('riskControlsGraph').querySelector('svg');
  // Unlinked risks are omitted; risks with more links come first.
  assert.equal(svg.querySelectorAll('.interested-party-bipartite-node').length, 4);
  assert.match(svg.textContent, /2 risk\(s\), 2 control\(s\), 3 mapping\(s\)/);
  // Node labels are shortened, but each node keeps its full label as a tooltip.
  assert.ok(
    [...svg.querySelectorAll('.interested-party-bipartite-node > title')].some((x) =>
      x.textContent.includes('complete sentences into risk titles'),
    ),
  );
  assert.match(
    ui.el('riskControlsGraphMeta').textContent,
    /^2 of 3 risks, 2 of 2 controls, 3 visible mappings for A\. Link thickness follows residual risk score\./,
  );
  const cells = ui.el('riskControlsHeatmap').querySelectorAll('td.risk-control-heatmap-cell');
  assert.equal(cells.length, 3);
  assert.deepEqual(
    [...cells].map((c) => c.textContent),
    ['4', '4', '0'],
  );
  ui.el('riskVizScoreInherent').checked = true;
  ui.el('riskVizScoreInherent').dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(
    [...ui.el('riskControlsHeatmap').querySelectorAll('td.risk-control-heatmap-cell')].map(
      (c) => c.textContent,
    ),
    ['9', '9', '16'],
  );
  assert.match(
    ui.el('riskControlsHeatmapMeta').textContent,
    /Darker cells have higher inherent risk score/,
  );
  const filter = ui.el('riskControlsHeatmap').querySelector('[data-risk-control-matrix-filter]');
  filter.value = 'encryption';
  filter.dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('riskControlsHeatmap').querySelectorAll('th.matrix-control-header').length, 1);
  filter.value = 'laptop';
  filter.dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('riskControlsHeatmap').querySelectorAll('tbody tr').length, 1);
  filter.value = 'zzz';
  filter.dispatchEvent(new ui.window.Event('input'));
  assert.match(ui.el('riskControlsHeatmap').textContent, /No risks or controls match that filter/);
  // Limits are clamped and zoom controls respond.
  ui.el('riskVizTopRisks').value = '1';
  ui.el('riskVizTopRisks').dispatchEvent(new ui.window.Event('change'));
  await settle(() => /^2 of 3 risks/.test(ui.el('riskControlsGraphMeta').textContent));
  ui.el('riskControlsGraph').querySelector('[data-zoom="in"]').click();
  ui.el('riskControlsGraph').querySelector('[data-zoom="reset"]').click();
  ui.el('reloadRiskVisualisations').click();
  await settle(
    () => ui.calls.filter((c) => c.url.startsWith('/api/v1/graph/risk_control')).length === 2,
  );
});

test('risk graph labels mark every truncation with an ellipsis', async (t) => {
  const label =
    'Customer payroll database for the finance department hosted in the primary region with nightly backups verified-by-the-operations-team-every-quarter-end';
  const graph = {
    nodes: [
      {
        id: 'risk:r1',
        node_kind: 'risk',
        risk_id: 'r1',
        label,
        risk_score: 9,
        residual_risk_score: 4,
        link_count: 1,
      },
      {
        id: 'control:c1',
        node_kind: 'control',
        control_id: 'c1',
        ref: 'A1',
        title: 'Backups',
        link_count: 1,
      },
    ],
    links: [{ source: 'risk:r1', target: 'control:c1', risk_score: 9, residual_risk_score: 4 }],
  };
  const { ui } = await risksPage(t, { query: '&tab=visualisations', graph });
  await settle(() => ui.el('riskControlsGraph').querySelector('svg'));
  const node = ui.el('riskControlsGraph').querySelector('.interested-party-bipartite-node');
  const lines = [...node.querySelector('text').querySelectorAll('tspan')].map((x) => x.textContent);
  assert.equal(lines.length, 3);
  assert.ok(lines.at(-1).endsWith('…'), lines.at(-1));
  // The full label stays available as the node's tooltip.
  assert.ok(node.querySelector(':scope > title').textContent.startsWith(label));
});

test('risk visualisations explain when no mappings exist', async (t) => {
  const { ui } = await risksPage(t, {
    query: '&tab=visualisations',
    graph: { nodes: [], links: [] },
  });
  await settle(() => /No risk\/control mappings/.test(ui.el('riskControlsHeatmap').textContent));
  assert.match(ui.el('riskControlsGraph').textContent, /No risk\/control mappings/);
});

test('library templates prefill a new risk with example ratings and KEEN-AF control suggestions', async (t) => {
  const { ui, state } = await risksPage(t, {
    query: '&template=tpl1',
    frameworks: [{ slug: 'A' }, { slug: 'KEEN-AF:1.0' }],
  });
  await settle(() => ui.el('threatSummary').value);
  assert.equal(ui.el('threatSummary').value, 'Phishing leads to credential theft');
  assert.equal(ui.el('note').value, 'Train staff');
  assert.deepEqual(
    [...ui.document.querySelectorAll('.risk-type')].filter((c) => c.checked).map((c) => c.value),
    ['Confidentiality', 'Integrity'],
  );
  assert.equal(ui.el('inherentLikelihood').value, '4');
  // Out-of-range suggestions are ignored.
  assert.equal(ui.el('residualLikelihood').value, '1');
  assert.equal(ui.el('asset').value, 'Devices');
  assert.equal(ui.el('templateControlsCard').hidden, false);
  assert.match(
    ui.el('templateControlsList').textContent,
    /^AC-1, AC-2\. These links are saved under KEEN-AF:1.0/,
  );
  assert.match(ui.el('status').textContent, /Template loaded with example ratings/);
  ui.el('category').value = 'cat2';
  ui.el('category').dispatchEvent(new ui.window.Event('change'));
  ui.el('riskForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].body.library_template_id, 'tpl1');
  // Unticking the control suggestion leaves the template unlinked.
  const second = await risksPage(t, {
    query: '&template=tpl1',
    frameworks: [{ slug: 'A' }, { slug: 'KEEN-AF:1.0' }],
  });
  await settle(() => second.ui.el('threatSummary').value);
  second.ui.el('templateIncludeControls').checked = false;
  second.ui.el('category').value = 'cat2';
  second.ui.el('category').dispatchEvent(new second.ui.window.Event('change'));
  second.ui
    .el('riskForm')
    .dispatchEvent(new second.ui.window.Event('submit', { cancelable: true }));
  await settle(() => second.state.posted.length === 1);
  assert.equal('library_template_id' in second.state.posted[0].body, false);
});

test('templates without KEEN-AF hide control suggestions and match existing assets', async (t) => {
  const { ui } = await risksPage(t, { query: '&template=tpl2' });
  await settle(() => /Template loaded/.test(ui.el('status').textContent));
  assert.equal(ui.el('templateControlsCard').hidden, true);
  assert.equal(ui.el('templateIncludeControls').disabled, true);
  assert.equal(ui.el('assetId').value, 'as1');
});

test('deep links open the new-risk editor and report risks that cannot be opened', async (t) => {
  const fresh = await risksPage(t, { query: '&tab=new' });
  assert.equal(fresh.ui.el('editorCard').style.display, '');
  assert.equal(fresh.ui.el('editorTitle').textContent, 'New risk scenario');
  const failed = await risksPage(t, { query: '&edit=r1', openFail: true });
  await settle(() =>
    /Failed to open risk: Error: Risk gone/.test(failed.ui.el('status').textContent),
  );
});

function analysis(overrides = {}) {
  return {
    framework: 'KEEN-AF:1.0',
    signals: [{ label: 'Personal data', reason: 'matched <b>gdpr</b>' }],
    existing_matches: {
      cia_risks: [
        {
          id: 'r1',
          asset_name: '<img src=x>Payroll',
          risk_types: ['Confidentiality'],
          threat_summary: 'Leak',
        },
      ],
      pestle_items: [
        { id: 'p1', item: 'Privacy law', type: 'Legal', clauses: [{ clause: { ref: '4.2' } }] },
      ],
      interested_parties: [{ id: 'ip1', label: 'Regulator' }],
    },
    library_suggestions: [
      {
        id: 'tpl1',
        name: 'Phishing',
        threat_summary: 'Credential theft',
        reason: 'Similar wording',
        matched_terms: ['phish'],
        control_refs: ['AC-1'],
        suggested_assessment: { inherent_likelihood: 4, inherent_impact: 3 },
      },
    ],
    control_suggestions: Array.from({ length: 9 }, (_, i) => ({
      control: { id: `c${i}`, ref: `AC-${i}`, title: `Control ${i}`, in_scope: i !== 0 },
      confidence: ['strong', 'medium', 'weak'][i % 3],
      score: 10 - i,
      matched_terms: ['data'],
      reasons: ['Mentions <i>data</i>'],
    })),
    pestle_hits: [{ type: 'Legal' }],
    pestle_suggestions: [
      {
        id: 'p1',
        item: 'Privacy law',
        type: 'Legal',
        lens: 'External',
        confidence: 'strong',
        score: 7,
        matched_terms: ['privacy'],
        clauses: [{ clause: { ref: '4.2' } }],
      },
    ],
    draft_pestle_item: {
      type: 'Legal',
      lens: 'External',
      item: 'New privacy duty',
      business_processes: [{ id: 'bp1', name: 'Payroll' }, { name: 'No id' }],
    },
    interested_party_suggestions: [
      {
        name: 'Data subjects',
        nature: 'Rights',
        reason: 'Personal data',
        draft: { name_id: 'n1' },
      },
      { name: 'Regulator', nature: 'Legal', duplicate_exact: true, draft: { name_id: 'n2' } },
    ],
    draft_risk: {
      risk_types: ['Confidentiality'],
      threat_summary: 'Payroll leak',
      mitigator_context: { source: 'keen_mitigator' },
    },
    ...overrides,
  };
}

test('Keen Mitigator validates input, sends the questionnaire and renders suggestions safely', async (t) => {
  const { ui, state } = await risksPage(t, { htmlName: 'mitigator', analysis: analysis() });
  // The "Data" category is preferred when present.
  assert.equal(ui.el('mitigatorCategory').value, 'cat2');
  assert.equal(ui.el('mitigatorWeightCValue').textContent, '60');
  ui.el('mitigatorForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Describe the potential issue first/.test(ui.el('status').textContent));
  assert.equal(ui.el('mitigatorThinking').hidden, true);
  ui.el('mitigatorIssue').value = '  Payroll data could leak  ';
  ui.el('mitigatorAsset').value = 'Payroll DB';
  ui.el('mitigatorRtI').checked = true;
  ui.el('mitigatorWeightI').value = '150';
  ui.el('mitigatorWeightI').dispatchEvent(new ui.window.Event('input'));
  ui.el('mitigatorImpactSensitive').checked = true;
  ui.el('mitigatorForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'Keen Mitigator analysis complete');
  const sent = state.posted[0].body;
  assert.equal(sent.issue, 'Payroll data could leak');
  assert.equal(sent.category_name, 'Data');
  assert.equal(sent.subcategory_name, 'PII');
  assert.deepEqual(sent.risk_types, ['Confidentiality', 'Integrity']);
  assert.deepEqual(sent.risk_weights, { Confidentiality: 60, Integrity: 100, Availability: 40 });
  assert.equal(sent.impact_flags.sensitive_data, true);
  assert.equal(sent.impact_flags.financial, false);
  const results = ui.el('mitigatorSuggestions');
  assert.equal(results.querySelector('img'), null);
  assert.equal(results.querySelector('i:not(.bi)'), null);
  assert.match(
    ui.el('mitigatorSignals').textContent,
    /1 possible CIA Triad risk, 1 possible PESTLE\(E\) item, 1 possible Interested Party/,
  );
  assert.match(
    ui.el('mitigatorResultsMeta').textContent,
    /^9 suggested controls, 1 related PESTLE\(E\) item, and 2 Interested Parties for KEEN-AF:1.0/,
  );
  // The first eight control suggestions are pre-selected.
  assert.equal(results.querySelectorAll('.mitigator-suggestion-select:checked').length, 8);
  assert.match(results.textContent, /Out of scope — review applicability/);
  assert.match(results.textContent, /Reusable risk library[\s\S]*Example inherent rating: 4 × 3/);
  assert.match(
    results.textContent,
    /Draft if you choose to create: Legal \/ External — New privacy duty; business processes: Payroll, No id/,
  );
  const parties = results.querySelectorAll('.mitigator-interested-select');
  assert.equal(parties[1].disabled, true);
  assert.equal(ui.el('mitigatorCreateRisk').disabled, false);
});

test('Keen Mitigator creates a risk, PESTLE item and interested parties from its drafts', async (t) => {
  const { ui, state } = await risksPage(t, {
    htmlName: 'mitigator',
    analysis: analysis(),
    globals: { confirm: () => true },
  });
  ui.el('mitigatorIssue').value = 'Leak';
  ui.el('mitigatorForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('mitigatorResultsCard').style.display === '');
  ui.el('mitigatorCreateRisk').click();
  await settle(() => /Enter an affected asset/.test(ui.el('status').textContent));
  ui.el('mitigatorAsset').value = 'Payroll DB';
  for (const cb of ui.document.querySelectorAll('.mitigator-suggestion-select'))
    cb.checked = cb.value === 'c1';
  ui.el('mitigatorCreateRisk').click();
  await settle(() => state.posted.some((p) => p.path === '/api/v1/risks'));
  const risk = state.posted.find((p) => p.path === '/api/v1/risks').body;
  assert.equal(risk.asset_name, 'Payroll DB');
  assert.deepEqual(risk.controls, ['c1']);
  assert.deepEqual(risk.mitigator_context, {
    source: 'keen_mitigator',
    selected_control_ids: ['c1'],
  });

  ui.el('mitigatorCreatePestle').click();
  await settle(() => state.patched.length === 1);
  const pestle = state.posted.find((p) => p.path === '/api/v1/pestle/items').body;
  assert.equal('business_processes' in pestle, false);
  assert.equal(pestle.framework, 'A');
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/pestle/items/new1/business-processes',
    body: { items: [{ business_process_id: 'bp1', relevance_code: 'medium' }] },
  });

  ui.el('mitigatorCreateInterested').click();
  await settle(() => state.posted.some((p) => p.path === '/api/v1/interested-parties'));
  const created = state.posted.filter((p) => p.path === '/api/v1/interested-parties');
  // The duplicate suggestion is skipped; the selected control is linked.
  assert.equal(created.length, 1);
  assert.deepEqual(created[0].body, { name_id: 'n1', framework: 'A', controls: ['c1'] });

  ui.el('mitigatorClear').click();
  assert.equal(ui.el('mitigatorIssue').value, '');
  assert.equal(ui.el('mitigatorResultsCard').style.display, 'none');
  assert.equal(ui.el('mitigatorCreateRisk').disabled, true);
});

test('Keen Mitigator reports empty suggestions and partial creation failures', async (t) => {
  const { ui, state } = await risksPage(t, {
    htmlName: 'mitigator',
    analysis: analysis({
      framework: 'A',
      control_suggestions: [],
      existing_matches: {},
      signals: [],
      pestle_suggestions: [],
      pestle_hits: [],
      draft_pestle_item: null,
      interested_party_suggestions: [
        { name: 'A', draft: { name_id: 'n1' } },
        { name: 'B', draft: { name_id: 'n2' } },
      ],
    }),
    postFail: (path) => path === '/api/v1/interested-parties',
    globals: { confirm: () => false },
  });
  ui.el('mitigatorIssue').value = 'Leak';
  ui.el('mitigatorForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  // The mascot turns while the analysis runs, then stops.
  assert.equal(ui.el('mitigatorThinking').hidden, false);
  assert.equal(ui.el('mitigatorForm').getAttribute('aria-busy'), 'true');
  await settle(() => ui.el('mitigatorResultsCard').style.display === '');
  assert.equal(ui.el('mitigatorThinking').hidden, true);
  assert.equal(ui.el('mitigatorForm').hasAttribute('aria-busy'), false);
  assert.match(ui.el('mitigatorSuggestions').textContent, /No controls matched strongly enough/);
  // Library scenarios appear in any framework; links that are not in it say so.
  assert.match(
    ui.el('mitigatorSuggestions').textContent,
    /Reusable risk library[\s\S]*Suggested controls in A: AC-1[\s\S]*Review this template/,
  );
  assert.match(ui.el('mitigatorSignals').textContent, /No extra rule signals were matched/);
  assert.equal(ui.el('mitigatorCreatePestle').disabled, true);
  // Declining to create a risk without controls sends nothing.
  ui.el('mitigatorAsset').value = 'Asset';
  ui.el('mitigatorCreateRisk').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.ok(!state.posted.some((p) => p.path === '/api/v1/risks'));
  ui.el('mitigatorCreateInterested').click();
  await settle(() =>
    /Some Interested Parties could not be created/.test(ui.el('status').textContent),
  );
  for (const cb of ui.document.querySelectorAll('.mitigator-interested-select')) cb.checked = false;
  ui.el('mitigatorCreateInterested').click();
  await settle(() => /Select at least one Interested Party/.test(ui.el('status').textContent));
});

test('Keen Mitigator is disabled for users without risk access', async (t) => {
  const { ui } = await risksPage(t, { htmlName: 'mitigator', me: {} });
  assert.match(ui.el('status').textContent, /You need risk.read permission to use Keen Mitigator/);
  assert.ok(
    [...ui.el('mitigatorForm').querySelectorAll('textarea, button')].every((x) => x.disabled),
  );
});

test('Keen Mitigator hides creation actions the user cannot perform', async (t) => {
  const { ui } = await risksPage(t, { htmlName: 'mitigator', me: { can_view_risks: true } });
  for (const id of ['mitigatorCreateRisk', 'mitigatorCreatePestle', 'mitigatorCreateInterested'])
    assert.equal(ui.el(id).style.display, 'none', id);
});

test('audit managers can sample risks without event access', async (t) => {
  const features = { enabled: { event_sampling: false } };
  const { ui } = await risksPage(t, {
    me: { can_view_risks: true, can_manage_audits: true, features },
  });
  await settle(() => ui.document.querySelector('[data-sample-risk="r1"]'));
  const { ui: reader } = await risksPage(t, { me: { can_view_risks: true, features } });
  await settle(() => reader.el('rows').querySelector('tr'));
  assert.equal(reader.document.querySelector('[data-sample-risk]'), null);
});
