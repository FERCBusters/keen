// The risk wizard: one guided path through method, subject, parties,
// description, rating, controls and review, creating the asset and parties it
// needs along the way; editing an existing risk; the PESTLE(E) path; and the
// hand-off from the Mitigator.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));

const categories = [
  { id: 'cat1', name: 'Information', subcategories: [{ id: 'sub1', name: 'Databases' }] },
  { id: 'cat2', name: 'People', subcategories: [{ id: 'sub2', name: 'Staff' }] },
];
const assets = [
  {
    id: 'a1',
    name: 'Payroll DB',
    category: { name: 'Information' },
    subcategory: { name: 'Databases' },
  },
  { id: 'a2', name: '<b>Laptops</b>', category: { name: 'Hardware' } },
];
const users = {
  items: [{ id: 'u1', username: 'dana' }],
  roles: [{ id: 'role1', name: 'Security lead' }],
};
const controls = [
  { id: 'c1', ref: 'A.5.1', title: 'Policies for information security' },
  { id: 'c2', ref: 'A.8.13', title: 'Information backup', in_scope: false },
];
const library = [
  {
    id: 'tpl1',
    name: 'Ransomware',
    method: 'risk',
    risk_types: ['Availability'],
    threat_summary: 'Ransomware encrypts the data',
    treatment_guidance: 'Keep offline backups',
    suggested_assessment: {
      asset: 'Payroll DB',
      inherent_likelihood: 4,
      inherent_impact: 5,
      residual_likelihood: 2,
      residual_impact: 3,
      keen_af_control_refs: ['BC-1'],
    },
  },
  {
    id: 'tpl2',
    name: 'Privacy law change',
    method: 'pestle',
    pestle_type: 'Legal',
    lens: 'External',
    risk_types: [],
    threat_summary: 'New retention duties',
    treatment_guidance: 'Regulator guidance is due',
    suggested_assessment: {},
  },
];
const parties = {
  framework: 'A',
  interested_parties: [
    { kind: 'interested_party', id: 'ip1', label: 'Customers', detail: 'Customer' },
  ],
  people: [{ kind: 'person', id: 'p1', label: 'Dana <b>Lee</b>', detail: 'DPO' }],
  vendors: [{ kind: 'vendor', id: 'v1', label: 'CloudCo', detail: 'Hosting' }],
  natures: ['Customer', 'Regulator'],
};
const pestleMeta = {
  types: ['Political', 'Legal'],
  lenses: ['Internal', 'External'],
  relevance_levels: [
    { code: 'high', label: '1 High' },
    { code: 'low', label: '3 Low' },
    { code: 'na', label: 'Not applicable' },
  ],
};
const existingRisk = {
  id: 'r1',
  title: 'Payroll held to ransom',
  display: 'Payroll held to ransom',
  asset_id: 'a1',
  asset: 'Payroll DB',
  threat_summary: 'Ransomware encrypts the payroll database',
  note: 'Seen at a peer organisation',
  risk_types: ['Availability'],
  interested_parties: [{ kind: 'person', id: 'p1', label: 'Dana <b>Lee</b>', detail: 'DPO' }],
  register_likelihood: 3,
  register_impact: 4,
  register_residual_likelihood: 2,
  register_residual_impact: 2,
  risk_owner: { id: 'role1', type: 'role', name: 'Security lead' },
  treatment_strategy: 'mitigate',
  treatment_status: 'in_progress',
  treatment_due_at: '2026-12-01',
  treatment_plan: 'Restore drills',
  controls: [{ id: 'c2', ref: 'A.8.13' }],
};

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => (data[k] = String(v)),
    removeItem: (k) => delete data[k],
    data,
  };
}

async function wizard(t, options = {}) {
  const state = { posted: [], patched: [], gets: [] };
  const ui = await page(t, 'risk-wizard', {
    query: options.query || '',
    me: options.me || { is_admin: true },
    globals: { sessionStorage: options.storage || memoryStorage(), ...(options.globals || {}) },
    get: async (u) => {
      const p = u.pathname;
      state.gets.push(p + u.search);
      if (p === '/api/v1/risks/categories') return { items: categories };
      if (p === '/api/v1/risks/assets') return { items: options.assets ?? assets };
      if (p === '/api/v1/risks/users') return users;
      if (p === '/api/v1/controls') return { items: controls };
      if (p === '/api/v1/risks/library') return { items: library };
      if (p === '/api/v1/risks/parties') return parties;
      if (p === '/api/v1/risks/register/heatmap') {
        if (options.registerFail) throw new Error('offline');
        return {
          inherent: [],
          residual: [],
          thresholds: { low_max: 4, moderate_max: 9, high_max: 16 },
        };
      }
      if (p === '/api/v1/pestle/meta') return pestleMeta;
      if (p === '/api/v1/pestle/business-processes')
        return { items: [{ id: 'bp1', name: 'Payroll run' }] };
      if (p === '/api/v1/clauses') {
        if (options.clausesFail) throw new Error('Clauses offline');
        return { items: [{ id: 'cl1', ref: '6.1.2', title: 'Risk assessment' }] };
      }
      if (p === '/api/v1/risks/r1') {
        if (options.riskFail) throw new Error('Risk gone');
        return existingRisk;
      }
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (options.postFail) throw new Error('Rejected');
      if (u.pathname === '/api/v1/pestle/business-processes') return { id: 'bp-new' };
      if (u.pathname === '/api/v1/pestle/items') return { id: 'pi-new' };
      return { id: 'new1' };
    },
    patch: async (u, b) => {
      state.patched.push({ path: u.pathname, body: plain(b) });
      return { id: 'r1' };
    },
  });
  await settle(
    () =>
      ui.el('methodChoices').querySelector('input') ||
      ui.el('status').textContent ||
      !ui.el('wizardForm').hidden === false,
  );
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
const tick = () => new Promise((r) => setTimeout(r, 5));
const reviewText = (ui) =>
  [...ui.el('reviewSummary').querySelectorAll('dt, dd > span')]
    .map((e) => e.textContent.replace(/\s+/g, ' ').trim())
    .join(' ');

test('the wizard walks a simple risk from method to saved, adding the asset and a party it needs', async (t) => {
  const { ui, state } = await wizard(t);
  assert.equal(visibleStep(ui), 'method');
  assert.deepEqual(
    [...ui.el('methodChoices').querySelectorAll('input')].map((i) => i.value),
    ['general', 'cia', 'pestle'],
  );
  assert.equal(ui.el('stepHint').textContent, 'Step 1 of 7');
  // Risk templates only, until PESTLE(E) is chosen.
  assert.match(ui.el('templateList').textContent, /Ransomware/);
  assert.doesNotMatch(ui.el('templateList').textContent, /Privacy law change/);
  assert.equal(ui.el('wizardBreadcrumb').getAttribute('href'), '/risks.html?framework=A');

  next(ui);
  assert.equal(visibleStep(ui), 'subject');
  assert.equal(ui.el('skipStep').hidden, true);
  assert.equal(ui.el('assetList').querySelector('b'), null);
  assert.match(ui.el('assetList').textContent, /<b>Laptops<\/b>/);
  // Nothing chosen: the wizard explains what it needs.
  next(ui);
  assert.equal(visibleStep(ui), 'subject');
  assert.match(ui.el('status').textContent, /Pick an asset, or type a name/);
  input(ui, 'assetSearch', 'HR portal');
  await tick();
  assert.equal(ui.el('newAssetPanel').hidden, false);
  assert.equal(ui.el('newAssetName').textContent, 'HR portal');
  next(ui);
  assert.match(ui.el('status').textContent, /Say what kind of asset it is/);
  input(ui, 'newAssetCategory', 'Information');
  assert.deepEqual(
    [...ui.el('assetSubcategoryOptions').options].map((o) => o.value),
    ['Databases'],
  );
  next(ui);
  assert.match(ui.el('status').textContent, /Add a subcategory too/);
  input(ui, 'newAssetSubcategory', 'Web apps');
  assert.match(
    ui.el('selectedAssetSummary').textContent,
    /A new asset HR portal will be added under Information › Web apps/,
  );
  next(ui);

  assert.equal(visibleStep(ui), 'parties');
  assert.equal(ui.el('skipStep').hidden, false);
  assert.equal(ui.el('partyList').querySelector('b'), null);
  assert.equal(ui.el('partyList').querySelectorAll('[data-party]').length, 3);
  check(ui, '[data-party="vendor:v1"]');
  assert.match(ui.el('selectedParties').textContent, /CloudCo/);
  input(ui, 'partySearch', 'Fair Work Ombudsman');
  await tick();
  assert.equal(ui.el('partyList').querySelectorAll('[data-party]').length, 0);
  assert.equal(ui.el('newPartyPanel').hidden, false);
  ui.el('addNewParty').click();
  assert.match(ui.el('status').textContent, /Say what their relationship to you is/);
  ui.el('newPartyNature').value = 'Regulator';
  ui.el('addNewParty').click();
  assert.match(ui.el('selectedParties').textContent, /Fair Work Ombudsman \(Regulator\) new/);
  // Removing a chip and re-ticking keeps the list consistent.
  ui.el('selectedParties').querySelector('[data-remove-party="vendor:v1"]').click();
  assert.doesNotMatch(ui.el('selectedParties').textContent, /CloudCo/);
  check(ui, '[data-party="person:p1"]');
  next(ui);

  assert.equal(visibleStep(ui), 'describe');
  assert.equal(ui.document.querySelector('[data-method-only="cia"]').hidden, true);
  next(ui);
  assert.match(ui.el('status').textContent, /Give the risk a short name/);
  ui.el('riskTitle').value = 'HR portal credentials phished';
  ui.el('riskDescription').value = 'Staff logins are stolen and payslips changed.';
  ui.el('riskNote').value = 'Raised by payroll';
  next(ui);

  assert.equal(visibleStep(ui), 'rate');
  assert.equal(ui.document.querySelector('[data-score="inherent"]').textContent, '1');
  input(ui, 'inherent-likelihood', '4');
  input(ui, 'inherent-impact', '5');
  assert.equal(ui.document.querySelector('[data-score="inherent"]').textContent, '20');
  assert.equal(ui.document.querySelector('[data-severity="inherent"]').textContent, 'Critical');
  assert.match(
    ui.document.querySelector('[data-scale-word="inherent-likelihood"]').textContent,
    /Likely/i,
  );
  input(ui, 'residual-likelihood', '2');
  input(ui, 'residual-impact', '3');
  assert.equal(ui.document.querySelector('[data-severity="residual"]').textContent, 'Moderate');
  assert.deepEqual(
    [...ui.el('riskOwner').options].map((o) => o.value),
    ['', 'user:u1', 'role:role1'],
  );
  ui.el('riskOwner').value = 'user:u1';
  ui.el('treatmentStrategy').value = 'mitigate';
  ui.el('treatmentStatus').value = 'in_progress';
  ui.el('treatmentDue').value = '2027-01-31';
  ui.el('treatmentPlan').value = 'Turn on MFA';
  next(ui);

  assert.equal(visibleStep(ui), 'controls');
  assert.equal(ui.el('controlsFramework').textContent, 'A');
  assert.match(ui.el('controlList').textContent, /out of scope/);
  check(ui, '[data-control="c1"]');
  assert.equal(ui.el('controlCount').textContent, '1');
  input(ui, 'controlSearch', 'backup');
  await tick();
  assert.equal(ui.el('controlList').querySelectorAll('[data-control]').length, 1);
  next(ui);

  assert.equal(visibleStep(ui), 'review');
  const review = reviewText(ui);
  assert.match(review, /Kind of risk Simple risk/);
  assert.match(review, /Asset HR portal new Information › Web apps/);
  assert.match(review, /Affected parties Fair Work Ombudsman new, Dana <b>Lee<\/b>/);
  assert.match(review, /Today 20 \(Critical\), after controls 6 \(Moderate\)/);
  assert.match(review, /Owner dana/);
  assert.match(review, /Treatment Mitigate \(In progress, due 2027-01-31\)/);
  assert.match(review, /Controls A\.5\.1/);
  assert.match(review, /Notes Raised by payroll/);
  assert.equal(ui.el('reviewSummary').querySelector('b'), null);
  assert.match(ui.el('nextStep').textContent, /Create risk/);
  // "Change" jumps back to a step and forward again through the stepper.
  ui.el('reviewSummary').querySelector('[data-goto-step="describe"]').click();
  assert.equal(visibleStep(ui), 'describe');
  assert.equal(ui.el('riskTitle').value, 'HR portal credentials phished');
  ui.el('wizardSteps').querySelector('[data-goto-step="method"]').click();
  assert.equal(visibleStep(ui), 'method');
  for (let i = 0; i < 6; i += 1) next(ui);
  assert.equal(visibleStep(ui), 'review');

  next(ui);
  await settle(() => state.posted.length === 1);
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/risks',
    body: {
      framework: 'A',
      title: 'HR portal credentials phished',
      threat_summary: 'Staff logins are stolen and payslips changed.',
      note: 'Raised by payroll',
      risk_types: [],
      register_likelihood: 4,
      register_impact: 5,
      register_residual_likelihood: 2,
      register_residual_impact: 3,
      treatment_strategy: 'mitigate',
      treatment_status: 'in_progress',
      treatment_plan: 'Turn on MFA',
      treatment_due_at: '2027-01-31',
      controls: ['c1'],
      interested_parties: [
        { name: 'Fair Work Ombudsman', nature: 'Regulator' },
        { person_id: 'p1' },
      ],
      asset_name: 'HR portal',
      category_id: 'cat1',
      subcategory_name: 'Web apps',
      risk_owner_user_id: 'u1',
      risk_owner_role_id: null,
    },
  });
  await settle(() => visibleStep(ui) === 'done');
  assert.equal(ui.el('doneTitle').textContent, 'Risk recorded');
  assert.match(
    ui.el('doneDetail').textContent,
    /Keen also added the asset “HR portal” and 1 new interested party for you/,
  );
  assert.equal(
    ui.el('doneActions').querySelector('a').getAttribute('href'),
    '/risk.html?id=new1&framework=A',
  );
  assert.equal(ui.el('wizardFooter').hidden, true);
});

test('a CIA risk needs at least one property, and a template fills the blanks', async (t) => {
  const { ui, state } = await wizard(t, { query: '&method=cia' });
  assert.equal(ui.document.querySelector('input[name="riskMethod"]:checked').value, 'cia');
  ui.el('templateList').querySelector('[data-template="tpl1"]').click();
  assert.match(ui.el('status').textContent, /Template loaded/);
  assert.ok(
    ui.el('templateList').querySelector('[data-template="tpl1"]').classList.contains('active'),
  );
  next(ui);
  // The template's example asset matched an existing one.
  assert.match(ui.el('selectedAssetSummary').textContent, /Using existing asset Payroll DB/);
  next(ui);
  ui.el('skipStep').click();
  assert.equal(visibleStep(ui), 'describe');
  assert.equal(ui.document.querySelector('[data-method-only="cia"]').hidden, false);
  assert.equal(ui.el('riskTitle').value, 'Ransomware');
  assert.equal(ui.el('riskDescription').value, 'Ransomware encrypts the data');
  assert.equal(ui.el('riskNote').value, 'Keep offline backups');
  assert.equal(ui.document.querySelector('[data-cia="Availability"]').checked, true);
  check(ui, '[data-cia="Availability"]', false);
  next(ui);
  assert.match(
    ui.el('status').textContent,
    /Tick at least one of confidentiality, integrity or availability/,
  );
  check(ui, '[data-cia="Integrity"]');
  next(ui);
  assert.equal(visibleStep(ui), 'rate');
  assert.equal(ui.document.querySelector('[data-score="inherent"]').textContent, '20');
  assert.equal(ui.document.querySelector('[data-score="residual"]').textContent, '6');
  next(ui);
  assert.equal(visibleStep(ui), 'controls');
  assert.match(ui.el('suggestedControlsNote').textContent, /BC-1.*not enabled here/);
  ui.el('skipStep').click();
  assert.match(ui.el('reviewSummary').textContent, /Template\s*Ransomware/);
  assert.match(ui.el('reviewSummary').textContent, /Security properties\s*Integrity/);
  next(ui);
  await settle(() => state.posted.length === 1);
  const body = state.posted[0].body;
  assert.deepEqual(body.risk_types, ['Integrity']);
  assert.equal(body.asset_id, 'a1');
  assert.equal(body.library_template_id, 'tpl1');
  assert.deepEqual(body.controls, []);
  assert.deepEqual(body.interested_parties, []);
  await settle(() => visibleStep(ui) === 'done');
  assert.equal(ui.el('doneDetail').textContent, 'Everything is in the register.');
});

test('a template can be deselected, and changing method clears a template of the other kind', async (t) => {
  const { ui } = await wizard(t, { query: '&template=tpl2' });
  // A PESTLE(E) template switches the wizard to the PESTLE(E) method.
  assert.equal(ui.document.querySelector('input[name="riskMethod"]:checked').value, 'pestle');
  assert.match(ui.el('templateList').textContent, /Privacy law change/);
  assert.equal(ui.el('stepHint').textContent, 'Step 1 of 5');
  const radio = ui.document.querySelector('input[name="riskMethod"][value="general"]');
  radio.checked = true;
  radio.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  assert.equal(ui.el('templateList').querySelector('.active'), null);
  assert.equal(ui.el('stepHint').textContent, 'Step 1 of 7');
  ui.el('templateList').querySelector('[data-template="tpl1"]').click();
  ui.el('templateList').querySelector('[data-template="tpl1"]').click();
  assert.equal(ui.el('templateList').querySelector('.active'), null);
  input(ui, 'templateSearch', 'zzz');
  await tick();
  assert.match(ui.el('templateList').textContent, /No risk templates match/);
  const missing = await wizard(t, { query: '&template=nope' });
  assert.match(missing.ui.el('status').textContent, /That template no longer exists/);
});

test('the PESTLE(E) path records a factor with its processes and clauses', async (t) => {
  const { ui, state } = await wizard(t, { query: '&method=pestle' });
  next(ui);
  assert.equal(visibleStep(ui), 'subject');
  assert.equal(ui.document.querySelector('[data-subject="asset"]').hidden, true);
  assert.equal(ui.el('skipStep').hidden, false);
  check(ui, '[data-process="bp1"]');
  input(ui, 'processSearch', 'Recruitment');
  await tick();
  ui.el('processList').querySelector('[data-add-process="Recruitment"]').click();
  assert.match(ui.el('processList').textContent, /Recruitment\s*new/);
  // A new process can be unticked again before saving.
  input(ui, 'processSearch', 'Onboarding');
  await tick();
  ui.el('processList').querySelector('[data-add-process="Onboarding"]').click();
  check(ui, '[data-new-process="Onboarding"]', false);
  assert.doesNotMatch(ui.el('processList').textContent, /Onboarding/);
  next(ui);
  assert.equal(visibleStep(ui), 'describe');
  assert.equal(ui.el('riskTitle').closest('.col-12').hidden, true);
  next(ui);
  assert.match(ui.el('status').textContent, /Describe the factor/);
  ui.el('riskDescription').value = 'Privacy law tightens retention';
  next(ui);
  assert.match(ui.el('status').textContent, /Choose which kind of influence/);
  const legal = ui.document.querySelector('input[name="pestleType"][value="Legal"]');
  legal.checked = true;
  legal.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  assert.ok(legal.closest('label').classList.contains('btn-info'));
  ui.el('pestleLens').value = 'Internal';
  input(ui, 'pestleRelevance', 'low', 'change');
  ui.el('pestleRationale').value = 'Draft bill published';
  ui.el('riskNote').value = 'Watch the consultation';
  next(ui);
  assert.equal(visibleStep(ui), 'rate');
  assert.equal(ui.document.querySelector('[data-method-not="pestle"]').hidden, true);
  check(ui, '[data-clause="cl1"]');
  const level = ui.el('clauseList').querySelector('[data-clause-level="cl1"]');
  assert.equal(level.disabled, false);
  assert.equal(level.value, 'low');
  level.value = 'high';
  level.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  next(ui);
  assert.equal(visibleStep(ui), 'review');
  const review = reviewText(ui);
  assert.match(review, /Business processes Payroll run, Recruitment \(new\)/);
  assert.match(review, /Type and lens Legal · Internal/);
  assert.match(review, /Overall relevance 3 Low/);
  assert.match(review, /Relevant clauses 6\.1\.2/);
  assert.match(ui.el('nextStep').textContent, /Create PESTLE\(E\) factor/);
  next(ui);
  await settle(() => state.patched.length === 2);
  assert.deepEqual(state.posted, [
    { path: '/api/v1/pestle/business-processes', body: { name: 'Recruitment' } },
    {
      path: '/api/v1/pestle/items',
      body: {
        framework: 'A',
        type: 'Legal',
        lens: 'Internal',
        item: 'Privacy law tightens retention',
        overall_relevance_code: 'low',
        rationale: 'Draft bill published\n\nWatch the consultation',
      },
    },
  ]);
  assert.deepEqual(state.patched, [
    {
      path: '/api/v1/pestle/items/pi-new/business-processes',
      body: {
        items: [
          { business_process_id: 'bp1', relevance_code: 'low' },
          { business_process_id: 'bp-new', relevance_code: 'low' },
        ],
      },
    },
    {
      path: '/api/v1/pestle/items/pi-new/clauses',
      body: { items: [{ clause_id: 'cl1', relevance_code: 'high' }] },
    },
  ]);
  assert.equal(ui.el('doneTitle').textContent, 'PESTLE(E) factor recorded');
  assert.match(ui.el('doneDetail').textContent, /the business process “Recruitment”/);
  assert.equal(
    ui.el('doneActions').querySelector('a').getAttribute('href'),
    '/pestle_item.html?id=pi-new&tab=details&framework=A',
  );
  assert.match(
    ui.el('doneActions').querySelectorAll('a')[2].getAttribute('href'),
    /risks\.html\?tab=pestle&framework=A/,
  );
});

test('editing an existing risk starts at the subject with everything filled in and saves with PATCH', async (t) => {
  const { ui, state } = await wizard(t, { query: '&id=r1' });
  assert.equal(ui.el('wizardTitle').textContent, 'Edit risk');
  assert.match(ui.el('wizardIntro').textContent, /Payroll held to ransom/);
  assert.equal(visibleStep(ui), 'subject');
  assert.equal(ui.el('wizardSteps').querySelector('[data-goto-step="method"]'), null);
  assert.match(ui.el('selectedAssetSummary').textContent, /Using existing asset Payroll DB/);
  // Choosing another asset, then changing one's mind.
  ui.el('selectedAssetSummary').querySelector('#clearAsset').click();
  assert.equal(ui.el('selectedAssetSummary').hidden, true);
  ui.el('assetList').querySelector('[data-asset="a2"]').click();
  assert.match(ui.el('selectedAssetSummary').textContent, /<b>Laptops<\/b>/);
  assert.equal(ui.el('assetSearch').value, '<b>Laptops</b>');
  ui.el('selectedAssetSummary').querySelector('#clearAsset').click();
  ui.el('assetList').querySelector('[data-asset="a1"]').click();
  next(ui);
  assert.equal(ui.document.querySelector('[data-party="person:p1"]').checked, true);
  next(ui);
  assert.equal(ui.el('riskTitle').value, 'Payroll held to ransom');
  assert.equal(ui.document.querySelector('[data-cia="Availability"]').checked, true);
  next(ui);
  assert.equal(ui.el('riskOwner').value, 'role:role1');
  assert.equal(ui.el('treatmentPlan').value, 'Restore drills');
  assert.equal(ui.document.querySelector('[data-score="inherent"]').textContent, '12');
  ui.el('backStep').click();
  assert.equal(visibleStep(ui), 'describe');
  next(ui);
  next(ui);
  assert.equal(ui.document.querySelector('[data-control="c2"]').checked, true);
  next(ui);
  assert.match(ui.el('nextStep').textContent, /Save changes/);
  assert.match(ui.el('reviewSummary').textContent, /Owner\s*Security lead/);
  next(ui);
  await settle(() => state.patched.length === 1);
  assert.equal(state.patched[0].path, '/api/v1/risks/r1');
  const body = state.patched[0].body;
  assert.equal(body.asset_id, 'a1');
  assert.equal(body.title, 'Payroll held to ransom');
  assert.deepEqual(body.risk_types, ['Availability']);
  assert.deepEqual(body.interested_parties, [{ person_id: 'p1' }]);
  assert.equal(body.risk_owner_role_id, 'role1');
  assert.equal(body.risk_owner_user_id, null);
  assert.deepEqual(body.controls, ['c2']);
  assert.equal('library_template_id' in body, false);
  await settle(() => visibleStep(ui) === 'done');
  assert.equal(ui.el('doneTitle').textContent, 'Risk updated');
  assert.equal(
    ui.el('doneActions').querySelector('a').getAttribute('href'),
    '/risk.html?id=r1&framework=A',
  );
});

test('a Mitigator draft pre-fills the wizard and pre-ticks its suggested controls', async (t) => {
  const storage = memoryStorage({
    'keen.riskWizardDraft': JSON.stringify({
      method: 'general',
      risk_types: ['Confidentiality', 'Bogus'],
      title: 'Phishing of payroll staff',
      threat_summary: 'Attackers phish payroll staff for credentials',
      asset_name: 'Payroll DB',
      controls: ['c1', 'unknown'],
      interested_parties: [
        { kind: 'vendor', id: 'v1', label: 'CloudCo', detail: 'Hosting' },
        { name: 'Payroll team', nature: 'Internal' },
      ],
      mitigator_context: { query: 'phishing payroll' },
    }),
  });
  const { ui, state } = await wizard(t, { storage });
  assert.match(
    ui.el('status').textContent,
    /Mitigator's analysis has been carried into the wizard/,
  );
  assert.equal(storage.getItem('keen.riskWizardDraft'), null);
  // A CIA type in the draft turns a general draft into a CIA risk.
  assert.equal(ui.document.querySelector('input[name="riskMethod"]:checked').value, 'cia');
  next(ui);
  assert.match(ui.el('selectedAssetSummary').textContent, /Using existing asset Payroll DB/);
  next(ui);
  assert.match(ui.el('selectedParties').textContent, /CloudCo.*Payroll team \(Internal\) new/s);
  next(ui);
  assert.equal(ui.el('riskTitle').value, 'Phishing of payroll staff');
  assert.equal(ui.document.querySelector('[data-cia="Confidentiality"]').checked, true);
  next(ui);
  next(ui);
  assert.equal(ui.document.querySelector('[data-control="c1"]').checked, true);
  assert.match(ui.el('controlList').querySelector('label').textContent, /A\.5\.1.*suggested/s);
  assert.match(ui.el('suggestedControlsNote').textContent, /The Mitigator suggested 1 control/);
  next(ui);
  next(ui);
  await settle(() => state.posted.length === 1);
  const body = state.posted[0].body;
  assert.deepEqual(body.controls, ['c1']);
  assert.deepEqual(body.risk_types, ['Confidentiality']);
  assert.deepEqual(body.mitigator_context, { query: 'phishing payroll' });
  assert.deepEqual(body.interested_parties, [
    { vendor_id: 'v1' },
    { name: 'Payroll team', nature: 'Internal' },
  ]);
});

test('a draft naming an unknown asset proposes it as new, with the category it suggested', async (t) => {
  const storage = memoryStorage({
    'keen.riskWizardDraft': JSON.stringify({
      asset_name: 'Badge system',
      category_name: 'People',
      subcategory_name: 'Staff',
    }),
  });
  const { ui, state } = await wizard(t, { storage });
  next(ui);
  await tick();
  assert.equal(ui.el('assetSearch').value, 'Badge system');
  assert.equal(ui.el('newAssetCategory').value, 'People');
  next(ui);
  ui.el('skipStep').click();
  ui.el('riskTitle').value = 'Badge cloning';
  next(ui);
  next(ui);
  ui.el('skipStep').click();
  next(ui);
  await settle(() => state.posted.length === 1);
  assert.equal(state.posted[0].body.asset_name, 'Badge system');
  assert.equal(state.posted[0].body.category_id, 'cat2');
  assert.equal(state.posted[0].body.subcategory_id, 'sub2');
});

test('save failures keep the wizard on the review step and say why', async (t) => {
  const { ui, state } = await wizard(t, { postFail: true, assets: [] });
  next(ui);
  assert.match(ui.el('assetList').textContent, /No assets yet/);
  input(ui, 'assetSearch', 'Thing');
  await tick();
  input(ui, 'newAssetCategory', 'Information');
  input(ui, 'newAssetSubcategory', 'Databases');
  next(ui);
  ui.el('skipStep').click();
  ui.el('riskTitle').value = 'Thing breaks';
  next(ui);
  next(ui);
  ui.el('skipStep').click();
  next(ui);
  await settle(() => /Could not save: Error: Rejected/.test(ui.el('status').textContent));
  assert.equal(visibleStep(ui), 'review');
  assert.equal(ui.el('nextStep').disabled, false);
  assert.equal(state.posted[0].body.subcategory_id, 'sub1');
});

test('load problems and permissions are explained', async (t) => {
  const { ui } = await wizard(t, { me: { can_view_risks: true } });
  assert.match(ui.el('status').textContent, /You need risk.manage permission/);
  assert.equal(ui.el('wizardForm').hidden, true);
  const gone = await wizard(t, { query: '&id=r1', riskFail: true });
  assert.match(
    gone.ui.el('status').textContent,
    /Could not start the risk wizard: Error: Risk gone/,
  );
  // Without the PESTLE(E) feature the method is not offered and its data is not fetched.
  const noPestle = await wizard(t, {
    me: { is_admin: true, features: { enabled: { pestle: false, mitigator: false } } },
    registerFail: true,
  });
  assert.deepEqual(
    [...noPestle.ui.el('methodChoices').querySelectorAll('input')].map((i) => i.value),
    ['general', 'cia'],
  );
  assert.equal(noPestle.ui.el('wizardMitigatorLink').hidden, true);
  assert.ok(!noPestle.state.gets.some((g) => g.startsWith('/api/v1/pestle')));
});

test('a template asset the register lacks is typed in as new, with a category of its own', async (t) => {
  const { ui, state } = await wizard(t, { assets: [assets[1]] });
  ui.el('templateList').querySelector('[data-template="tpl1"]').click();
  next(ui);
  assert.equal(visibleStep(ui), 'subject');
  // The template's example asset is offered as the name of a new asset.
  assert.equal(ui.el('assetSearch').value, 'Payroll DB');
  input(ui, 'assetSearch', 'Payroll DB');
  await tick();
  assert.equal(ui.el('newAssetPanel').hidden, false);
  input(ui, 'newAssetCategory', 'Facilities');
  input(ui, 'newAssetSubcategory', 'Server rooms');
  assert.match(
    ui.el('selectedAssetSummary').textContent,
    /A new asset Payroll DB will be added under Facilities › Server rooms/,
  );
  next(ui);
  ui.el('skipStep').click();
  next(ui);
  next(ui);
  assert.equal(visibleStep(ui), 'controls');
  check(ui, '[data-control="c1"]');
  check(ui, '[data-control="c2"]');
  assert.equal(ui.el('controlCount').textContent, '2');
  check(ui, '[data-control="c2"]', false);
  assert.equal(ui.el('controlCount').textContent, '1');

  // Stepping back to the subject redraws it with the new asset still chosen.
  ui.document.querySelector('[data-goto-step="subject"]').click();
  assert.equal(visibleStep(ui), 'subject');
  assert.match(ui.el('selectedAssetSummary').textContent, /A new asset Payroll DB/);
  assert.equal(ui.el('assetList').querySelector('[data-asset="a1"]'), null);
  for (const step of ['parties', 'describe', 'rate', 'controls', 'review']) {
    next(ui);
    assert.equal(visibleStep(ui), step);
  }
  assert.match(reviewText(ui), /Controls A\.5\.1/);
  next(ui);
  await settle(() => state.posted.length === 1);
  const body = state.posted[0].body;
  assert.equal(body.asset_name, 'Payroll DB');
  assert.equal(body.category_name, 'Facilities');
  assert.equal('category_id' in body, false);
  assert.equal(body.subcategory_name, 'Server rooms');
  assert.deepEqual(body.controls, ['c1']);
});

test('a PESTLE(E) clause can be unticked, and a clause list that cannot load is empty', async (t) => {
  const { ui } = await wizard(t, { query: '&method=pestle' });
  next(ui);
  ui.el('skipStep').click();
  ui.el('riskDescription').value = 'Privacy law tightens retention';
  const legal = ui.document.querySelector('input[name="pestleType"][value="Legal"]');
  legal.checked = true;
  legal.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  next(ui);
  assert.equal(visibleStep(ui), 'rate');
  check(ui, '[data-clause="cl1"]');
  const level = ui.el('clauseList').querySelector('[data-clause-level="cl1"]');
  assert.equal(level.disabled, false);
  check(ui, '[data-clause="cl1"]', false);
  assert.equal(level.disabled, true);
  next(ui);
  assert.doesNotMatch(reviewText(ui), /Relevant clauses 6\.1\.2/);

  const offline = await wizard(t, { query: '&method=pestle', clausesFail: true });
  next(offline.ui);
  offline.ui.el('skipStep').click();
  offline.ui.el('riskDescription').value = 'Privacy law tightens retention';
  const type = offline.ui.document.querySelector('input[name="pestleType"][value="Legal"]');
  type.checked = true;
  type.dispatchEvent(new offline.ui.window.Event('change', { bubbles: true }));
  next(offline.ui);
  assert.equal(visibleStep(offline.ui), 'rate');
  assert.equal(offline.ui.el('clauseList').querySelector('[data-clause]'), null);
  assert.equal(offline.ui.el('status').textContent, '');
});
