// The shared risk helpers as the Mitigator and the risk wizard use them: how a
// risk matched by the Mitigator is named when the analysis sends only its asset
// name and threat, and how the draft hand-off between the two pages behaves
// when the browser refuses session storage or holds something other than a draft.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

// The fields the analysis API sends for each existing risk it matches.
const match = (overrides) => ({
  risk_types: [],
  score: 12,
  confidence: 'medium',
  matched_terms: ['payroll'],
  ...overrides,
});

const analysis = {
  framework: 'A',
  signals: [],
  suggestions: [],
  library_suggestions: [],
  affected_suggestions: [],
  existing_matches: {
    cia_risks: [
      match({ id: 'r1', asset_name: 'Payroll DB', threat_summary: 'Staff logins are phished' }),
      match({ id: 'r2', asset_name: 'Laptops', threat_summary: '' }),
    ],
    pestle_items: [],
  },
  draft_risk: { threat_summary: 'Payroll staff are phished', risk_types: [] },
};

async function mitigator(t, storage) {
  const location = {
    origin: 'https://keen.example',
    pathname: '/mitigator.html',
    search: '?framework=A',
    href: 'https://keen.example/mitigator.html?framework=A',
    hash: '',
  };
  const posted = [];
  const ui = await page(t, 'mitigator', {
    globals: { sessionStorage: storage, location },
    get: async (u) => {
      if (u.pathname === '/api/v1/risks/categories') return { items: [] };
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u) => {
      posted.push(u.pathname);
      return analysis;
    },
  });
  await settle(() => ui.el('mitigatorIssue'));
  ui.el('mitigatorIssue').value = 'Payroll staff get phished';
  ui.el('mitigatorForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => posted.length && ui.el('mitigatorThinking').hidden);
  return { ui, location };
}

const refusing = (overrides = {}) => ({
  getItem: () => null,
  setItem: () => {
    throw new Error('QuotaExceededError');
  },
  removeItem: () => {},
  ...overrides,
});

test('a risk the Mitigator matches is named by its threat, or "Risk" when it records none', async (t) => {
  const { ui } = await mitigator(t, refusing());
  const cards = ui.el('mitigatorSuggestions').querySelectorAll('.mitigator-suggestion-card');
  assert.deepEqual(
    [...cards].slice(0, 2).map((c) => c.querySelector('.fw-semibold').textContent),
    ['Staff logins are phished', 'Risk'],
  );
});

test('the Mitigator still opens the wizard when the browser refuses to store the draft', async (t) => {
  const { ui, location } = await mitigator(t, refusing());
  ui.el('mitigatorToWizard').click();
  assert.equal(location.href, '/risk-wizard.html?framework=A');
});

async function wizard(t, storage) {
  const ui = await page(t, 'risk-wizard', {
    globals: { sessionStorage: storage },
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/risks/categories') return { items: [] };
      if (p === '/api/v1/risks/assets') return { items: [] };
      if (p === '/api/v1/risks/users') return { items: [], roles: [] };
      if (p === '/api/v1/controls') return { items: [] };
      if (p === '/api/v1/risks/library') return { items: [] };
      if (p === '/api/v1/affected/options')
        return { people: [], vendors: [], assets: [], can_add: { person: true, vendor: true } };
      if (p === '/api/v1/risks/register/heatmap')
        return { thresholds: { low_max: 4, moderate_max: 9, high_max: 16 } };
      if (p === '/api/v1/pestle/meta')
        return { types: ['Legal'], lenses: ['External'], relevance_levels: [] };
      if (p === '/api/v1/pestle/business-processes') return { items: [] };
      if (p === '/api/v1/clauses') return { items: [] };
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.el('methodChoices').querySelector('input') || ui.el('status').textContent);
  return ui;
}

const chosenMethod = (ui) => ui.document.querySelector('input[name="riskMethod"]:checked').value;

test('the wizard starts blank when the browser refuses to read session storage', async (t) => {
  const ui = await wizard(
    t,
    refusing({
      getItem: () => {
        throw new Error('SecurityError');
      },
    }),
  );
  assert.equal(ui.el('status').textContent, '');
  assert.equal(chosenMethod(ui), 'general');
  assert.equal(ui.el('stepHint').textContent, 'Step 1 of 7');
});

test('a stored value that is not a draft is discarded and the wizard starts blank', async (t) => {
  const data = { 'keen.riskWizardDraft': '42' };
  const ui = await wizard(t, {
    getItem: (k) => data[k] ?? null,
    setItem: (k, v) => (data[k] = v),
    removeItem: (k) => delete data[k],
  });
  assert.deepEqual(data, {});
  assert.equal(ui.el('status').textContent, '');
  assert.equal(chosenMethod(ui), 'general');
});

test('a stored draft missing fields fills only what it holds', async (t) => {
  // The draft is browser storage, written by whichever build of the page ran
  // last, so a field it lacks stays empty instead of reading "undefined".
  const data = {
    'keen.riskWizardDraft': JSON.stringify({
      asset_name: 'Badge readers',
      // A record named without its detail, one without its name, one without an
      // id, and one of no known kind.
      affected: [
        { kind: 'vendor', id: 'v9', label: 'Regulator' },
        { kind: 'asset', id: 'a7' },
        { kind: 'person', label: 'Nobody' },
        { kind: 'group', id: 'g1', label: 'Staff' },
      ],
    }),
  };
  const ui = await wizard(t, {
    getItem: (k) => data[k] ?? null,
    setItem: (k, v) => (data[k] = v),
    removeItem: (k) => delete data[k],
  });
  assert.equal(ui.el('assetSearch').value, 'Badge readers');
  assert.equal(ui.el('newAssetCategory').value, '');
  assert.equal(ui.el('newAssetSubcategory').value, '');
  const next = () =>
    ui.el('wizardForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  const step = () =>
    [...ui.document.querySelectorAll('.risk-wizard-step')].find((s) => !s.hidden)?.dataset.step;
  const type = (id, value) => {
    ui.el(id).value = value;
    ui.el(id).dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  };
  next();
  next();
  // The new asset still needs its category, which a filled-in "undefined"
  // would have supplied.
  assert.equal(step(), 'subject');
  assert.match(ui.el('status').textContent, /Say what kind of asset it is/);
  type('newAssetCategory', 'Facilities');
  type('newAssetSubcategory', 'Access control');
  next();
  assert.equal(step(), 'parties');
  assert.deepEqual(
    [...ui.el('affectedChosen').querySelectorAll('.badge')].map((b) => b.textContent),
    ['Regulator', ''],
  );
  assert.equal(
    ui.el('affectedList').querySelector('[data-affected="vendor:v9"]').closest('label').textContent,
    'RegulatorVendor / 3rd party',
  );
});
