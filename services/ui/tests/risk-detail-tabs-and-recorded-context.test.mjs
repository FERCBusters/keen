// The risk detail page: how its tabs behave without a risk id or with Bootstrap
// present, and how it summarises the Mitigator context a risk was recorded
// with, whatever subset of that context an API client stored.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';

const d3 = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');

function risk(overrides = {}) {
  return {
    id: 'r1',
    framework: 'A',
    title: 'Payroll held to ransom',
    display: 'Payroll held to ransom',
    method: 'general',
    asset: 'Payroll DB',
    asset_entity: { name: 'Payroll DB' },
    category: { name: 'Information' },
    subcategory: { name: 'Databases' },
    threat_summary: 'Ransomware',
    updated_at: '2026-04-01T00:00:00Z',
    risk_types: [],
    interested_parties: [],
    risk_owner: { id: null, type: 'user', name: null, username: null },
    risk_score: 6,
    register_likelihood: 2,
    register_impact: 3,
    residual_risk_score: 2,
    register_residual_likelihood: 1,
    register_residual_impact: 2,
    treatment_strategy: '',
    treatment_status: 'open',
    treatment_plan: '',
    treatment_due_at: null,
    note: '',
    mitigator_context: {},
    control_count: 0,
    controls: [],
    clauses: [],
    ...overrides,
  };
}

async function riskPage(t, { query = '&id=r1', record = risk(), setup, scripts } = {}) {
  const ui = await page(t, 'risk', {
    query,
    // D3 attaches itself to the object the page already holds for it.
    setup(window) {
      if (scripts) window.d3 = {};
      setup?.(window);
    },
    scripts,
    get: async (u) => {
      if (u.pathname === '/api/v1/risks/r1') return structuredClone(record);
      if (u.pathname === '/api/v1/risks/r1/changelog') return { items: [] };
      if (u.pathname === '/api/v1/questions/entities/risk/r1') return { threads: [] };
      if (u.pathname === '/api/v1/isms/swot') return { items: [], total: 0 };
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => (ui.calls.length && ui.idle()) || ui.el('status').textContent);
  return ui;
}

const shown = (ui, id) => ui.el(id).dispatchEvent(new ui.window.Event('shown.bs.tab'));

test('a risk address without an id requests nothing from its questions and changelog tabs', async (t) => {
  const ui = await riskPage(t, { query: '' });
  assert.equal(ui.el('status').textContent, 'Missing risk id.');
  shown(ui, 'riskQuestionsTab');
  assert.match(ui.window.location.search, /tab=questions/);
  // With no record to ask about, the panel explains itself and hides the form.
  assert.equal(
    ui.el('riskQuestions').querySelector('[data-question-list]').textContent,
    'Save the record first.',
  );
  assert.equal(ui.el('riskQuestions').querySelector('[data-question-ask]').hidden, true);
  shown(ui, 'riskChangelogTab');
  assert.match(ui.window.location.search, /tab=changelog/);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(ui.el('riskChangelog').textContent, 'Loading changelog');
  // Only the navigation's framework catalogue is fetched.
  assert.deepEqual(
    ui.calls.map((c) => c.url),
    ['/api/v1/frameworks'],
  );
});

test('a deep link to a tab opens it through Bootstrap', async (t) => {
  for (const [tab, expected] of [
    ['&tab=changelog', 'riskChangelogTab'],
    ['&tab=questions', 'riskQuestionsTab'],
    ['', 'riskDetailsTab'],
  ]) {
    const opened = [];
    await riskPage(t, {
      query: '&id=r1' + tab,
      setup(window) {
        window.bootstrap = {
          Tab: { getOrCreateInstance: (el) => ({ show: () => opened.push(el.id) }) },
        };
      },
    });
    assert.deepEqual(opened, [expected], tab);
  }
});

const context = (ui) => ui.el('riskMitigatorContext').textContent.replace(/\s+/g, ' ').trim();

test('a Mitigator analysis that matched nothing is summarised without prompts or control badges', async (t) => {
  // What the Mitigator stores when its analysis finds no impacts or controls.
  const ui = await riskPage(t, {
    record: risk({
      mitigator_context: {
        source: 'keen_mitigator',
        risk_weights: { Confidentiality: 60, Integrity: 40, Availability: 20 },
        impact_flags: { personal_data: false },
        suggested_controls: [],
        selected_control_ids: [],
      },
    }),
  });
  assert.equal(ui.el('riskMitigatorContextWrap').style.display, '');
  assert.equal(
    context(ui),
    'Risk weighting: Confidentiality 60 / Integrity 40 / Availability 20 Suggested controls: 0',
  );
  // Only the weighting and the count: no prompt line and no empty badge row.
  assert.equal(ui.el('riskMitigatorContext').children.length, 2);
});

test('Mitigator context stored with only its source, or with lists of the wrong shape, still renders', async (t) => {
  // The API stores mitigator_context as the client sends it, bounded only in
  // size, so a client can record the Mitigator source with any subset of fields.
  const bare = await riskPage(t, {
    record: risk({ mitigator_context: { source: 'keen_mitigator' } }),
  });
  assert.equal(
    context(bare),
    'Risk weighting: Confidentiality 0 / Integrity 0 / Availability 0 Suggested controls: 0',
  );

  const odd = await riskPage(t, {
    record: risk({
      mitigator_context: {
        source: 'keen_mitigator',
        risk_weights: { Integrity: 70 },
        selected_control_ids: 'c1,c2',
        suggested_controls: { ref: 'A.5.1' },
        impact_flags: { safety_impact: true },
      },
    }),
  });
  // Only real lists are counted; a string or an object counts as none.
  assert.equal(
    context(odd),
    'Risk weighting: Confidentiality 0 / Integrity 70 / Availability 0 Impact prompts: safety impact Suggested controls: 0',
  );
  assert.equal(odd.el('riskMitigatorContext').querySelector('.badge'), null);
});

const linkedRisk = () =>
  risk({
    controls: [{ id: 'c1', ref: 'A.5.1', title: 'Policies <b>', in_scope: true }],
    clauses: [
      { id: 'k1', ref: '6.1', title: 'Risk treatment' },
      { id: 'k2', ref: '8.2', title: '' },
    ],
  });
const graphNodes = (ui) =>
  [...ui.el('riskGraph').querySelectorAll('.bipartite-node')].map((g) =>
    [...g.querySelectorAll('text')].map((text) => text.textContent),
  );

test('the Visualisation tab draws the risk with its linked controls and clauses', async (t) => {
  const ui = await riskPage(t, { record: linkedRisk(), scripts: [d3] });
  shown(ui, 'riskGraphTab');
  assert.match(ui.window.location.search, /tab=visualisation/);
  // The risk and its kind, then each control and clause; an untitled clause has no second line.
  assert.deepEqual(graphNodes(ui), [
    ['Payroll held to ransom', 'Simple risk'],
    ['A.5.1', 'Policies <b>'],
    ['6.1', 'Risk treatment'],
    ['8.2'],
  ]);
  assert.deepEqual(
    [...ui.el('riskGraph').querySelectorAll('svg > g > text')].map((text) => text.textContent),
    ['Controls (1)', 'Clauses (2)'],
  );
  // Links carry no weight of their own, so each is drawn alike.
  assert.deepEqual(
    [...ui.el('riskGraph').querySelectorAll('path')].map((path) => [
      path.getAttribute('stroke-width'),
      path.getAttribute('stroke'),
      path.querySelector('title').textContent,
    ]),
    [
      ['3.5', '#2563eb', 'Payroll held to ransom × A.5.1'],
      ['3.5', '#0f766e', 'Payroll held to ransom × 6.1'],
      ['3.5', '#0f766e', 'Payroll held to ransom × 8.2'],
    ],
  );
  assert.equal(ui.el('riskGraph').querySelector('svg').getAttribute('viewBox'), '0 0 980 320');
  // A linked record opens its own page.
  ui.el('riskGraph')
    .querySelectorAll('.bipartite-node')[1]
    .dispatchEvent(new ui.window.Event('click'));
});

test('the address opens the Visualisation tab at the width the page has', async (t) => {
  const ui = await riskPage(t, {
    record: linkedRisk(),
    scripts: [d3],
    query: '&id=r1&tab=visualisation',
    setup(window) {
      Object.defineProperty(window.document.getElementById('riskGraph'), 'clientWidth', {
        value: 640,
      });
    },
  });
  await settle(() => ui.el('riskGraph').querySelector('svg'));
  assert.equal(ui.el('riskGraph').querySelector('svg').getAttribute('viewBox'), '0 0 640 320');
});

test('a risk with no links, a page without D3 and a risk that did not load each say so', async (t) => {
  const bare = await riskPage(t, { scripts: [d3], record: risk({ clauses: [] }) });
  shown(bare, 'riskGraphTab');
  assert.equal(
    bare.el('riskGraph').textContent,
    'This risk is not linked to any control or clause of this framework yet.',
  );

  const withoutD3 = await riskPage(t, { record: linkedRisk() });
  shown(withoutD3, 'riskGraphTab');
  assert.equal(withoutD3.el('riskGraph').textContent, 'D3 is not available.');

  const missing = await riskPage(t, { query: '', scripts: [d3] });
  shown(missing, 'riskGraphTab');
  assert.equal(missing.el('riskGraph').textContent, 'Loading visualisation');
});
