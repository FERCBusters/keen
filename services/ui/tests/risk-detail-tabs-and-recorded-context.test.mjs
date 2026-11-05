// The risk detail page: how its tabs behave without a risk id or with Bootstrap
// present, and how it summarises the Mitigator context a risk was recorded
// with, whatever subset of that context an API client stored.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

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
    ...overrides,
  };
}

async function riskPage(t, { query = '&id=r1', record = risk(), setup } = {}) {
  const ui = await page(t, 'risk', {
    query,
    setup,
    get: async (u) => {
      if (u.pathname === '/api/v1/risks/r1') return structuredClone(record);
      if (u.pathname === '/api/v1/risks/r1/changelog') return { items: [] };
      if (u.pathname === '/api/v1/questions/entities/risk/r1') return { threads: [] };
      if (u.pathname === '/api/v1/isms/swot') return { items: [], total: 0 };
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.el('swotRelatedPane')?.textContent || ui.el('status').textContent);
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
