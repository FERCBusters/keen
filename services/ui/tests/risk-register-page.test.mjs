import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => (v instanceof FormData ? v : JSON.parse(JSON.stringify(v)));

function riskRow(i, overrides = {}) {
  return {
    id: `r${i}`,
    asset: `<img src=x>Asset ${i}`,
    threat_summary: `Threat ${i}`,
    risk_types: ['Confidentiality'],
    risk_owner: { username: 'dana' },
    register_inherent_score: 20,
    register_residual_score: 6,
    register_likelihood: 4,
    register_impact: 5,
    register_residual_likelihood: 2,
    register_residual_impact: 3,
    treatment_strategy: 'mitigate',
    treatment_status: 'in_progress',
    treatment_due_at: '2026-12-31',
    treatment_plan: 'Patch',
    control_count: 2,
    ...overrides,
  };
}

async function registerPage(t, options = {}) {
  const state = {
    pages: options.pages || [
      {
        items: [
          riskRow(1),
          riskRow(2, {
            register_inherent_score: null,
            register_residual_score: 3,
            treatment_strategy: null,
            treatment_status: 'open',
            treatment_due_at: null,
            risk_owner: null,
            threat_summary: '',
          }),
        ],
        total: 2,
      },
    ],
    library: options.library ?? [
      {
        id: 'l1',
        name: '<b>Ransomware</b>',
        threat_summary: 'Encryption',
        suggested_assessment: {
          inherent_likelihood: 3,
          inherent_impact: 4,
          residual_likelihood: 1,
          residual_impact: 2,
        },
      },
      { id: 'l2', name: 'Phishing', threat_summary: 'Email' },
    ],
    patched: [],
    posted: [],
    removed: [],
    pageCalls: 0,
  };
  const fail = options.fail || {};
  const ui = await page(t, 'risk-register', {
    me: options.me || { is_admin: true },
    globals: options.globals,
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/risks/register') {
        if (fail.register) throw new Error('Register offline');
        const data = state.pages[Math.min(state.pageCalls++, state.pages.length - 1)];
        return {
          thresholds: { low_max: 4, moderate_max: 9, high_max: 16 },
          ...structuredClone(data),
        };
      }
      if (p === '/api/v1/risks/register/heatmap') {
        if (fail.heatmap) throw new Error('Heatmap offline');
        return {
          inherent: [{ likelihood: 4, impact: 5, count: 1 }],
          residual: [{ likelihood: 2, impact: 3, count: 2 }],
        };
      }
      if (p === '/api/v1/risks/library') {
        if (fail.library) throw new Error('Library offline');
        return { items: structuredClone(state.library) };
      }
      throw new Error('Unexpected GET ' + u);
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      if (options.patchFail) throw new Error('Rejected');
      return u.pathname.endsWith('/settings') ? b : { ok: true };
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname + u.search, body: plain(b) });
      if (options.postFail) throw new Error('Import failed');
      return u.pathname.endsWith('import.csv') ? { created: 3 } : { id: 'l3' };
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      return { ok: true };
    },
  });
  await settle(() => ui.el('registerMeta').textContent || ui.el('status').textContent);
  await new Promise((r) => setTimeout(r, 5));
  return { ui, state };
}

test('the register lists risks with severity bands, treatment and a heatmap', async (t) => {
  const { ui } = await registerPage(t);
  const rows = [...ui.el('registerRows').querySelectorAll('tr')];
  assert.equal(rows.length, 2);
  assert.equal(ui.el('registerRows').querySelector('img'), null);
  assert.match(rows[0].textContent, /20 · Critical/);
  assert.match(rows[0].textContent, /6 · Moderate/);
  assert.match(rows[0].textContent, /mitigate[\s\S]*in progress · due 2026-12-31/);
  assert.match(rows[1].textContent, /Needs rating/);
  assert.match(rows[1].textContent, /3 · Low/);
  assert.match(rows[1].textContent, /Untitled risk/);
  assert.match(rows[1].textContent, /Unassigned/);
  assert.match(rows[1].textContent, /Undecided/);
  assert.match(rows[0].querySelector('a').getAttribute('href'), /risks\.html\?edit=r1&framework=A/);
  assert.equal(ui.el('registerMeta').textContent.trim(), '2 of 2 risks shown');
  assert.equal(ui.el('lowMax').value, '4');
  assert.match(
    ui.el('exportRegister').getAttribute('href'),
    /\/api\/v1\/risks\/register\/export\.csv\?framework=A/,
  );
  // Inherent heatmap: likelihood 4 × impact 5 holds one risk.
  const cell = (l, i) =>
    ui.el('heatmap').querySelector(`td[title^="Likelihood ${l}, impact ${i}:"]`);
  assert.equal(cell(4, 5).textContent, '1');
  assert.equal(cell(2, 3).textContent, '·');
  const residual = ui.document.querySelector('input[name="scoreMode"][value="residual"]');
  residual.checked = true;
  residual.dispatchEvent(new ui.window.Event('change'));
  assert.equal(cell(2, 3).textContent, '2');
  assert.equal(cell(4, 5).textContent, '·');
  // Search filters locally.
  ui.el('registerSearch').value = 'threat 1';
  ui.el('registerSearch').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('registerRows').querySelectorAll('tr').length, 1);
  ui.el('registerSearch').value = 'nothing';
  ui.el('registerSearch').dispatchEvent(new ui.window.Event('input'));
  assert.match(ui.el('registerRows').textContent, /No risks match/);
});

test('large registers load further pages on request', async (t) => {
  const { ui, state } = await registerPage(t, {
    pages: [
      { items: [riskRow(1)], total: 2 },
      { items: [riskRow(2)], total: 2 },
    ],
  });
  assert.match(ui.el('registerMeta').textContent, /1 of 2 risks shown/);
  ui.el('moreRisks').click();
  await settle(() => ui.el('registerRows').querySelectorAll('tr').length === 2);
  assert.match(
    ui.calls.filter((c) => c.url.startsWith('/api/v1/risks/register?')).at(-1).url,
    /offset=1/,
  );
  assert.equal(ui.el('moreRisks'), null);
  assert.equal(state.pageCalls, 2);
});

test('managers rate and treat a risk, sending only assessed values', async (t) => {
  const { ui, state } = await registerPage(t);
  ui.document.querySelector('[data-rate="r1"]').click();
  assert.equal(ui.el('ratingCard').style.display, '');
  assert.equal(ui.el('ratingTitle').textContent, '<img src=x>Asset 1 · Threat 1');
  assert.equal(ui.el('inherentLikelihood').value, '4');
  assert.equal(ui.el('treatmentStatus').value, 'in_progress');
  assert.match(ui.el('editScenario').getAttribute('href'), /edit=r1/);
  ui.el('inherentImpact').value = '';
  ui.el('residualLikelihood').value = '1';
  ui.el('treatmentDue').value = '';
  ui.el('ratingForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'Rating and treatment saved.');
  assert.deepEqual(state.patched[0], {
    path: '/api/v1/risks/r1/register',
    body: {
      register_likelihood: 4,
      register_impact: null,
      register_residual_likelihood: 1,
      register_residual_impact: 3,
      treatment_strategy: 'mitigate',
      treatment_status: 'in_progress',
      treatment_due_at: null,
      treatment_plan: 'Patch',
    },
  });
});

test('managers save thresholds, which re-band existing scores', async (t) => {
  const { ui, state } = await registerPage(t);
  ui.el('highMax').value = '20';
  ui.el('thresholdForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'Risk thresholds saved.');
  assert.deepEqual(state.patched[0].body, { low_max: 4, moderate_max: 9, high_max: 20 });
  assert.match(ui.el('registerRows').querySelector('tr').textContent, /20 · High/);
});

test('scenarios can be saved to and deleted from the reusable library', async (t) => {
  let suggested;
  const { ui, state } = await registerPage(t, {
    globals: { prompt: (_, def) => ((suggested = def), 'Reusable'), confirm: () => true },
  });
  const library = ui.el('libraryList');
  assert.equal(library.querySelector('b'), null);
  assert.match(library.textContent, /Example inherent: 3 × 4 · residual: 1 × 2/);
  assert.match(
    library.querySelector('a').getAttribute('href'),
    /risks\.html\?template=l1&framework=A/,
  );
  ui.el('addToLibrary').click();
  assert.equal(state.posted.length, 0);
  ui.document.querySelector('[data-rate="r1"]').click();
  ui.el('addToLibrary').click();
  await settle(() => ui.el('status').textContent === 'Scenario saved to risk library.');
  assert.equal(suggested, 'Threat 1');
  assert.deepEqual(state.posted[0].body, {
    name: 'Reusable',
    threat_summary: 'Threat 1',
    risk_types: ['Confidentiality'],
    treatment_guidance: 'Patch',
    source_risk_id: 'r1',
    suggested_assessment: {
      asset: '<img src=x>Asset 1',
      inherent_likelihood: 4,
      inherent_impact: 5,
      residual_likelihood: 2,
      residual_impact: 3,
    },
  });
  ui.document.querySelector('[data-delete-library="l2"]').click();
  await settle(() => ui.el('status').textContent === 'Template deleted.');
  assert.deepEqual(state.removed, ['/api/v1/risks/library/l2']);
});

test('CSV import posts the selected file and reports the count', async (t) => {
  const { ui, state } = await registerPage(t);
  ui.el('importForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  assert.equal(state.posted.length, 0);
  Object.defineProperty(ui.el('importFile'), 'files', {
    value: [new File(['asset\nX'], 'risks.csv')],
    configurable: true,
  });
  ui.el('importForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === '3 risks imported.');
  assert.equal(state.posted[0].path, '/api/v1/risks/register/import.csv?framework=A');
  assert.equal(state.posted[0].body.get('file').name, 'risks.csv');
});

test('readers view details without rating, threshold or import controls', async (t) => {
  const { ui, state } = await registerPage(t, {
    me: { can_view_risks: true },
    globals: { confirm: () => true },
  });
  assert.equal(ui.el('thresholdForm').style.display, 'none');
  assert.equal(ui.el('importForm').style.display, 'none');
  assert.equal(ui.document.querySelector('[data-delete-library]'), null);
  const btn = ui.document.querySelector('[data-rate="r1"]');
  assert.equal(btn.textContent, 'Details');
  btn.click();
  assert.ok(
    [...ui.el('ratingForm').querySelectorAll('input, select, textarea, button')].every(
      (x) => x.disabled,
    ),
  );
  ui.el('ratingForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  ui.el('thresholdForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  ui.el('addToLibrary').disabled = false;
  ui.el('addToLibrary').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(state.patched.length + state.posted.length, 0);
});

test('load and mutation failures are reported', async (t) => {
  const failing = await registerPage(t, { fail: { register: true, library: true, heatmap: true } });
  await settle(() => /Could not load/.test(failing.ui.el('status').textContent));
  const empty = await registerPage(t, { library: [] });
  assert.match(empty.ui.el('libraryList').textContent, /No reusable scenarios yet/);
  const rejected = await registerPage(t, { patchFail: true, postFail: true });
  rejected.ui
    .el('thresholdForm')
    .dispatchEvent(new rejected.ui.window.Event('submit', { cancelable: true }));
  await settle(() =>
    /Could not save thresholds: Error: Rejected/.test(rejected.ui.el('status').textContent),
  );
  rejected.ui.document.querySelector('[data-rate="r1"]').click();
  rejected.ui
    .el('ratingForm')
    .dispatchEvent(new rejected.ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Could not save risk rating/.test(rejected.ui.el('status').textContent));
  Object.defineProperty(rejected.ui.el('importFile'), 'files', {
    value: [new File(['x'], 'x.csv')],
    configurable: true,
  });
  rejected.ui
    .el('importForm')
    .dispatchEvent(new rejected.ui.window.Event('submit', { cancelable: true }));
  await settle(() =>
    /Could not import CSV: Error: Import failed/.test(rejected.ui.el('status').textContent),
  );
});
