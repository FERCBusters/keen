// The risk/control visualisations on the Risks dashboard: the compact layout on
// a narrow screen, the order of risks and controls that tie on mappings, the
// Top risks and Top controls limits, a graph without D3, and the meta line when
// no framework is selected or the score mode changes before anything loads.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';

const d3 = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');

// Builds the graph the API serves: a node per mapped risk and control, a link
// per mapping, and each node's link count.
function graph(risks, controls) {
  const nodes = [];
  const links = [];
  const count = {};
  for (const r of risks)
    for (const target of r.controls) {
      links.push({
        source: `risk:${r.id}`,
        target: `control:${target}`,
        value: Math.max(1, r.risk_score),
        risk_score: r.risk_score,
        residual_risk_score: r.residual_risk_score,
        link_kind: 'relationship',
      });
      count[`risk:${r.id}`] = (count[`risk:${r.id}`] || 0) + 1;
      count[`control:${target}`] = (count[`control:${target}`] || 0) + 1;
    }
  for (const r of risks)
    nodes.push({
      id: `risk:${r.id}`,
      type: 'source',
      node_kind: 'risk',
      label: r.label,
      risk_id: r.id,
      asset: r.label.split(' - ')[0],
      threat_summary: '',
      category: r.category ?? 'Information',
      subcategory: r.subcategory ?? 'Databases',
      risk_types: r.risk_types ?? [],
      risk_score: r.risk_score,
      residual_risk_score: r.residual_risk_score,
      link_count: count[`risk:${r.id}`],
      color: '#2563eb',
    });
  for (const c of controls)
    nodes.push({
      id: `control:${c.id}`,
      type: 'control',
      node_kind: 'control',
      label: c.ref,
      control_id: c.id,
      ref: c.ref,
      title: c.title ?? null,
      in_scope: true,
      link_count: count[`control:${c.id}`],
    });
  return { nodes, links, graph_kind: 'risk_control_relationship' };
}

async function dashboard(t, data, options = {}) {
  const ui = await page(t, 'risks', {
    query: options.query ?? '&tab=visualisations',
    scripts: options.withoutD3 ? [] : [d3],
    vendor: options.vendor,
    setup(window) {
      if (!options.withoutD3) window.d3 = {};
      options.setup?.(window);
    },
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/risks/categories') return { items: [] };
      if (p === '/api/v1/risks')
        return { framework: 'A', total: 0, limit: 200, offset: 0, items: [] };
      if (p === '/api/v1/risks/register/heatmap')
        return {
          thresholds: { low_max: 4, moderate_max: 9, high_max: 16 },
          inherent: [],
          residual: [],
        };
      if (p === '/api/v1/graph/risk_control') return data;
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.el('resultMeta').textContent !== '-');
  await settle(ui.idle);
  return ui;
}

const rows = (ui) =>
  [...ui.el('riskControlsHeatmap').querySelectorAll('tbody tr')].map(
    (tr) => tr.querySelector('a').textContent,
  );
const columns = (ui) =>
  [...ui.el('riskControlsHeatmap').querySelectorAll('th.matrix-control-header')].map(
    (th) => th.textContent,
  );
const change = (ui, id, value) => {
  ui.el(id).value = value;
  ui.el(id).dispatchEvent(new ui.window.Event('change'));
};

test('changing the score mode before the visualisations load reports no mappings, then loads in that mode', async (t) => {
  const data = graph(
    [
      {
        id: 'r1',
        label: 'Payroll DB - Ransomware',
        risk_score: 12,
        residual_risk_score: 4,
        controls: ['c1'],
      },
    ],
    [{ id: 'c1', ref: 'A.8.13', title: 'Backup' }],
  );
  const ui = await dashboard(t, data, { query: '' });
  ui.el('riskVizScoreInherent').checked = true;
  ui.el('riskVizScoreInherent').dispatchEvent(new ui.window.Event('change'));
  assert.equal(
    ui.el('riskControlsGraphMeta').textContent,
    '0 of 0 risks, 0 of 0 controls, 0 visible mappings for A. Link thickness follows inherent risk score.',
  );
  assert.equal(
    ui.el('riskControlsHeatmap').textContent,
    'No risk/control mappings are available for this framework yet.',
  );
  ui.el('riskVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('riskControlsHeatmap').querySelector('td.risk-control-heatmap-cell'));
  assert.equal(
    ui.el('riskControlsHeatmap').querySelector('td.risk-control-heatmap-cell').textContent,
    '12',
  );
});

test('without D3 the graph says so and the heatmap still draws', async (t) => {
  const data = graph(
    [
      {
        id: 'r1',
        label: 'Payroll DB - Ransomware',
        risk_score: 12,
        residual_risk_score: 4,
        controls: ['c1'],
      },
    ],
    [{ id: 'c1', ref: 'A.8.13', title: 'Backup' }],
  );
  const ui = await dashboard(t, data, { withoutD3: true });
  await settle(() => ui.el('riskControlsHeatmap').querySelector('td.risk-control-heatmap-cell'));
  assert.equal(ui.el('riskControlsGraph').textContent, 'D3 is not available.');
  assert.equal(ui.el('riskControlsGraph').querySelector('svg'), null);
});

const label35 = 'Customer records held in the CRM db'; // 35 characters
const title40 = 'Information backup and restore testing!!'; // 40 characters

function layoutGraph() {
  return graph(
    [
      {
        id: 'r1',
        label: label35,
        category: 'Information assets',
        subcategory: 'Databases',
        risk_score: 12,
        residual_risk_score: 4,
        controls: ['c1'],
      },
    ],
    [{ id: 'c1', ref: 'A.8.13', title: title40 }],
  );
}

const nodeLines = (ui, i) =>
  [
    ...ui.el('riskControlsGraph').querySelectorAll('.bipartite-node')[i].querySelectorAll('text'),
  ].map((text) => [...text.querySelectorAll('tspan')].map((s) => s.textContent));
const nodeX = (ui) =>
  [...ui.el('riskControlsGraph').querySelectorAll('.bipartite-node')].map(
    (g) => g.getAttribute('transform').match(/translate\(([^,]+),/)[1],
  );
const sceneTransform = (ui) =>
  ui
    .el('riskControlsGraph')
    .querySelector('.risk-controls-graph-scene')
    .getAttribute('transform')
    .match(/[-\d.]+/g)
    .map(Number);

async function sized(t, width) {
  return dashboard(t, layoutGraph(), {
    setup(window) {
      Object.defineProperty(window.document.getElementById('riskControlsGraph'), 'clientWidth', {
        value: width,
      });
    },
  });
}

test('a narrow graph uses the compact layout with tighter columns and shorter label lines', async (t) => {
  const narrow = await sized(t, 500);
  await settle(() => narrow.el('riskControlsGraph').querySelector('svg'));
  const svg = narrow.el('riskControlsGraph').querySelector('svg');
  assert.equal(svg.getAttribute('viewBox'), '0 0 500 520');
  // Columns of 180, 220 and 180 pixels put risks at 198 and controls at 418.
  assert.deepEqual(nodeX(narrow), ['198', '418']);
  // 636 pixels of content, 4 rows 62 pixels apart, fitted into 500 × 520.
  const [x, y, scale] = sceneTransform(narrow);
  assert.ok(Math.abs(scale - 480 / 636) < 1e-6, String(scale));
  assert.ok(Math.abs(x - 10) < 1e-6, String(x));
  assert.ok(Math.abs(y - (520 - 364 * (480 / 636)) / 2) < 1e-6, String(y));
  // Risk labels wrap at 28 characters and their detail is cut to one short line.
  assert.deepEqual(nodeLines(narrow, 0), [
    ['Customer records held in the', 'CRM db (1)'],
    ['Information assets /…'],
  ]);
  // Control details are cut at 28 characters.
  assert.deepEqual(nodeLines(narrow, 1), [['A.8.13 (1)'], ['Information backup and…']]);

  // Before the browser has laid the page out the graph is drawn at 980 pixels.
  const unmeasured = await sized(t, 0);
  await settle(() => unmeasured.el('riskControlsGraph').querySelector('svg'));
  assert.equal(
    unmeasured.el('riskControlsGraph').querySelector('svg').getAttribute('viewBox'),
    '0 0 980 520',
  );
  assert.deepEqual(nodeX(unmeasured), ['351', '763']);

  const wide = await sized(t, 1200);
  await settle(() => wide.el('riskControlsGraph').querySelector('svg'));
  assert.equal(
    wide.el('riskControlsGraph').querySelector('svg').getAttribute('viewBox'),
    '0 0 1200 520',
  );
  assert.deepEqual(nodeX(wide), ['426', '930']);
  assert.deepEqual(nodeLines(wide, 0), [
    ['Customer records held in the CRM db (1)'],
    ['Information assets / Databases'],
  ]);
  assert.deepEqual(nodeLines(wide, 1), [['A.8.13 (1)'], [title40]]);
});

test('risks with as many mappings are ordered by score and then name, and controls by reference', async (t) => {
  const data = graph(
    [
      { id: 'r1', label: 'Beta asset', risk_score: 4, residual_risk_score: 2, controls: ['c2'] },
      { id: 'r2', label: 'Gamma asset', risk_score: 9, residual_risk_score: 6, controls: ['c1'] },
      { id: 'r3', label: 'Alpha asset', risk_score: 4, residual_risk_score: 2, controls: ['c3'] },
    ],
    [
      { id: 'c1', ref: 'A.5.3', title: 'Segregation' },
      { id: 'c2', ref: 'A.5.1', title: 'Policies' },
      { id: 'c3', ref: 'A.5.2', title: 'Roles' },
    ],
  );
  const ui = await dashboard(t, data);
  await settle(() => rows(ui).length === 3);
  assert.deepEqual(rows(ui), ['Gamma asset', 'Alpha asset', 'Beta asset']);
  assert.deepEqual(columns(ui), ['A.5.1', 'A.5.2', 'A.5.3']);
});

test('clearing Top risks or Top controls falls back to 30 risks and 50 controls', async (t) => {
  const risks = [];
  const controls = [];
  for (let i = 1; i <= 35; i += 1) {
    const n = String(i).padStart(2, '0');
    risks.push({
      id: `r${n}`,
      label: `Asset ${n}`,
      risk_score: 4,
      residual_risk_score: 2,
      controls: [`c${n}`],
    });
    controls.push({ id: `c${n}`, ref: `X.${n}`, title: `Control ${n}` });
  }
  const ui = await dashboard(t, graph(risks, controls));
  const meta = () => ui.el('riskControlsHeatmapMeta').textContent;
  await settle(() => meta().startsWith('15 of 35 risks, 15 of 35 controls'));
  change(ui, 'riskVizTopRisks', '');
  await settle(() => meta().startsWith('30 of 35 risks, 15 of 35 controls'));
  change(ui, 'riskVizTopControls', '');
  await settle(() => meta().startsWith('30 of 35 risks, 30 of 35 controls'));
});

test('a risk whose controls are all beyond Top controls shows no count of shown controls', async (t) => {
  const data = graph(
    [
      {
        id: 'r1',
        label: 'Payroll DB',
        risk_score: 9,
        residual_risk_score: 4,
        controls: ['c1', 'c2', 'c3', 'c4', 'c5'],
      },
      { id: 'r2', label: 'Laptops', risk_score: 6, residual_risk_score: 2, controls: ['c6'] },
    ],
    ['c1', 'c2', 'c3', 'c4', 'c5', 'c6'].map((id, i) => ({ id, ref: `A.${i + 1}`, title: id })),
  );
  const ui = await dashboard(t, data);
  change(ui, 'riskVizTopControls', '5');
  await settle(() => columns(ui).length === 5);
  const subtitles = [...ui.el('riskControlsHeatmap').querySelectorAll('tbody tr th')].map(
    (th) => th.querySelectorAll('.small-muted')[0].textContent,
  );
  assert.deepEqual(subtitles, [
    'Information / Databases · 5 shown control(s)',
    'Information / Databases',
  ]);
});

test('a control without a title is not matched by the word "null"', async (t) => {
  const data = graph(
    [{ id: 'r1', label: 'Payroll DB', risk_score: 9, residual_risk_score: 4, controls: ['c1'] }],
    [{ id: 'c1', ref: 'A.8.13', title: null }],
  );
  const ui = await dashboard(t, data);
  await settle(() => columns(ui).length === 1);
  assert.equal(
    ui.el('riskControlsHeatmap').querySelector('th.matrix-control-header').getAttribute('title'),
    'A.8.13 - ',
  );
  const filter = ui.el('riskControlsHeatmap').querySelector('[data-risk-control-matrix-filter]');
  filter.value = 'null';
  filter.dispatchEvent(new ui.window.Event('input'));
  assert.match(ui.el('riskControlsHeatmap').textContent, /No risks or controls match that filter/);
});

test('reopening the visualisations tab keeps the heatmap filter typed before leaving it', async (t) => {
  const data = graph(
    [
      { id: 'r1', label: 'Payroll DB', risk_score: 9, residual_risk_score: 4, controls: ['c1'] },
      { id: 'r2', label: 'Laptops', risk_score: 6, residual_risk_score: 2, controls: ['c2'] },
    ],
    [
      { id: 'c1', ref: 'A.8.13', title: 'Backup' },
      { id: 'c2', ref: 'A.8.1', title: 'Endpoint devices' },
    ],
  );
  const ui = await dashboard(t, data);
  await settle(() => rows(ui).length === 2);
  const filter = ui.el('riskControlsHeatmap').querySelector('[data-risk-control-matrix-filter]');
  filter.value = 'laptop';
  filter.dispatchEvent(new ui.window.Event('input'));
  assert.deepEqual(rows(ui), ['Laptops']);
  ui.el('risksListTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  ui.el('riskVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(ui.idle);
  assert.equal(
    ui.el('riskControlsHeatmap').querySelector('[data-risk-control-matrix-filter]').value,
    'laptop',
  );
  assert.deepEqual(rows(ui), ['Laptops']);
});

test('without a selected framework the meta line names the current framework', async (t) => {
  const data = graph(
    [{ id: 'r1', label: 'Payroll DB', risk_score: 9, residual_risk_score: 4, controls: ['c1'] }],
    [{ id: 'c1', ref: 'A.8.13', title: 'Backup' }],
  );
  const ui = await dashboard(t, data, {
    vendor: {
      // A deployment with framework selection turned off.
      getCurrentFramework: () => '',
      withFramework: (path) => path,
    },
  });
  await settle(() => columns(ui).length === 1);
  assert.equal(
    ui.el('riskControlsHeatmapMeta').textContent,
    '1 of 1 risks, 1 of 1 controls, 1 visible mappings for the current framework. Darker cells have higher residual risk score.',
  );
  assert.ok(ui.calls.some((c) => c.url === '/api/v1/graph/risk_control?framework='));
});
