// Keen Mitigator: the natural-language starting point. It lays out existing
// risks first, then library templates, then the makings of a new risk with its
// suggested controls plainly labelled as controls, and hands a draft to the
// risk wizard.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (v) => JSON.parse(JSON.stringify(v));

const categories = [
  { id: 'cat1', name: 'Information', subcategories: [{ id: 'sub1', name: 'Databases' }] },
];
const analysis = {
  framework: 'A',
  signals: [{ kind: 'phishing', label: 'Phishing', reason: 'mentions phishing' }],
  suggestions: [
    {
      control: { id: 'c1', ref: 'A.5.1', title: 'Policies', in_scope: true },
      confidence: 'strong',
      reasons: ['Matches "<b>phishing</b>"'],
      matched_terms: ['phishing'],
    },
    {
      control: { id: 'c2', ref: 'A.8.13', title: 'Backup', in_scope: false },
      confidence: 'weak',
      reasons: [],
      matched_terms: [],
    },
  ],
  library_suggestions: [
    {
      id: 'tpl1',
      name: 'Phishing <i>campaign</i>',
      risk_types: ['Confidentiality'],
      threat_summary: 'Staff are phished',
      reason: 'Shares the word phishing.',
      control_refs: ['A.5.1'],
      suggested_assessment: { inherent_likelihood: 4, inherent_impact: 3 },
    },
  ],
  pestle_hits: [{ type: 'Legal' }],
  interested_party_suggestions: [
    { name: 'Payroll team', nature: 'Internal', reason: 'Named in the description' },
    { name: 'Customers', nature: 'Customer', reason: 'Already recorded', duplicate_exact: true },
  ],
  existing_matches: {
    cia_risks: [
      {
        id: 'r1',
        title: 'Payroll phishing',
        display: 'Payroll phishing',
        asset_name: 'Payroll DB',
        risk_types: ['Confidentiality'],
        residual_risk_score: 4,
        threat_summary: 'Staff logins are phished',
      },
    ],
    pestle_items: [
      {
        id: 'pi1',
        item: 'Privacy law tightens',
        type: 'Legal',
        lens: 'External',
        confidence: 'medium',
        matched_terms: ['privacy'],
        clauses: [{ clause: { ref: '6.1.2' } }],
      },
    ],
    interested_parties: [{ id: 'ip1', label: 'Customers' }],
  },
  draft_risk: {
    asset_name: 'Payroll DB',
    category_name: 'Information',
    subcategory_name: 'Databases',
    risk_types: ['Confidentiality'],
    threat_summary: 'Payroll staff are phished for their credentials',
    note: 'From the Mitigator',
    mitigator_context: { query_terms: ['phishing'] },
  },
  draft_pestle_item: { item: 'Phishing regulation', type: 'Legal', lens: 'External' },
};

function memoryStorage() {
  const data = {};
  return {
    getItem: (k) => data[k] ?? null,
    setItem: (k, v) => (data[k] = String(v)),
    removeItem: (k) => delete data[k],
    data,
  };
}

async function mitigator(t, options = {}) {
  const state = { posted: [] };
  const storage = memoryStorage();
  const location = {
    origin: 'https://keen.example',
    pathname: '/mitigator.html',
    search: '?framework=A',
    href: 'https://keen.example/mitigator.html?framework=A',
    hash: '',
  };
  const ui = await page(t, 'mitigator', {
    me: options.me || { is_admin: true },
    globals: { sessionStorage: storage, location },
    get: async (u) => {
      if (u.pathname === '/api/v1/risks/categories') {
        if (options.categoriesFail) throw new Error('Categories offline');
        return { items: categories };
      }
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (options.postFail) throw new Error('Analyser offline');
      return options.analysis ?? analysis;
    },
  });
  await settle(() => ui.el('mitigatorCategory').options.length > 1 || ui.el('status').textContent);
  return { ui, state, storage, location };
}

const analyse = async (ui, state, text = 'Payroll staff get phished. Then payslips change.') => {
  ui.el('mitigatorIssue').value = text;
  ui.el('mitigatorForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.posted.length > 0 && ui.el('mitigatorThinking').hidden);
};

test('an analysis is laid out as existing matches, templates, then a new risk with controls labelled as controls', async (t) => {
  const { ui, state } = await mitigator(t);
  assert.deepEqual(
    [...ui.el('mitigatorCategory').options].map((o) => o.textContent),
    ['Not sure yet', 'Information'],
  );
  assert.equal(ui.el('mitigatorSubcategory').disabled, true);
  ui.el('mitigatorCategory').value = 'cat1';
  ui.el('mitigatorCategory').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('mitigatorSubcategory').disabled, false);
  ui.el('mitigatorSubcategory').value = 'sub1';
  ui.el('mitigatorAsset').value = 'Payroll DB';
  ui.el('mitigatorImpactSensitive').checked = true;
  ui.el('mitigatorWeightC').value = '80';
  ui.el('mitigatorWeightC').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('mitigatorWeightCValue').textContent, '80');

  // An empty description is caught before any request.
  ui.el('mitigatorForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  assert.equal(ui.el('status').textContent, 'Describe the potential issue first');
  assert.equal(state.posted.length, 0);

  await analyse(ui, state);
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/risks/mitigator/analyse',
    body: {
      issue: 'Payroll staff get phished. Then payslips change.',
      asset_name: 'Payroll DB',
      category_id: 'cat1',
      category_name: 'Information',
      subcategory_id: 'sub1',
      subcategory_name: 'Databases',
      risk_types: ['Confidentiality'],
      risk_weights: { Confidentiality: 80, Integrity: 40, Availability: 40 },
      impact_flags: {
        sensitive_data: true,
        data_corruption: false,
        service_outage: false,
        financial: false,
        brand: false,
        supplier: false,
        compliance: false,
      },
      framework: 'A',
      limit: 10,
    },
  });
  assert.equal(ui.el('status').textContent, 'Keen Mitigator analysis complete');
  assert.equal(ui.el('mitigatorResultsCard').style.display, '');
  assert.equal(
    ui.el('mitigatorResultsMeta').textContent,
    '2 existing matches, 1 template and 2 suggested controls for A.',
  );
  assert.match(ui.el('mitigatorSignals').textContent, /Phishing/);

  const sections = ui.el('mitigatorSuggestions').querySelectorAll('section.mitigator-choice');
  assert.equal(sections.length, 3);
  assert.deepEqual(
    [...sections].map((s) => s.querySelector('h2').textContent),
    ['Is it already covered?', 'Start from a template', 'Or create something new'],
  );
  // 1. Existing risks and factors open in place rather than being recreated.
  assert.match(
    sections[0].textContent,
    /Payroll phishing.*Payroll DB.*Confidentiality.*after controls.*4/s,
  );
  assert.equal(
    sections[0].querySelector('a[href="/risk.html?id=r1&framework=A"]').textContent,
    'Open this risk',
  );
  assert.match(
    sections[0].textContent,
    /Privacy law tightens.*Legal · External · clauses 6\.1\.2.*Medium match/s,
  );
  assert.ok(
    sections[0].querySelector('a[href="/pestle_item.html?id=pi1&tab=details&framework=A"]'),
  );
  assert.ok(sections[0].querySelector('a[href="/interested_party.html?id=ip1&framework=A"]'));
  assert.ok(sections[0].classList.contains('border-warning-subtle'));
  // 2. Templates are offered with their example rating and the reason they matched.
  assert.equal(sections[1].querySelector('i'), null);
  assert.match(
    sections[1].textContent,
    /Phishing <i>campaign<\/i>.*Shares the word phishing\. Suggested controls here: A\.5\.1\..*Example rating: 4 × 3/s,
  );
  assert.ok(sections[1].querySelector('[data-use-template="tpl1"]'));
  // 3. Controls are called safeguards and badged so nobody reads them as risks.
  assert.match(
    sections[2].textContent,
    /Safeguards the new risk could link to\s*framework controls/,
  );
  const boxes = sections[2].querySelectorAll('.mitigator-suggestion-select');
  assert.deepEqual(
    [...boxes].map((b) => [b.value, b.checked]),
    [
      ['c1', true],
      ['c2', true],
    ],
  );
  assert.match(sections[2].textContent, /Out of scope - review applicability/);
  assert.equal(sections[2].querySelector('b'), null);
  assert.match(sections[2].textContent, /Matches "<b>phishing<\/b>"/);
  const parties = sections[2].querySelectorAll('.mitigator-interested-select');
  assert.deepEqual(
    [...parties].map((b) => [b.checked, b.disabled]),
    [
      [true, false],
      [false, true],
    ],
  );
  assert.match(sections[2].textContent, /Customers - Customer \(already recorded\)/);
  assert.match(sections[2].textContent, /This also reads like a PESTLE\(E\) influence: Legal/);
  assert.ok(ui.el('mitigatorToWizard'));
  assert.ok(ui.el('mitigatorToPestleWizard'));

  // Clearing puts the form back to its defaults.
  ui.el('mitigatorClear').click();
  assert.equal(ui.el('mitigatorIssue').value, '');
  assert.equal(ui.el('mitigatorResultsCard').style.display, 'none');
  assert.equal(ui.el('mitigatorWeightCValue').textContent, '60');
  assert.equal(ui.el('mitigatorRtC').checked, true);
  assert.equal(ui.el('mitigatorImpactSensitive').checked, false);
});

test('the wizard receives the ticked controls and parties as a draft', async (t) => {
  const { ui, state, storage, location } = await mitigator(t);
  await analyse(ui, state);
  const backup = ui.document.querySelector('.mitigator-suggestion-select[value="c2"]');
  backup.checked = false;
  ui.el('mitigatorToWizard').click();
  assert.equal(location.href, '/risk-wizard.html?framework=A');
  const draft = JSON.parse(storage.getItem('keen.riskWizardDraft'));
  assert.deepEqual(draft, {
    method: 'cia',
    title: 'Payroll staff get phished',
    threat_summary: 'Payroll staff are phished for their credentials',
    asset_name: 'Payroll DB',
    category_name: 'Information',
    subcategory_name: 'Databases',
    risk_types: ['Confidentiality'],
    note: 'From the Mitigator',
    controls: ['c1'],
    interested_parties: [{ name: 'Payroll team', nature: 'Internal' }],
    mitigator_context: { query_terms: ['phishing'], selected_control_ids: ['c1'] },
    template_id: '',
  });
});

test('a template or a PESTLE(E) factor can be chosen instead', async (t) => {
  const { ui, state, storage, location } = await mitigator(t);
  await analyse(ui, state);
  ui.document.querySelector('[data-use-template="tpl1"]').click();
  assert.equal(location.href, '/risk-wizard.html?template=tpl1&framework=A');
  assert.equal(JSON.parse(storage.getItem('keen.riskWizardDraft')).template_id, 'tpl1');
  ui.el('mitigatorToPestleWizard').click();
  assert.equal(location.href, '/risk-wizard.html?method=pestle&framework=A');
  assert.deepEqual(JSON.parse(storage.getItem('keen.riskWizardDraft')), {
    method: 'pestle',
    threat_summary: 'Phishing regulation',
    pestle_type: 'Legal',
    lens: 'External',
  });
});

test('an empty analysis says nothing matched and falls back to the description', async (t) => {
  const { ui, state, storage } = await mitigator(t, {
    analysis: {
      framework: 'A',
      signals: [],
      suggestions: [],
      library_suggestions: [],
      existing_matches: {},
      draft_risk: {},
    },
  });
  ui.el('mitigatorRtC').checked = false;
  await analyse(ui, state, 'Something vague');
  assert.equal(
    ui.el('mitigatorResultsMeta').textContent,
    '0 existing matches, 0 templates and 0 suggested controls for A.',
  );
  assert.match(ui.el('mitigatorSignals').textContent, /no particular signals/);
  const sections = ui.el('mitigatorSuggestions').querySelectorAll('section.mitigator-choice');
  assert.equal(sections[0].querySelector('h2').textContent, 'Nothing like this exists yet');
  assert.match(sections[1].textContent, /No library template matched/);
  assert.match(sections[2].textContent, /No controls matched strongly enough/);
  assert.equal(ui.el('mitigatorToPestleWizard'), null);
  ui.el('mitigatorToWizard').click();
  const draft = JSON.parse(storage.getItem('keen.riskWizardDraft'));
  assert.equal(draft.method, 'general');
  assert.equal(draft.threat_summary, 'Something vague');
  assert.deepEqual(draft.controls, []);
});

test('failures and permissions are explained', async (t) => {
  const failed = await mitigator(t, { postFail: true, categoriesFail: true });
  assert.match(failed.ui.el('status').textContent, /Could not load asset categories/);
  await analyse(failed.ui, failed.state);
  assert.match(
    failed.ui.el('status').textContent,
    /Failed to analyse mitigations: Analyser offline/,
  );
  assert.equal(failed.ui.el('mitigatorAnalyse').disabled, false);

  const reader = await mitigator(t, { me: { can_view_risks: true } });
  await analyse(reader.ui, reader.state);
  assert.equal(reader.ui.document.querySelector('[data-use-template]'), null);
  assert.equal(reader.ui.el('mitigatorToWizard'), null);
  assert.match(
    reader.ui.el('mitigatorSuggestions').textContent,
    /You need risk.manage permission to create risks/,
  );

  const nobody = await mitigator(t, { me: {} });
  assert.match(
    nobody.ui.el('status').textContent,
    /You need risk.read permission to use Keen Mitigator/,
  );
  assert.equal(nobody.ui.el('mitigatorAnalyse').disabled, true);
  assert.equal(nobody.state.posted.length, 0);
});
