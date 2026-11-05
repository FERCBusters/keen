// The Risks dashboard in the states the main workflow tests leave out: users
// without risk access, empty CIA, PESTLE(E) and library tabs, factors and
// controls in their less common shapes, tabs opened through Bootstrap or more
// than once, and the rating card's cancelled and partial actions.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));

// A register risk as the list API serialises it.
function riskRow(overrides = {}) {
  return {
    id: 'r1',
    title: 'Payroll held to ransom',
    display: 'Payroll held to ransom',
    method: 'general',
    asset: 'Payroll DB',
    category: { id: 'cat1', name: 'Information' },
    subcategory: { id: 'sub1', name: 'Databases' },
    risk_types: [],
    interested_parties: [],
    risk_owner: { id: null, type: 'user', name: null, username: null },
    threat_summary: 'Ransomware encrypts the payroll database',
    treatment_strategy: '',
    treatment_status: 'open',
    treatment_plan: '',
    treatment_due_at: null,
    register_likelihood: 3,
    register_impact: 3,
    register_residual_likelihood: null,
    register_residual_impact: null,
    risk_score: 9,
    residual_risk_score: 0,
    control_count: 0,
    framework: 'A',
    ...overrides,
  };
}

const factor = (overrides = {}) => ({
  id: 'pi1',
  framework: 'A',
  type: 'Legal',
  lens: 'External',
  item: 'Privacy law tightens retention',
  overall_relevance: { code: 'high', label: '1 High' },
  rationale: '',
  business_process_count: 0,
  clause_count: 0,
  ...overrides,
});

const template = (overrides = {}) => ({
  id: 'tpl1',
  name: 'Lost laptop',
  threat_summary: 'A laptop is left on a train',
  method: 'risk',
  pestle_type: '',
  lens: '',
  risk_types: [],
  treatment_guidance: '',
  suggested_assessment: {},
  ...overrides,
});

async function dashboard(t, options = {}) {
  const state = { posted: [], patched: [], removed: [], riskQueries: [], pestleQueries: [] };
  const ui = await page(t, 'risks', {
    query: options.query || '',
    me: options.me || { is_admin: true },
    globals: options.globals,
    confirm: options.confirm,
    setup: options.setup,
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/risks/categories') return { items: [] };
      if (p === '/api/v1/risks') {
        state.riskQueries.push(Object.fromEntries(u.searchParams));
        const items = options.risks ?? [riskRow()];
        return { framework: 'A', total: items.length, limit: 200, offset: 0, items };
      }
      if (p === '/api/v1/risks/register/heatmap')
        return {
          thresholds: { low_max: 4, moderate_max: 9, high_max: 16 },
          inherent: [],
          residual: [],
        };
      if (p === '/api/v1/risks/library') return { items: options.library ?? [template()] };
      if (p === '/api/v1/pestle/meta')
        return { types: ['Political', 'Legal'], lenses: ['Internal', 'External'] };
      if (p === '/api/v1/pestle/items') {
        state.pestleQueries.push(Object.fromEntries(u.searchParams));
        const items = options.factors ?? [factor()];
        return { framework: 'A', total: items.length, items };
      }
      if (p === '/api/v1/risks/controls-without-risks') {
        const items = options.uncovered ?? [];
        return { framework: 'A', total: items.length, items };
      }
      if (p === '/api/v1/graph/risk_control') return { nodes: [], links: [] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: b instanceof FormData ? 'form' : plain(b) });
      return { id: 'new1', created: 1 };
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
  await settle(() => ui.el('resultMeta').textContent !== '-' || ui.el('status').textContent);
  await settle(ui.idle);
  return { ui, state };
}

const shown = (ui, id) => ui.el(id).dispatchEvent(new ui.window.Event('shown.bs.tab'));
const apiPaths = (ui) => ui.calls.map((c) => c.url.split('?')[0]);

// --- Users without risk access ---------------------------------------------------------------

test('without risk access the visible register, CIA and PESTLE(E) controls request nothing', async (t) => {
  const { ui, state } = await dashboard(t, { me: {} });
  assert.match(ui.el('rows').textContent, /risk\.read permission required/);
  ui.el('reload').click();
  ui.el('q').value = 'payroll';
  ui.el('q').dispatchEvent(new ui.window.Event('input'));
  shown(ui, 'ciaTab');
  shown(ui, 'pestleTab');
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(apiPaths(ui), ['/api/v1/frameworks']);
  assert.equal(ui.el('ciaRows').querySelector('.text-danger'), null);
  assert.deepEqual(state.riskQueries, []);
});

test('the import form refuses to send a file for users without risk management', async (t) => {
  const { ui, state } = await dashboard(t, { me: {} });
  // The sidebar's import form is still on screen for users without risk access.
  assert.notEqual(ui.el('importForm').style.display, 'none');
  Object.defineProperty(ui.el('importFile'), 'files', {
    value: [new File(['asset,category'], 'risks.csv', { type: 'text/csv' })],
    configurable: true,
  });
  ui.el('importForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(state.posted, []);
  assert.equal(ui.el('status').textContent, 'You need risk.read permission to view risks.');
});

// --- CIA and PESTLE(E) ------------------------------------------------------------------------

test('a register without CIA risks says so on the CIA tab', async (t) => {
  const { ui } = await dashboard(t, { query: '&tab=cia' });
  await settle(() => /information-security/.test(ui.el('ciaMeta').textContent));
  assert.equal(
    ui.el('ciaRows').textContent.trim(),
    'No CIA risks yet. Use “Add a CIA risk” to record one.',
  );
  assert.equal(ui.el('ciaMeta').textContent, '0 information-security risks.');
});

test('two CIA risks are counted in the plural', async (t) => {
  const risks = [
    riskRow({ id: 'r1', method: 'cia', risk_types: ['Integrity'] }),
    riskRow({ id: 'r2', method: 'cia', risk_types: ['Availability'], display: 'Laptop theft' }),
  ];
  const { ui } = await dashboard(t, { query: '&tab=cia', risks });
  await settle(() => /information-security/.test(ui.el('ciaMeta').textContent));
  assert.equal(ui.el('ciaMeta').textContent, '2 information-security risks.');
  assert.equal(ui.el('ciaRows').querySelectorAll('tr').length, 2);
});

test('the PESTLE(E) tab filters by lens, explains an empty list and shows rationale only when recorded', async (t) => {
  const { ui, state } = await dashboard(t, {
    query: '&tab=pestle',
    factors: [factor(), factor({ id: 'pi2', rationale: 'Regulator <b>activity</b> is rising' })],
  });
  await settle(() => ui.el('pestleRows').querySelectorAll('tr').length === 2);
  const [bare, reasoned] = ui.el('pestleRows').querySelectorAll('tr');
  assert.equal(bare.querySelectorAll('td')[2].querySelector('.small-muted'), null);
  assert.equal(
    reasoned.querySelectorAll('td')[2].querySelector('.small-muted').textContent,
    'Regulator <b>activity</b> is rising',
  );
  ui.el('pestleLensFilter').value = 'Internal';
  ui.el('pestleLensFilter').dispatchEvent(new ui.window.Event('change'));
  await settle(() => state.pestleQueries.length === 2);
  assert.equal(state.pestleQueries.at(-1).lens, 'Internal');
  assert.equal('lens' in state.pestleQueries[0], false);

  const empty = await dashboard(t, { query: '&tab=pestle', factors: [] });
  await settle(() => /factors? for/.test(empty.ui.el('pestleMeta').textContent));
  assert.equal(
    empty.ui.el('pestleRows').textContent.trim(),
    'No PESTLE(E) factors yet. Use “Add a PESTLE(E) factor” to record one.',
  );
});

// --- Library ------------------------------------------------------------------------------------

test('an empty library explains how to add the first template', async (t) => {
  const { ui } = await dashboard(t, { query: '&tab=library', library: [] });
  await settle(() => ui.el('libraryList').textContent.trim());
  assert.equal(
    ui.el('libraryList').textContent.trim(),
    'No templates yet. On the Risks tab, choose Rate / treat on a risk and then “Save as a template”.',
  );
});

test('a risk template without CIA properties is badged as a simple risk, and readers cannot use or delete it', async (t) => {
  const { ui } = await dashboard(t, { query: '&tab=library', me: { can_view_risks: true } });
  await settle(() => ui.el('libraryList').querySelector('.list-group-item .badge'));
  const item = ui.el('libraryList').querySelector('.list-group-item');
  assert.equal(item.querySelector('.badge').textContent, 'Risk');
  assert.equal(item.querySelector('.badge').className, 'badge text-bg-secondary');
  assert.equal(item.querySelector('a, button'), null);
});

test('only a template’s Delete button asks to delete it, and cancelling keeps it', async (t) => {
  const asked = [];
  const { ui, state } = await dashboard(t, {
    query: '&tab=library',
    confirm: (options) => (asked.push(options.title), false),
  });
  await settle(() => ui.el('libraryList').querySelector('[data-delete-library]'));
  ui.el('libraryList').querySelector('.fw-semibold').click();
  assert.deepEqual(asked, []);
  ui.el('libraryList').querySelector('[data-delete-library="tpl1"]').click();
  await settle(() => asked.length === 1);
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(asked, ['Delete reusable template']);
  assert.deepEqual(state.removed, []);
  assert.equal(ui.el('status').textContent, '');
});

// --- Controls without risks --------------------------------------------------------------------

test('an out-of-scope control without risks is badged as out of scope', async (t) => {
  const { ui } = await dashboard(t, {
    query: '&tab=controls-without-risks',
    uncovered: [
      { id: 'c1', ref: 'A.5.1', title: 'Policies', in_scope: true },
      { id: 'c2', ref: 'A.8.13', title: null, in_scope: false },
    ],
  });
  await settle(() => ui.el('controlsWithoutRisksRows').querySelectorAll('tr').length === 2);
  assert.deepEqual(
    [...ui.el('controlsWithoutRisksRows').querySelectorAll('tr')].map((tr) => [
      tr.querySelectorAll('td')[1].textContent,
      tr.querySelector('.badge').textContent,
    ]),
    [
      ['Policies', 'In scope'],
      ['', 'Out of scope'],
    ],
  );
});

// --- Tabs ---------------------------------------------------------------------------------------

test('a tab named in the address is opened through Bootstrap, even when Bootstrap fails', async (t) => {
  const opened = [];
  const { ui } = await dashboard(t, {
    query: '&tab=library',
    setup(window) {
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => opened.push(el.id) }) },
      };
    },
  });
  await settle(() => ui.el('libraryList').querySelector('.list-group-item'));
  assert.deepEqual(opened, ['libraryTab']);

  const broken = await dashboard(t, {
    query: '&tab=library',
    setup(window) {
      window.bootstrap = {
        Tab: {
          getOrCreateInstance: () => ({
            show: () => {
              throw new Error('No tab target');
            },
          }),
        },
      };
    },
  });
  // The library still loads for the tab the address asked for.
  await settle(() => broken.ui.el('libraryList').querySelector('.list-group-item'));
  assert.match(broken.ui.el('libraryList').textContent, /Lost laptop/);
});

test('a tab that has loaded once is not fetched again when reopened', async (t) => {
  const { ui } = await dashboard(t);
  for (const id of ['ciaTab', 'pestleTab', 'libraryTab', 'riskVisualisationsTab']) {
    shown(ui, id);
    await settle(ui.idle);
    shown(ui, 'risksListTab');
    shown(ui, id);
    await settle(ui.idle);
  }
  const count = (path) => apiPaths(ui).filter((p) => p === path).length;
  // The register itself loads once at start and once more for the CIA tab.
  assert.equal(count('/api/v1/risks'), 2);
  assert.equal(count('/api/v1/pestle/items'), 1);
  assert.equal(count('/api/v1/risks/library'), 1);
  assert.equal(count('/api/v1/graph/risk_control'), 1);
});

test('returning to the Risks tab leaves an address without a query when nothing else was set', async (t) => {
  const { ui } = await dashboard(t, {
    setup(window) {
      window.history.replaceState({}, '', '/risks.html?tab=library');
    },
  });
  await settle(() => ui.el('libraryList').querySelector('.list-group-item'));
  shown(ui, 'risksListTab');
  assert.equal(ui.window.location.href, 'https://keen.example/risks.html');
});

// --- Rating card ---------------------------------------------------------------------------------

test('a rating saved without a due date sends no date', async (t) => {
  const { ui, state } = await dashboard(t);
  ui.el('rows').querySelector('[data-rate="r1"]').click();
  assert.equal(ui.el('treatmentDue').value, '');
  ui.el('ratingForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 1);
  assert.equal(state.patched[0].path, '/api/v1/risks/r1/register');
  assert.equal(state.patched[0].body.treatment_due_at, null);
  assert.equal(state.patched[0].body.register_residual_likelihood, null);
});

test('saving a template is offered to managers only, and a cancelled name saves nothing', async (t) => {
  const asked = [];
  const prompt = (message) => (asked.push(message), null);
  const reader = await dashboard(t, { me: { can_view_risks: true }, globals: { prompt } });
  reader.ui.el('rows').querySelector('[data-rate="r1"]').click();
  reader.ui.el('addToLibrary').click();
  assert.deepEqual(asked, []);

  const manager = await dashboard(t, { globals: { prompt } });
  manager.ui.el('rows').querySelector('[data-rate="r1"]').click();
  manager.ui.el('addToLibrary').click();
  assert.deepEqual(asked, ['Name this reusable template']);
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(manager.state.posted, []);
  assert.deepEqual(reader.state.posted, []);
});
