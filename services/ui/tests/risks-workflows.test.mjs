// The Risks dashboard through the real page module: every risk with its kind,
// parties and treatment; the register, heatmap and thresholds; CIA and
// PESTLE(E) tabs; the reusable library; risk/control visualisations; controls
// without risks; and the permission boundaries.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle, pickFilter } from './support/page-fixture.mjs';

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
];
const riskRows = [
  {
    id: 'r1',
    title: 'Payroll held to ransom',
    display: 'Payroll held to ransom',
    asset: 'Payroll DB',
    threat_summary: 'Ransomware encrypts the payroll database',
    method: 'cia',
    risk_types: ['Availability'],
    interested_parties: [
      { kind: 'person', id: 'p1', label: 'Dana', detail: 'DPO', href: '/isms.html?tab=people' },
      {
        kind: 'interested_party',
        id: 'ip1',
        label: '<b>Staff</b>',
        detail: 'Internal',
        href: '/interested_party.html?id=ip1',
      },
    ],
    risk_owner: { username: 'dana' },
    risk_score: 9,
    residual_risk_score: 4,
    treatment_strategy: 'mitigate',
    treatment_status: 'in_progress',
    treatment_due_at: '2026-12-01',
    control_count: 1,
    category: { name: 'Information' },
    subcategory: { name: 'Databases' },
  },
  {
    id: 'r2',
    title: '',
    display: '<img src=x>Laptop',
    asset: '<img src=x>Laptop',
    threat_summary: '',
    method: 'general',
    risk_types: [],
    interested_parties: [],
    risk_score: 16,
    residual_risk_score: 0,
    treatment_strategy: '',
    treatment_status: 'open',
    control_count: 0,
  },
];
// r1 is rated today and after controls; r2 is rated today only.
riskRows[0] = {
  ...riskRows[0],
  register_likelihood: 3,
  register_impact: 3,
  register_residual_likelihood: 2,
  register_residual_impact: 2,
  treatment_plan: 'Restore drills',
};
riskRows[1] = {
  ...riskRows[1],
  register_likelihood: 4,
  register_impact: 4,
  register_residual_likelihood: null,
  register_residual_impact: null,
  treatment_plan: '',
};
const graph = {
  nodes: [
    {
      id: 'risk:r1',
      node_kind: 'risk',
      risk_id: 'r1',
      label:
        'Payroll DB - Ransomware with a very long descriptive label that wraps across several lines because operators like to write complete sentences into risk titles',
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
const library = [
  {
    id: 'tpl1',
    name: 'Phishing',
    method: 'risk',
    risk_types: ['Confidentiality', 'Integrity'],
    threat_summary: 'Phishing leads to credential theft',
    treatment_guidance: 'Train staff',
    suggested_assessment: {
      asset: 'Laptops',
      inherent_likelihood: 4,
      inherent_impact: 3,
      residual_likelihood: 2,
      residual_impact: 2,
      keen_af_control_refs: ['AC-1', 'AC-2'],
    },
  },
  {
    id: 'tpl2',
    name: '<b>Privacy law</b>',
    method: 'pestle',
    pestle_type: 'Legal',
    lens: 'External',
    risk_types: [],
    threat_summary: 'New retention duties',
    suggested_assessment: {},
  },
];
const pestleItems = [
  {
    id: 'pi1',
    type: 'Legal',
    lens: 'External',
    item: '<b>GDPR</b> fines rise',
    rationale: 'Regulator activity is increasing',
    overall_relevance: { code: 'high', label: '1 High' },
    business_process_count: 2,
    clause_count: 1,
  },
];

async function dashboard(t, options = {}) {
  const state = { posted: [], patched: [], removed: [], riskQueries: [], pestleQueries: [] };
  let thresholds = { low_max: 4, moderate_max: 9, high_max: 16 };
  const ui = await page(t, 'risks', {
    query: options.query || '',
    scripts: [d3],
    me: options.me || { is_admin: true },
    globals: options.globals,
    confirm: options.confirm,
    setup(window) {
      window.d3 = {};
    },
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/risks/categories') {
        if (options.categoriesFail) throw new Error('Categories offline');
        return { items: categories };
      }
      if (p === '/api/v1/risks') {
        state.riskQueries.push(Object.fromEntries(u.searchParams));
        if (options.listFail) throw new Error('List offline');
        return { items: options.risks ?? riskRows, total: 2, framework: 'A' };
      }
      if (p === '/api/v1/risks/register/heatmap') {
        if (options.heatmapFail) throw new Error('Heatmap offline');
        return {
          thresholds,
          inherent: [
            { likelihood: 3, impact: 3, count: 1 },
            { likelihood: 4, impact: 4, count: 1 },
          ],
          residual: [{ likelihood: 2, impact: 2, count: 1 }],
        };
      }
      if (p === '/api/v1/risks/library') {
        if (options.libraryFail) throw new Error('Library offline');
        return { items: library };
      }
      if (p === '/api/v1/pestle/meta')
        return {
          types: ['Political', 'Legal'],
          lenses: ['Internal', 'External'],
          relevance_levels: [],
        };
      if (p === '/api/v1/pestle/items') {
        state.pestleQueries.push(Object.fromEntries(u.searchParams));
        if (options.pestleFail) throw new Error('Factors offline');
        return { items: options.pestleItems ?? pestleItems, framework: 'A' };
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
      if (p === '/api/v1/audits') return { items: [{ id: 'au1', title: 'Audit', status: 'open' }] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({
        path: u.pathname,
        body: b instanceof FormData ? { file: b.get('file')?.name } : plain(b),
      });
      if (options.postFail) throw new Error('Rejected');
      if (u.pathname.endsWith('/import.csv')) return { created: 3 };
      return { id: 'new1' };
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      if (options.patchFail) throw new Error('Refused');
      if (u.pathname.endsWith('/register/settings')) {
        thresholds = plain(b);
        return thresholds;
      }
      return { ok: true };
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      if (options.removeFail) throw new Error('Delete refused');
      return { ok: true };
    },
  });
  await settle(() => ui.el('resultMeta').textContent !== '-' || ui.el('status').textContent);
  await new Promise((r) => setTimeout(r, 20));
  return { ui, state };
}

const cell = (ui, l, i) =>
  ui.el('heatmap').querySelector(`td[title^="Likelihood ${l}, impact ${i}"]`);

// --- All risks ------------------------------------------------------------------------------------

test('the dashboard lists every risk with its kind, parties, treatment and safe text', async (t) => {
  const { ui, state } = await dashboard(t);
  assert.deepEqual(state.riskQueries, [{ framework: 'A' }]);
  const rows = ui.el('rows').querySelectorAll('tr');
  assert.equal(rows.length, 2);
  // Highest score first; names and party labels are text, never markup.
  assert.match(rows[0].textContent, /Laptop/);
  assert.equal(ui.el('rows').querySelector('img'), null);
  assert.equal(ui.el('rows').querySelector('b'), null);
  assert.match(rows[1].textContent, /Payroll held to ransom/);
  assert.match(rows[1].textContent, /Ransomware encrypts/);
  assert.match(rows[1].querySelectorAll('td')[2].textContent, /CIA/);
  assert.match(rows[1].querySelectorAll('td')[2].textContent, /Availability/);
  assert.match(rows[0].querySelectorAll('td')[2].textContent, /Risk/);
  const parties = rows[1].querySelectorAll('td')[3];
  assert.match(parties.textContent, /Dana/);
  assert.match(parties.textContent, /<b>Staff<\/b>/);
  assert.equal(
    parties
      .querySelector('a[href="/interested_party.html?id=ip1&framework=A"]')
      ?.textContent.trim(),
    '<b>Staff</b>',
  );
  assert.match(
    rows[1].querySelectorAll('td')[7].textContent,
    /Mitigate.*In progress.*due 2026-12-01/s,
  );
  assert.match(rows[0].querySelectorAll('td')[7].textContent, /Not decided yet/);
  // Scores sit in their severity band; an unrated residual says so.
  assert.equal(rows[1].querySelectorAll('td')[5].textContent.trim(), '9 · Moderate');
  assert.equal(rows[1].querySelectorAll('td')[6].textContent.trim(), '4 · Low');
  assert.equal(rows[0].querySelectorAll('td')[5].textContent.trim(), '16 · High');
  assert.equal(rows[0].querySelectorAll('td')[6].textContent.trim(), 'Needs rating');
  assert.equal(rows[1].querySelector('[data-rate="r1"]').textContent, 'Rate / treat');
  // Open goes to the risk page, Edit to the wizard.
  assert.equal(
    rows[1].querySelector('a.btn-outline-secondary').getAttribute('href'),
    '/risk.html?id=r1&framework=A',
  );
  assert.equal(
    rows[1].querySelector('a.btn-outline-primary').getAttribute('href'),
    '/risk-wizard.html?id=r1&framework=A',
  );
  assert.equal(ui.el('newRisk').getAttribute('href'), '/risk-wizard.html');
  assert.equal(ui.el('resultMeta').textContent, '2 of 2 risks shown; control mappings for A.');

  // Filters: kind and treatment are applied on the page, categories go to the API.
  ui.el('methodFilter').value = 'cia';
  ui.el('methodFilter').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.el('resultMeta').textContent.startsWith('1 of 2'));
  assert.match(ui.el('rows').textContent, /Payroll held to ransom/);
  assert.doesNotMatch(ui.el('rows').textContent, /Laptop/);
  ui.el('methodFilter').value = '';
  ui.el('treatmentFilter').value = 'open';
  ui.el('treatmentFilter').dispatchEvent(new ui.window.Event('change'));
  await settle(
    () =>
      ui.el('rows').textContent.includes('Laptop') &&
      !ui.el('rows').textContent.includes('Payroll'),
  );
  ui.el('treatmentFilter').value = '';
  pickFilter(ui, 'categoryFilter', 'cat1');
  await settle(() => state.riskQueries.at(-1).category_id === 'cat1');
  pickFilter(ui, 'subcategoryFilter', 'sub2');
  await settle(() => state.riskQueries.at(-1).subcategory_id === 'sub2');
  ui.el('q').value = 'ransom';
  ui.el('q').dispatchEvent(new ui.window.Event('input'));
  await settle(() => state.riskQueries.at(-1).q === 'ransom');
});

test('risks are sampled into audits by their display name', async (t) => {
  const { ui, state } = await dashboard(t);
  ui.document.querySelector('[data-sample-risk="r1"]').click();
  await settle(() => ui.document.getElementById('entityAuditSampleSelect')?.value === 'au1');
  ui.document.getElementById('entityAuditSampleSubmit').click();
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].body.title, 'Payroll held to ransom');
  assert.equal(state.posted[0].body.entity_type, 'risk');
});

test('an empty register explains itself and a failed load is reported', async (t) => {
  const { ui } = await dashboard(t, { risks: [] });
  assert.match(ui.el('rows').textContent, /No risks match\. Use “Add a risk”/);
  const failed = await dashboard(t, { listFail: true });
  assert.match(failed.ui.el('rows').textContent, /Failed to load risks/);
  assert.match(failed.ui.el('status').textContent, /Failed to load risks: List offline/);
});

// --- Heatmap, thresholds and the rating card ----------------------------------------------

test('the Risks tab carries the heatmap and lets managers rate, treat and set thresholds in place', async (t) => {
  const prompts = { answer: '', seen: [] };
  const { ui, state } = await dashboard(t, {
    globals: {
      prompt: (message, suggested) => (prompts.seen.push([message, suggested]), prompts.answer),
    },
  });
  await settle(() => cell(ui, 3, 3));
  // The heatmap counts rated risks per cell for today's ratings, then after controls.
  assert.equal(cell(ui, 3, 3).textContent, '1');
  assert.equal(cell(ui, 4, 4).textContent, '1');
  assert.equal(cell(ui, 2, 2).textContent, '·');
  ui.document.querySelector('input[name="scoreMode"][value="residual"]').checked = true;
  ui.document
    .querySelector('input[name="scoreMode"][value="residual"]')
    .dispatchEvent(new ui.window.Event('change'));
  assert.equal(cell(ui, 2, 2).textContent, '1');
  assert.deepEqual(
    [ui.el('lowMax').value, ui.el('moderateMax').value, ui.el('highMax').value],
    ['4', '9', '16'],
  );
  assert.equal(ui.el('ratingCard').style.display, 'none');

  // Rating a risk sends only its register fields; the list and heatmap refresh afterwards.
  ui.el('rows').querySelector('[data-rate="r1"]').click();
  assert.equal(ui.el('ratingCard').style.display, '');
  assert.equal(ui.el('ratingTitle').textContent, 'Payroll held to ransom');
  assert.equal(ui.el('editScenario').getAttribute('href'), '/risk-wizard.html?id=r1&framework=A');
  assert.equal(ui.el('treatmentPlan').value, 'Restore drills');
  ui.el('residualLikelihood').value = '';
  ui.el('treatmentStrategy').value = 'accept';
  ui.el('ratingForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/risks/r1/register',
    body: {
      register_likelihood: 3,
      register_impact: 3,
      register_residual_likelihood: null,
      register_residual_impact: 2,
      treatment_strategy: 'accept',
      treatment_status: 'in_progress',
      treatment_due_at: '2026-12-01',
      treatment_plan: 'Restore drills',
    },
  });
  await settle(() => ui.el('status').textContent === 'Rating and treatment saved.');
  assert.equal(
    ui.calls.filter((c) => c.url.startsWith('/api/v1/risks/register/heatmap')).length,
    2,
  );
  // The card stays open on the same risk after the refresh.
  assert.equal(ui.el('ratingTitle').textContent, 'Payroll held to ransom');

  ui.el('lowMax').value = '3';
  ui.el('thresholdForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.patched.length === 2);
  assert.deepEqual(state.patched[1].body, { low_max: 3, moderate_max: 9, high_max: 16 });
  // Re-banding: 4 is no longer Low, on the list as well as the heatmap.
  await settle(() => /4 · Moderate/.test(ui.el('rows').textContent));

  // Saving a template from the rating card keeps the risk's kind and ratings.
  ui.el('rows').querySelector('[data-rate="r2"]').click();
  prompts.answer = 'Lost laptop';
  ui.el('addToLibrary').click();
  await settle(() => state.posted.some((p) => p.path === '/api/v1/risks/library'));
  const saved = state.posted.find((p) => p.path === '/api/v1/risks/library').body;
  assert.equal(prompts.seen[0][1], '<img src=x>Laptop');
  assert.equal(saved.name, 'Lost laptop');
  assert.equal(saved.method, 'risk');
  assert.deepEqual(saved.risk_types, []);
  assert.equal(saved.source_risk_id, 'r2');
  assert.equal(saved.suggested_assessment.inherent_likelihood, 4);
  ui.el('closeRating').click();
  assert.equal(ui.el('ratingCard').style.display, 'none');
});

test('CSV import reports its count and refreshes the list and heatmap', async (t) => {
  const { ui, state } = await dashboard(t);
  assert.equal(
    ui.el('exportRegister').getAttribute('href'),
    '/api/v1/risks/register/export.csv?framework=A',
  );
  Object.defineProperty(ui.el('importFile'), 'files', {
    value: [new File(['asset,category'], 'risks.csv', { type: 'text/csv' })],
    configurable: true,
  });
  ui.el('importForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === '3 risks imported.');
  assert.equal(state.posted[0].path, '/api/v1/risks/register/import.csv');
  assert.equal(state.posted[0].body.file, 'risks.csv');
  assert.equal(
    ui.calls.filter((c) => c.url.startsWith('/api/v1/risks/register/heatmap')).length,
    2,
  );
  // An old register link lands on the Risks tab.
  const old = await dashboard(t, { query: '&tab=register' });
  assert.doesNotMatch(old.ui.window.location.search, /tab=/);
});

test('a tab the page does not define, including inherited object names, keeps the Risks tab', async (t) => {
  const paths = (ui) => [...new Set(ui.calls.map((c) => c.url.split('?')[0]))].sort();
  const plain = await dashboard(t);
  await settle(() => cell(plain.ui, 3, 3));
  // Inherited names reach no loader, so the page loads exactly what it loads
  // without a tab and finishes without an error.
  for (const tab of ['__proto__', '__defineSetter__', 'constructor', 'toString', 'unknown']) {
    const { ui } = await dashboard(t, { query: `&tab=${tab}` });
    await settle(() => cell(ui, 3, 3));
    assert.deepEqual(paths(ui), paths(plain.ui), tab);
  }
});

test('readers see the heatmap and ratings without rating, threshold or import controls', async (t) => {
  const { ui, state } = await dashboard(t, { me: { can_view_risks: true } });
  await settle(() => cell(ui, 3, 3));
  assert.equal(ui.el('thresholdForm').style.display, 'none');
  assert.equal(ui.el('importForm').style.display, 'none');
  assert.equal(ui.el('newRisk').style.display, 'none');
  assert.equal(ui.document.querySelector('#rows a.btn-outline-primary'), null);
  assert.equal(ui.document.querySelector('[data-sample-risk]'), null);
  const button = ui.el('rows').querySelector('[data-rate="r1"]');
  assert.equal(button.textContent, 'Rating');
  button.click();
  assert.equal(ui.el('inherentLikelihood').disabled, true);
  assert.equal(ui.el('closeRating').disabled, false);
  ui.el('ratingForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  ui.el('thresholdForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(state.patched.length, 0);
});

test('without the register feature the heatmap and rating buttons are absent', async (t) => {
  const { ui } = await dashboard(t, {
    me: { is_admin: true, features: { enabled: { risk_register: false } } },
  });
  assert.equal(ui.el('rows').querySelector('[data-rate]'), null);
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/risks/register/heatmap')));
  assert.equal(ui.el('heatmapColumn').hasAttribute('data-feature-hidden'), true);
  assert.equal(ui.el('riskRegisterLayout').classList.contains('no-sidebar'), true);
});

test('the heatmap, export and import fold to the left so the register can widen', async (t) => {
  // Without browser storage the panel starts open and still folds.
  const { ui } = await dashboard(t);
  const layout = ui.el('riskRegisterLayout');
  const toggle = ui.el('toggleRiskSidebar');
  assert.equal(layout.classList.contains('sidebar-collapsed'), false);
  assert.equal(layout.classList.contains('no-sidebar'), false);
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(toggle.getAttribute('aria-controls'), 'riskSidebarBody');
  assert.equal(toggle.textContent, 'Hide panel');
  toggle.click();
  assert.equal(layout.classList.contains('sidebar-collapsed'), true);
  assert.equal(ui.el('riskSidebarBody').hidden, true);
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  assert.equal(toggle.textContent, 'Heatmap and import');
  assert.equal(toggle.title, 'Show the heatmap, export and import');
  toggle.click();
  assert.equal(ui.el('riskSidebarBody').hidden, false);
  assert.equal(toggle.title, 'Hide the heatmap, export and import');

  // A browser that folded the panel before finds it folded, and remembers changes.
  const stored = { 'keen.risks.sidebar': 'collapsed' };
  const storage = {
    getItem: (key) => stored[key] ?? null,
    setItem: (key, value) => {
      stored[key] = value;
    },
  };
  const again = await dashboard(t, { globals: { localStorage: storage } });
  assert.equal(again.ui.el('riskSidebarBody').hidden, true);
  assert.equal(again.ui.el('riskRegisterLayout').classList.contains('sidebar-collapsed'), true);
  again.ui.el('toggleRiskSidebar').click();
  assert.equal(stored['keen.risks.sidebar'], 'open');
  again.ui.el('toggleRiskSidebar').click();
  assert.equal(stored['keen.risks.sidebar'], 'collapsed');
});

test('rating, threshold, template and heatmap failures are reported', async (t) => {
  const { ui } = await dashboard(t, {
    patchFail: true,
    postFail: true,
    globals: { prompt: () => 'x' },
  });
  await settle(() => cell(ui, 3, 3));
  ui.el('rows').querySelector('[data-rate="r1"]').click();
  ui.el('ratingForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Could not save the rating: Refused/.test(ui.el('status').textContent));
  ui.el('thresholdForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Could not save thresholds: Refused/.test(ui.el('status').textContent));
  ui.el('addToLibrary').click();
  await settle(() => /Could not save the template: Rejected/.test(ui.el('status').textContent));
  const failed = await dashboard(t, { heatmapFail: true });
  await settle(() =>
    /Could not load the risk heatmap: Heatmap offline/.test(failed.ui.el('status').textContent),
  );
});

// --- CIA and PESTLE(E) tabs -----------------------------------------------------------------------

test('the CIA tab asks for CIA risks only and filters by type', async (t) => {
  const { ui, state } = await dashboard(t, { query: '&tab=cia' });
  await settle(() => ui.el('ciaRows').querySelectorAll('tr').length === 1);
  assert.equal(state.riskQueries.at(-1).risk_type, 'Confidentiality,Integrity,Availability');
  // A general risk returned by the API never appears here.
  assert.match(ui.el('ciaRows').textContent, /Payroll held to ransom/);
  assert.doesNotMatch(ui.el('ciaRows').textContent, /Laptop/);
  assert.equal(ui.el('ciaMeta').textContent, '1 information-security risk.');
  assert.equal(ui.el('newCiaRisk').getAttribute('href'), '/risk-wizard.html?method=cia');
  pickFilter(ui, 'typeFilter', 'Integrity');
  await settle(() => /tagged Integrity/.test(ui.el('ciaMeta').textContent));
  assert.equal(state.riskQueries.at(-1).risk_type, 'Integrity');
});

test('the PESTLE(E) tab lists factors with safe text and links to the wizard and workspace', async (t) => {
  const { ui, state } = await dashboard(t, { query: '&tab=pestle' });
  await settle(() => ui.el('pestleRows').querySelectorAll('tr').length === 1);
  const row = ui.el('pestleRows').querySelector('tr');
  assert.equal(row.querySelector('b'), null);
  assert.match(row.textContent, /Legal.*External.*<b>GDPR<\/b> fines rise.*1 High.*2.*1/s);
  assert.equal(
    row.querySelector('a').getAttribute('href'),
    '/pestle_item.html?id=pi1&tab=details&framework=A',
  );
  assert.deepEqual(
    [...ui.el('pestleTypeFilter').options].map((o) => o.value),
    ['', 'Political', 'Legal'],
  );
  assert.equal(ui.el('newPestleRisk').getAttribute('href'), '/risk-wizard.html?method=pestle');
  assert.equal(ui.el('pestleWorkspaceLink').getAttribute('href'), '/pestle.html');
  ui.el('pestleTypeFilter').value = 'Legal';
  ui.el('pestleTypeFilter').dispatchEvent(new ui.window.Event('change'));
  await settle(() => state.pestleQueries.at(-1).type === 'Legal');
  ui.el('pestleQ').value = 'fines';
  ui.el('pestleQ').dispatchEvent(new ui.window.Event('input'));
  await settle(() => state.pestleQueries.at(-1).q === 'fines');
});

// --- Library ------------------------------------------------------------------------------------

test('the library serves every kind of template, filters by kind and deletes after confirmation', async (t) => {
  const confirms = [];
  const { ui, state } = await dashboard(t, {
    query: '&tab=library',
    confirm: (options) => (confirms.push(options), true),
  });
  await settle(() => ui.el('libraryList').querySelectorAll('.list-group-item').length === 2);
  const items = ui.el('libraryList').querySelectorAll('.list-group-item');
  assert.match(
    items[0].textContent,
    /Phishing.*CIA.*Confidentiality.*Integrity.*Example rating: today 4 × 3.*Links KEEN-AF controls AC-1, AC-2/s,
  );
  assert.equal(items[1].querySelector('b'), null);
  assert.match(items[1].textContent, /<b>Privacy law<\/b>.*PESTLE\(E\).*Legal · External/s);
  assert.equal(
    items[0].querySelector('a.btn-outline-primary').getAttribute('href'),
    '/risk-wizard.html?template=tpl1&framework=A',
  );
  ui.el('libraryMethodFilter').value = 'pestle';
  ui.el('libraryMethodFilter').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('libraryList').querySelectorAll('.list-group-item').length, 1);
  ui.el('libraryMethodFilter').value = '';
  ui.el('librarySearch').value = 'phish';
  ui.el('librarySearch').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('libraryList').querySelectorAll('.list-group-item').length, 1);
  ui.el('libraryList').querySelector('[data-delete-library="tpl1"]').click();
  await settle(() => state.removed.length === 1);
  assert.deepEqual(state.removed, ['/api/v1/risks/library/tpl1']);
  assert.equal(confirms[0].title, 'Delete reusable template');
  await settle(() => ui.el('status').textContent === 'Template deleted.');
  ui.el('librarySearch').value = 'zzz';
  ui.el('librarySearch').dispatchEvent(new ui.window.Event('input'));
  assert.match(ui.el('libraryList').textContent, /No templates match/);
});

// --- Permissions ----------------------------------------------------------------------------------

test('users without risk access see a permission message and nothing loads', async (t) => {
  const { ui } = await dashboard(t, { me: {} });
  assert.match(ui.el('status').textContent, /You need risk.read permission to view risks/);
  assert.match(ui.el('rows').textContent, /risk.read permission required/);
  assert.equal(ui.el('riskVisualisationsTabItem').style.display, 'none');
  assert.equal(ui.el('newRisk').style.display, 'none');
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/risks')));
});

test('audit managers can sample risks without event access', async (t) => {
  const features = { enabled: { event_sampling: false } };
  const { ui } = await dashboard(t, {
    me: { can_view_risks: true, can_manage_audits: true, features },
  });
  await settle(() => ui.document.querySelector('[data-sample-risk="r1"]'));
  const { ui: reader } = await dashboard(t, { me: { can_view_risks: true, features } });
  await settle(() => reader.el('rows').querySelector('tr'));
  assert.equal(reader.document.querySelector('[data-sample-risk]'), null);
});

// --- Controls without risks and visualisations ---------------------------------------------------

test('controls without risks load on their tab and can be searched', async (t) => {
  const { ui } = await dashboard(t, { query: '&tab=controls-without-risks' });
  await settle(() => ui.el('controlsWithoutRisksRows').querySelector('a'));
  assert.equal(ui.el('controlsWithoutRisksRows').querySelector('b'), null);
  assert.equal(
    ui.el('controlsWithoutRisksMeta').textContent,
    '1 control without associated risks for A.',
  );
  ui.el('controlsWithoutRisksQ').value = 'gap';
  ui.el('controlsWithoutRisksQ').dispatchEvent(new ui.window.Event('input'));
  await settle(() =>
    /All controls currently have at least one/.test(ui.el('controlsWithoutRisksRows').textContent),
  );
  ui.el('controlsWithoutRisksTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  assert.match(ui.window.location.search, /tab=controls-without-risks/);
  const failed = await dashboard(t, { uncoveredFail: true });
  failed.ui.el('controlsWithoutRisksTab').dispatchEvent(new failed.ui.window.Event('shown.bs.tab'));
  await settle(() =>
    /Failed to load controls without risks/.test(failed.ui.el('status').textContent),
  );
});

test('risk visualisations draw the risk/control graph and a filterable heatmap', async (t) => {
  const { ui } = await dashboard(t);
  ui.el('riskVisualisationsTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('riskControlsGraph').querySelector('svg'));
  assert.match(ui.window.location.search, /tab=visualisations/);
  const svg = ui.el('riskControlsGraph').querySelector('svg');
  // Unlinked risks are omitted; risks with more links come first.
  assert.equal(svg.querySelectorAll('.interested-party-bipartite-node').length, 4);
  assert.match(svg.textContent, /2 risk\(s\), 2 control\(s\), 3 mapping\(s\)/);
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
  const small = {
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
  const { ui } = await dashboard(t, { query: '&tab=visualisations', graph: small });
  await settle(() => ui.el('riskControlsGraph').querySelector('svg'));
  const node = ui.el('riskControlsGraph').querySelector('.interested-party-bipartite-node');
  const lines = [...node.querySelector('text').querySelectorAll('tspan')].map((x) => x.textContent);
  assert.equal(lines.length, 3);
  assert.ok(lines.at(-1).endsWith('…'), lines.at(-1));
  assert.ok(node.querySelector(':scope > title').textContent.startsWith(label));
});

test('risk visualisations explain when no mappings exist', async (t) => {
  const { ui } = await dashboard(t, {
    query: '&tab=visualisations',
    graph: { nodes: [], links: [] },
  });
  await settle(() => /No risk\/control mappings/.test(ui.el('riskControlsHeatmap').textContent));
  assert.match(ui.el('riskControlsGraph').textContent, /No risk\/control mappings/);
});

// --- Load and action failures on each tab -----------------------------------------------------

test('CIA, PESTLE(E), library and category load failures are reported on their tabs', async (t) => {
  const cia = await dashboard(t, { query: '&tab=cia', listFail: true, categoriesFail: true });
  await settle(() => /Failed to load CIA risks\./.test(cia.ui.el('ciaRows').textContent));
  assert.equal(cia.ui.el('ciaRows').querySelector('td').getAttribute('colspan'), '8');
  assert.match(cia.ui.el('status').textContent, /Failed to load CIA risks: List offline/);
  // The category filter could not load either; the register still answers.
  assert.equal(
    cia.ui.calls.some((c) => c.url.startsWith('/api/v1/risks/categories')),
    true,
  );

  const pestle = await dashboard(t, { query: '&tab=pestle', pestleFail: true });
  await settle(() =>
    /Failed to load PESTLE\(E\) factors\./.test(pestle.ui.el('pestleRows').textContent),
  );
  assert.equal(pestle.ui.el('pestleRows').querySelector('td').getAttribute('colspan'), '7');
  assert.equal(
    pestle.ui.el('status').textContent,
    'Failed to load PESTLE(E) factors: Factors offline',
  );

  const library = await dashboard(t, { query: '&tab=library', libraryFail: true });
  await settle(() => /Could not load the risk library/.test(library.ui.el('status').textContent));
  assert.equal(
    library.ui.el('status').textContent,
    'Could not load the risk library: Library offline',
  );
});

test('a refused template deletion and a refused CSV import are reported', async (t) => {
  const { ui, state } = await dashboard(t, {
    query: '&tab=library',
    removeFail: true,
    postFail: true,
    globals: { confirm: () => true },
  });
  await settle(() => ui.el('libraryList').querySelector('[data-delete-library="tpl1"]'));
  ui.el('libraryList').querySelector('[data-delete-library="tpl1"]').click();
  await settle(() => /Could not delete the template/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Could not delete the template: Delete refused');
  assert.deepEqual(state.removed, ['/api/v1/risks/library/tpl1']);
  assert.ok(ui.el('libraryList').querySelector('[data-delete-library="tpl1"]'));

  Object.defineProperty(ui.el('importFile'), 'files', {
    value: [new File(['asset,category'], 'risks.csv', { type: 'text/csv' })],
    configurable: true,
  });
  ui.el('importForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Could not import the CSV/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Could not import the CSV: Rejected');
  assert.equal(state.posted.at(-1).path, '/api/v1/risks/register/import.csv');
});

test('risks with the same score sort by name, and factor badges follow their relevance', async (t) => {
  const tied = riskRows.map((r) => ({ ...r, risk_score: 9 }));
  const { ui } = await dashboard(t, {
    risks: tied,
    query: '&tab=pestle',
    pestleItems: [
      { ...pestleItems[0], id: 'pi1', overall_relevance: { code: 'medium', label: '2 Medium' } },
      { ...pestleItems[0], id: 'pi2', overall_relevance: { code: 'low', label: '3 Low' } },
      { ...pestleItems[0], id: 'pi3', overall_relevance: null },
    ],
  });
  await settle(() => ui.el('pestleRows').querySelectorAll('tr').length === 3);
  assert.deepEqual(
    [...ui.el('pestleRows').querySelectorAll('.badge')]
      .filter((b) => !b.classList.contains('text-bg-info'))
      .map((b) => [b.textContent, b.className]),
    [
      ['2 Medium', 'badge text-bg-warning'],
      ['3 Low', 'badge text-bg-success'],
      ['N/A', 'badge text-bg-light border'],
    ],
  );
  // Equal scores fall back to the display name: "Laptop" before "Payroll".
  const names = [...ui.el('rows').querySelectorAll('tr')].map((tr) => tr.textContent);
  assert.ok(names.length === 2);
  assert.ok(names[0].includes('Laptop'), names[0]);
  assert.ok(names[1].includes('Payroll'), names[1]);
});
