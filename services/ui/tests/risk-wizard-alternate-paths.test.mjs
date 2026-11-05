// The risk wizard on the paths its main workflow tests leave out: templates
// without CIA properties or an example asset, an asset chosen and then renamed,
// empty registers of processes, parties, users and controls, a Mitigator draft
// for a PESTLE(E) factor, editing an unrated, unowned or no-longer-owned risk,
// clause searches, the controls feature switched off, and clicks that land
// beside the controls they are meant for.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));

const categories = [
  {
    id: 'cat1',
    name: 'Information',
    subcategories: [{ id: 'sub1', name: 'Databases', category_id: 'cat1' }],
  },
];
// Assets as the asset register serialises them: always within a category and subcategory.
const assets = [
  {
    id: 'a1',
    name: 'Payroll DB',
    category: { id: 'cat1', name: 'Information' },
    subcategory: { id: 'sub1', name: 'Databases' },
  },
];
const controls = [
  { id: 'c1', ref: 'A.5.1', title: 'Policies', in_scope: true },
  { id: 'c2', ref: 'A.8.13', title: null, in_scope: true },
];
const generalTemplate = {
  id: 'tpl1',
  name: 'Lost laptop',
  threat_summary: 'A laptop is left on a train',
  method: 'risk',
  pestle_type: '',
  lens: '',
  risk_types: [],
  treatment_guidance: '',
  suggested_assessment: {},
};
// Vendors carry no detail in the party options the API serves.
const parties = {
  framework: 'A',
  interested_parties: [
    { id: 'ip1', kind: 'interested_party', label: 'Customers', detail: 'Customer' },
  ],
  people: [],
  vendors: [{ id: 'v1', kind: 'vendor', label: 'CloudCo', detail: '' }],
  natures: ['Customer', 'Regulator'],
};
const pestleMeta = {
  types: ['Political', 'Legal'],
  lenses: ['Internal', 'External'],
  relevance_levels: [
    { id: 'l1', code: 'high', label: '1 High' },
    { id: 'l2', code: 'medium', label: '2 Medium' },
    { id: 'l3', code: 'low', label: '3 Low' },
    { id: 'l4', code: 'na', label: 'Not applicable' },
  ],
};
// A register risk as the API serialises it for editing.
function existing(overrides = {}) {
  return {
    id: 'r1',
    title: 'Laptop theft',
    display: 'Laptop theft',
    method: 'general',
    asset_id: 'a1',
    asset: 'Payroll DB',
    threat_summary: 'A laptop is stolen',
    note: '',
    risk_types: [],
    interested_parties: [],
    risk_owner: { id: null, type: 'user', name: null, username: null },
    register_likelihood: null,
    register_impact: null,
    register_residual_likelihood: null,
    register_residual_impact: null,
    treatment_strategy: '',
    treatment_status: 'open',
    treatment_plan: '',
    treatment_due_at: null,
    framework: 'A',
    controls: [],
    ...overrides,
  };
}

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => (data[k] = String(v)),
    removeItem: (k) => delete data[k],
  };
}

async function wizard(t, options = {}) {
  const state = { posted: [], patched: [], gets: [] };
  const ui = await page(t, 'risk-wizard', {
    query: options.query || '',
    me: options.me || { is_admin: true },
    frameworks: options.frameworks,
    vendor: options.vendor,
    globals: {
      sessionStorage: memoryStorage(
        options.draft ? { 'keen.riskWizardDraft': JSON.stringify(options.draft) } : {},
      ),
    },
    get: async (u) => {
      const p = u.pathname;
      state.gets.push(p);
      if (p === '/api/v1/risks/categories') return { items: categories };
      if (p === '/api/v1/risks/assets') return { items: options.assets ?? assets };
      if (p === '/api/v1/risks/users')
        return (
          options.users ?? {
            items: [{ id: 'u1', username: 'dana' }],
            roles: [{ id: 'role1', name: 'Security lead' }],
          }
        );
      if (p === '/api/v1/controls') return { items: options.controls ?? controls };
      if (p === '/api/v1/risks/library') return { items: options.library ?? [generalTemplate] };
      if (p === '/api/v1/risks/parties') return options.parties ?? parties;
      if (p === '/api/v1/risks/register/heatmap')
        return {
          thresholds: { low_max: 4, moderate_max: 9, high_max: 16 },
          inherent: [],
          residual: [],
        };
      if (p === '/api/v1/pestle/meta') return pestleMeta;
      if (p === '/api/v1/pestle/business-processes')
        return { items: options.processes ?? [{ id: 'bp1', name: 'Payroll run' }] };
      if (p === '/api/v1/clauses')
        return {
          items: [
            { id: 'cl1', ref: '6.1.2', title: 'Risk assessment' },
            { id: 'cl2', ref: '8.1', title: 'Operational planning' },
          ],
        };
      if (p === '/api/v1/risks/r1') return options.risk ?? existing();
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (u.pathname === '/api/v1/pestle/items') return { id: 'pi-new' };
      return { id: 'new1' };
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      return { id: 'r1' };
    },
  });
  await settle(() => ui.el('methodChoices').querySelector('input') || ui.el('status').textContent);
  return { ui, state };
}

const next = (ui) =>
  ui.el('wizardForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
const visibleStep = (ui) =>
  [...ui.document.querySelectorAll('.risk-wizard-step')].find((s) => !s.hidden)?.dataset.step;
const input = (ui, id, value, type = 'input') => {
  ui.el(id).value = value;
  ui.el(id).dispatchEvent(new ui.window.Event(type, { bubbles: true }));
};
const check = (ui, selector, checked = true) => {
  const box = ui.document.querySelector(selector);
  box.checked = checked;
  box.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
};
const reviewValue = (ui, label) =>
  [...ui.el('reviewSummary').querySelectorAll('dt')]
    .find((dt) => dt.textContent === label)
    ?.nextElementSibling.querySelector('span')
    .textContent.replace(/\s+/g, ' ')
    .trim();
const chosenMethod = (ui) => ui.document.querySelector('input[name="riskMethod"]:checked').value;

// Fills in the steps of a simple risk until the rating step, with the asset Payroll DB.
function toRating(ui) {
  next(ui);
  ui.el('assetList').querySelector('[data-asset="a1"]').click();
  next(ui);
  ui.el('skipStep').click();
  ui.el('riskTitle').value = 'Laptop theft';
  next(ui);
}

// --- Templates ----------------------------------------------------------------------------------

test('a risk template without CIA properties is listed as a general risk and leaves the asset blank', async (t) => {
  const { ui } = await wizard(t, { query: '&method=cia' });
  const item = ui.el('templateList').querySelector('[data-template="tpl1"]');
  assert.equal(item.querySelectorAll('.small')[0].textContent, 'General risk');
  item.click();
  // Choosing it turns the CIA risk being described into a simple risk.
  assert.equal(chosenMethod(ui), 'general');
  next(ui);
  assert.equal(visibleStep(ui), 'subject');
  // The template names no example asset, so nothing is typed in for one.
  assert.equal(ui.el('assetSearch').value, '');
  assert.equal(ui.el('newAssetPanel').hidden, true);
});

test('the PESTLE(E) method with only risk templates says it has none, and clicks off a template change nothing', async (t) => {
  const { ui } = await wizard(t, { query: '&method=pestle' });
  const message = ui.el('templateList').querySelector('.list-group-item');
  assert.equal(message.textContent, 'No PESTLE(E) templates yet. Carry on without one.');
  message.click();
  assert.equal(
    ui.el('templateList').textContent,
    'No PESTLE(E) templates yet. Carry on without one.',
  );
  assert.equal(ui.el('status').textContent, '');
});

// --- Subject ------------------------------------------------------------------------------------

test('an asset chosen and then renamed in the search is offered as a new asset instead', async (t) => {
  const { ui, state } = await wizard(t);
  next(ui);
  ui.el('assetList').querySelector('[data-asset="a1"]').click();
  assert.equal(ui.el('assetSearch').value, 'Payroll DB');
  assert.match(ui.el('selectedAssetSummary').textContent, /Using existing asset Payroll DB/);
  input(ui, 'assetSearch', 'Payroll DB replica');
  assert.equal(ui.el('newAssetPanel').hidden, false);
  assert.equal(ui.el('newAssetName').textContent, 'Payroll DB replica');
  assert.match(
    ui.el('selectedAssetSummary').textContent,
    /A new asset Payroll DB replica will be added/,
  );
  input(ui, 'newAssetCategory', 'Information');
  input(ui, 'newAssetSubcategory', 'Databases');
  next(ui);
  ui.el('skipStep').click();
  ui.el('riskTitle').value = 'Replica drift';
  next(ui);
  next(ui);
  ui.el('skipStep').click();
  assert.equal(reviewValue(ui, 'Asset'), 'Payroll DB replica new Information › Databases');
  next(ui);
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].body.asset_name, 'Payroll DB replica');
  assert.equal('asset_id' in state.posted[0].body, false);
});

test('clicking the chosen asset again clears it, and clicks beside the list or the summary change nothing', async (t) => {
  const { ui } = await wizard(t);
  next(ui);
  const a1 = () => ui.el('assetList').querySelector('[data-asset="a1"]');
  a1().click();
  ui.el('selectedAssetSummary').querySelector('strong').click();
  assert.match(ui.el('selectedAssetSummary').textContent, /Using existing asset Payroll DB/);
  a1().click();
  assert.equal(ui.el('selectedAssetSummary').hidden, true);
  assert.equal(a1().classList.contains('active'), false);

  const empty = await wizard(t, { assets: [] });
  next(empty.ui);
  const message = empty.ui.el('assetList').querySelector('.list-group-item');
  message.click();
  assert.equal(message.textContent, 'No assets yet. Type a name to add the first one.');
  assert.equal(empty.ui.el('selectedAssetSummary').hidden, true);
});

test('a PESTLE(E) factor without recorded business processes explains how to add one', async (t) => {
  const { ui } = await wizard(t, { query: '&method=pestle', processes: [] });
  next(ui);
  assert.equal(
    ui.el('processList').textContent,
    'No business processes yet. Type a name to add one.',
  );
});

// --- Parties --------------------------------------------------------------------------------------

test('a vendor without detail is listed and chosen by name alone, and clicks on its chip keep it', async (t) => {
  const { ui } = await wizard(t);
  next(ui);
  ui.el('assetList').querySelector('[data-asset="a1"]').click();
  next(ui);
  const row = ui.el('partyList').querySelector('[data-party="vendor:v1"]').closest('label');
  assert.equal(row.querySelector('.flex-grow-1').textContent, 'CloudCo');
  assert.equal(
    ui
      .el('partyList')
      .querySelector('[data-party="interested_party:ip1"]')
      .closest('label')
      .querySelector('.flex-grow-1').textContent,
    'Customers - Customer',
  );
  check(ui, '[data-party="vendor:v1"]');
  const chip = ui.el('selectedParties').querySelector('.badge');
  assert.equal(chip.textContent, 'CloudCo');
  chip.click();
  assert.equal(ui.el('selectedParties').querySelector('.badge').textContent, 'CloudCo');
});

test('with no parties recorded the list says so, and two new parties are counted when saved', async (t) => {
  const none = { framework: 'A', interested_parties: [], people: [], vendors: [], natures: [] };
  const { ui, state } = await wizard(t, { parties: none });
  next(ui);
  ui.el('assetList').querySelector('[data-asset="a1"]').click();
  next(ui);
  assert.equal(
    ui.el('partyList').textContent,
    'No interested parties, people or vendors recorded yet. Type a name to add one.',
  );
  for (const [name, nature] of [
    ['Fair Work Ombudsman', 'Regulator'],
    ['Payroll team', 'Internal'],
  ]) {
    input(ui, 'partySearch', name);
    ui.el('newPartyNature').value = nature;
    ui.el('addNewParty').click();
  }
  next(ui);
  ui.el('riskTitle').value = 'Payslips changed';
  next(ui);
  next(ui);
  ui.el('skipStep').click();
  next(ui);
  await settle(() => visibleStep(ui) === 'done');
  assert.equal(state.posted[0].body.interested_parties.length, 2);
  assert.equal(
    ui.el('doneDetail').textContent,
    'Keen also added 2 new interested parties for you.',
  );
});

test('Add does nothing when the name was cleared before the party list caught up', async (t) => {
  // The design system's debounce: the list redraws a moment after typing stops.
  const debounce = (fn, wait) => {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
  };
  const { ui } = await wizard(t, { vendor: { debounce } });
  next(ui);
  ui.el('assetList').querySelector('[data-asset="a1"]').click();
  next(ui);
  input(ui, 'partySearch', 'Acme');
  await settle(() => ui.el('newPartyPanel').hidden === false);
  ui.el('newPartyNature').value = 'Customer';
  input(ui, 'partySearch', '');
  ui.el('addNewParty').click();
  assert.equal(ui.el('selectedParties').textContent, 'Nobody selected yet.');
  assert.equal(ui.el('status').textContent, '');
  assert.equal(ui.el('newPartyNature').value, 'Customer');
  // The redraw then catches up and hides the offer.
  await settle(() => ui.el('newPartyPanel').hidden);
});

// --- Drafts --------------------------------------------------------------------------------------

test('a PESTLE(E) draft from the Mitigator preselects its type and lens', async (t) => {
  const { ui } = await wizard(t, {
    query: '&method=pestle',
    draft: {
      method: 'pestle',
      threat_summary: 'Phishing regulation tightens',
      pestle_type: 'Legal',
      lens: 'Internal',
    },
  });
  next(ui);
  ui.el('skipStep').click();
  assert.equal(visibleStep(ui), 'describe');
  assert.equal(ui.el('riskDescription').value, 'Phishing regulation tightens');
  const legal = ui.document.querySelector('input[name="pestleType"][value="Legal"]');
  assert.equal(legal.checked, true);
  assert.equal(legal.closest('label').classList.contains('btn-info'), true);
  const political = ui.document.querySelector('input[name="pestleType"][value="Political"]');
  assert.equal(political.closest('label').classList.contains('btn-outline-secondary'), true);
  assert.equal(ui.el('pestleLens').value, 'Internal');
});

test('the note in a Mitigator draft is carried into the description step', async (t) => {
  const { ui } = await wizard(t, {
    draft: {
      method: 'general',
      title: 'Payroll phishing',
      threat_summary: 'Payroll staff are phished',
      asset_name: 'Payroll DB',
      category_name: '',
      subcategory_name: '',
      risk_types: [],
      note: 'From the Mitigator',
      controls: [],
      interested_parties: [],
    },
  });
  next(ui);
  next(ui);
  ui.el('skipStep').click();
  assert.equal(ui.el('riskNote').value, 'From the Mitigator');
});

// --- Rating and owners ----------------------------------------------------------------------------

test('without users or roles the owner list offers nobody', async (t) => {
  const { ui } = await wizard(t, { users: { items: [], roles: [] } });
  toRating(ui);
  assert.equal(visibleStep(ui), 'rate');
  assert.deepEqual(
    [...ui.el('riskOwner').options].map((o) => [o.value, o.textContent]),
    [['', 'Nobody yet']],
  );
  assert.equal(ui.el('riskOwner').querySelector('optgroup'), null);
});

test('a risk owned by someone no longer listed is edited with nobody as owner', async (t) => {
  const { ui, state } = await wizard(t, {
    query: '&id=r1',
    risk: existing({ risk_owner: { id: 'u9', type: 'user', name: 'erin', username: 'erin' } }),
  });
  next(ui);
  next(ui);
  next(ui);
  assert.equal(visibleStep(ui), 'rate');
  assert.equal(ui.el('riskOwner').value, '');
  assert.equal(ui.el('riskOwner').selectedIndex, 0);
  next(ui);
  ui.el('skipStep').click();
  assert.equal(reviewValue(ui, 'Owner'), 'Nobody yet');
  next(ui);
  await settle(() => state.patched.length === 1);
  assert.equal(state.patched[0].body.risk_owner_user_id, null);
});

test('a user-owned risk keeps its owner when edited', async (t) => {
  const { ui, state } = await wizard(t, {
    query: '&id=r1',
    risk: existing({ risk_owner: { id: 'u1', type: 'user', name: 'dana', username: 'dana' } }),
  });
  next(ui);
  next(ui);
  next(ui);
  assert.equal(ui.el('riskOwner').value, 'user:u1');
  next(ui);
  ui.el('skipStep').click();
  assert.equal(reviewValue(ui, 'Owner'), 'dana');
  next(ui);
  await settle(() => state.patched.length === 1);
  assert.equal(state.patched[0].body.risk_owner_user_id, 'u1');
  assert.equal(state.patched[0].body.risk_owner_role_id, null);
});

test('an unrated, unowned simple risk is edited from 1 × 1 with no owner or due date', async (t) => {
  const { ui, state } = await wizard(t, { query: '&id=r1' });
  assert.equal(visibleStep(ui), 'subject');
  next(ui);
  next(ui);
  // A simple risk has no CIA properties to tick.
  assert.equal(ui.document.querySelector('[data-method-only="cia"]').hidden, true);
  assert.equal(ui.el('riskTitle').value, 'Laptop theft');
  next(ui);
  assert.equal(ui.document.querySelector('[data-score="inherent"]').textContent, '1');
  assert.equal(ui.document.querySelector('[data-score="residual"]').textContent, '1');
  assert.equal(ui.el('riskOwner').value, '');
  assert.equal(ui.el('treatmentDue').value, '');
  next(ui);
  next(ui);
  assert.equal(reviewValue(ui, 'Kind of risk'), 'Simple risk');
  next(ui);
  await settle(() => state.patched.length === 1);
  const body = state.patched[0].body;
  assert.deepEqual(
    [
      body.register_likelihood,
      body.register_impact,
      body.register_residual_likelihood,
      body.register_residual_impact,
    ],
    [1, 1, 1, 1],
  );
  assert.deepEqual(body.risk_types, []);
  assert.equal(body.risk_owner_user_id, null);
  assert.equal(body.treatment_due_at, null);
});

// --- Clauses and processes -------------------------------------------------------------------------

async function pestleToClauses(t, options = {}) {
  const { ui, state } = await wizard(t, { query: '&method=pestle', ...options });
  next(ui);
  check(ui, '[data-process="bp1"]');
  next(ui);
  ui.el('riskDescription').value = 'Privacy law tightens retention';
  check(ui, 'input[name="pestleType"][value="Legal"]');
  return { ui, state };
}

test('a clause search keeps ticked clauses ticked, and says when nothing matches', async (t) => {
  const { ui } = await pestleToClauses(t);
  next(ui);
  assert.equal(visibleStep(ui), 'rate');
  check(ui, '[data-clause="cl1"]');
  input(ui, 'clauseSearch', 'risk');
  assert.equal(ui.el('clauseList').querySelectorAll('[data-clause]').length, 1);
  assert.equal(ui.el('clauseList').querySelector('[data-clause="cl1"]').checked, true);
  assert.equal(ui.el('clauseList').querySelector('[data-clause-level="cl1"]').disabled, false);
  input(ui, 'clauseSearch', 'zzz');
  assert.equal(ui.el('clauseList').textContent, 'No clauses match.');
});

test('with an overall relevance of not applicable, linked processes are recorded as highly relevant', async (t) => {
  const { ui, state } = await pestleToClauses(t);
  input(ui, 'pestleRelevance', 'na', 'change');
  next(ui);
  // Clause levels start at the first level when the factor itself is not applicable.
  check(ui, '[data-clause="cl2"]');
  assert.equal(ui.el('clauseList').querySelector('[data-clause-level="cl2"]').value, 'high');
  next(ui);
  assert.equal(reviewValue(ui, 'Overall relevance'), 'Not applicable');
  next(ui);
  await settle(() => state.patched.length === 2);
  assert.equal(state.posted.at(-1).body.overall_relevance_code, 'na');
  assert.deepEqual(state.patched[0].body, {
    items: [{ business_process_id: 'bp1', relevance_code: 'high' }],
  });
  assert.deepEqual(state.patched[1].body, {
    items: [{ clause_id: 'cl2', relevance_code: 'high' }],
  });
});

// --- Controls -------------------------------------------------------------------------------------

test('without a selected framework the controls step names this framework', async (t) => {
  const { ui } = await wizard(t, {
    vendor: { getCurrentFramework: () => '', withFramework: (path) => path },
  });
  toRating(ui);
  next(ui);
  assert.equal(visibleStep(ui), 'controls');
  assert.equal(ui.el('controlsFramework').textContent, 'this framework');
});

test('a control search without matches says so, and a control without a title shows its reference', async (t) => {
  const { ui } = await wizard(t);
  toRating(ui);
  next(ui);
  const labels = [...ui.el('controlList').querySelectorAll('label')].map((l) =>
    l.textContent.replace(/\s+/g, ' ').trim(),
  );
  assert.deepEqual(labels, ['A.5.1 Policies', 'A.8.13']);
  input(ui, 'controlSearch', 'zzz');
  assert.equal(ui.el('controlList').textContent, 'No controls match.');
});

test('two controls suggested by the Mitigator are counted in the plural', async (t) => {
  const { ui } = await wizard(t, {
    draft: {
      method: 'general',
      title: 'Payroll phishing',
      threat_summary: 'Payroll staff are phished',
      asset_name: 'Payroll DB',
      category_name: '',
      subcategory_name: '',
      risk_types: [],
      note: '',
      controls: ['c1', 'c2'],
      interested_parties: [],
    },
  });
  next(ui);
  next(ui);
  ui.el('skipStep').click();
  next(ui);
  next(ui);
  assert.equal(
    ui.el('suggestedControlsNote').textContent,
    'The Mitigator suggested 2 controls as mitigations for this scenario; they are pre-ticked.',
  );
});

test('where the Keen Assurance Framework is enabled, a template’s KEEN-AF links are saved with the risk', async (t) => {
  const template = {
    ...generalTemplate,
    id: 'tpl2',
    name: 'Ransomware',
    risk_types: ['Availability'],
    suggested_assessment: { keen_af_control_refs: ['BC-1', 'BC-2'] },
  };
  const { ui } = await wizard(t, {
    library: [template],
    frameworks: [
      { slug: 'A', name: 'Framework A', control_count: 2 },
      { slug: 'KEEN-AF:1.0', name: 'Keen Assurance Framework', control_count: 40 },
    ],
  });
  ui.el('templateList').querySelector('[data-template="tpl2"]').click();
  toRating(ui);
  next(ui);
  assert.equal(
    ui.el('suggestedControlsNote').textContent,
    'The template links Keen Assurance Framework controls BC-1, BC-2; Keen saves those links under KEEN-AF:1.0 when you save.',
  );
});

test('without the controls feature no controls are fetched, and an edited risk’s controls are listed by id', async (t) => {
  const { ui, state } = await wizard(t, {
    query: '&id=r1',
    me: { is_admin: true, features: { enabled: { controls: false } } },
    risk: existing({ controls: [{ id: 'c2', ref: 'A.8.13', title: null }] }),
  });
  assert.equal(state.gets.includes('/api/v1/controls'), false);
  next(ui);
  next(ui);
  next(ui);
  next(ui);
  assert.equal(visibleStep(ui), 'controls');
  assert.equal(ui.el('controlList').textContent, 'No controls match.');
  assert.equal(ui.el('controlCount').textContent, '1');
  next(ui);
  assert.equal(reviewValue(ui, 'Controls'), 'c2');
  next(ui);
  await settle(() => state.patched.length === 1);
  assert.deepEqual(state.patched[0].body.controls, ['c2']);
});

// --- Review ---------------------------------------------------------------------------------------

test('parties and controls skipped after ticking are shown unticked when the review sends you back', async (t) => {
  const { ui } = await wizard(t);
  next(ui);
  ui.el('assetList').querySelector('[data-asset="a1"]').click();
  next(ui);
  check(ui, '[data-party="vendor:v1"]');
  // Skip drops the ticked party.
  ui.el('skipStep').click();
  ui.el('riskTitle').value = 'Laptop theft';
  next(ui);
  next(ui);
  check(ui, '[data-control="c1"]');
  ui.el('skipStep').click();
  assert.equal(visibleStep(ui), 'review');
  assert.equal(reviewValue(ui, 'Affected parties'), 'None');
  assert.equal(reviewValue(ui, 'Controls'), 'None yet');
  const change = (step) =>
    ui.el('reviewSummary').querySelector(`[data-goto-step="${step}"]`).click();

  change('controls');
  assert.equal(visibleStep(ui), 'controls');
  assert.equal(ui.el('controlList').querySelector('[data-control="c1"]').checked, false);
  assert.equal(ui.el('controlCount').textContent, '0');
  next(ui);
  change('parties');
  assert.equal(visibleStep(ui), 'parties');
  assert.equal(ui.el('partyList').querySelector('[data-party="vendor:v1"]').checked, false);
  assert.equal(ui.el('selectedParties').textContent, 'Nobody selected yet.');
});
