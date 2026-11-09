// ISMS detail pages with records as the API serves them: assets with their
// owner and risks, objectives for managers and readers, documents without an
// upload, access rows whose service was deleted, and controls without a title.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';
import { designSystemDates } from './support/design-system-dates.mjs';
import {
  accessRecord,
  assetRecord,
  control,
  documentRecord,
  licence,
  measure,
  metricEntry,
  objective,
  orgNode,
} from './support/isms-register-fixture.mjs';

const hostile = '<img src=x onerror=alert(1)>';

// One detail page with its record, change history and related panels. Dates
// are formatted as the design system formats them.
async function detail(t, name, { path, row, me = { is_admin: true }, risks, query = '&id=x1' }) {
  let win;
  const shown = [];
  const dates = designSystemDates(() => win);
  const ui = await page(t, name, {
    query,
    me,
    vendor: { fmtTs: dates.fmtTs, isoDateToDisplay: dates.isoDateToDisplay },
    setup(window) {
      win = window;
      window.mospDefaultTimezone = 'UTC';
      window.bootstrap = {
        Tab: { getOrCreateInstance: (el) => ({ show: () => shown.push(el.id) }) },
      };
    },
    get: async (u) => {
      if (u.pathname === path) return structuredClone(row);
      if (u.pathname.endsWith('/changelog')) return { items: [] };
      if (u.pathname === '/api/v1/isms/swot') return { items: [], total: 0 };
      if (u.pathname === '/api/v1/sources') return { items: [] };
      if (risks && u.pathname === '/api/v1/risks') return risks(u);
      throw new Error('Unexpected GET ' + u);
    },
  });
  await settle(() => ui.calls.some((c) => c.url.startsWith(path)));
  await new Promise((resolve) => setTimeout(resolve, 30));
  return { ui, shown };
}

const riskRow = (id, title, fields = {}) => ({
  id,
  title,
  risk_types: [],
  treatment_strategy: 'mitigate',
  treatment_status: 'open',
  treatment_due_at: null,
  risk_score: 12,
  residual_risk_score: 6,
  risk_owner: null,
  ...fields,
});

test('an asset names its owner and its licence', async (t) => {
  const row = assetRecord('x1', 'Payroll', {
    license_id: 'l1',
    license: 'Enterprise',
    license_name: 'Enterprise',
    license_entity: licence('l1', 'Enterprise'),
    owner_org_node: orgNode('n1', 'Finance'),
    owner_org_node_id: 'n1',
  });
  const { ui } = await detail(t, 'isms-asset', { path: '/api/v1/isms/assets/x1', row });
  assert.equal(
    ui.el('assetMeta').textContent,
    'Data · Databases · Enterprise · updated 2026-03-01 09:00:00',
  );
  assert.equal(ui.el('assetSubcategory').textContent, 'Databases');
  assert.equal(ui.el('assetOwner').textContent, 'Finance');
});

test("an asset's risks show only the treatment details they have, and load once", async (t) => {
  const asked = [];
  const { ui } = await detail(t, 'isms-asset', {
    path: '/api/v1/isms/assets/x1',
    row: assetRecord('x1', 'Payroll', { risk_count: 2 }),
    me: { can_view_isms: true, can_view_risks: true },
    risks: (u) => {
      asked.push(u.search);
      return {
        items: [
          riskRow('r1', 'Unpatched ' + hostile),
          riskRow('r2', 'Leaked payslips', {
            risk_types: ['Confidentiality'],
            treatment_due_at: '2026-12-01',
          }),
        ],
      };
    },
  });
  ui.el('assetRisksTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('assetRisks').querySelectorAll('tbody tr').length === 2);
  const [plainRisk, fullRisk] = ui.el('assetRisks').querySelectorAll('tbody tr');
  assert.equal(plainRisk.querySelector('td').querySelectorAll('div').length, 0);
  assert.equal(
    plainRisk.querySelectorAll('td')[1].querySelector('.small-muted').textContent,
    'Open',
  );
  assert.equal(plainRisk.querySelector('a').textContent, 'Unpatched ' + hostile);
  assert.equal(ui.el('assetRisks').querySelector('img'), null);
  assert.equal(fullRisk.querySelector('td div .badge').textContent, 'Confidentiality');
  assert.equal(
    fullRisk.querySelectorAll('td')[1].querySelector('.small-muted').textContent,
    'Open · due 2026-12-01',
  );
  ui.el('assetRisksTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(asked.length, 1);
});

test("an asset's risks that cannot load say so in the tab", async (t) => {
  const { ui } = await detail(t, 'isms-asset', {
    path: '/api/v1/isms/assets/x1',
    row: assetRecord('x1', 'Payroll'),
    me: { can_view_isms: true, can_manage_risks: true },
    risks: () => {
      throw new Error('Risk register offline');
    },
  });
  ui.el('assetRisksTab').dispatchEvent(new ui.window.Event('shown.bs.tab'));
  await settle(() => ui.el('assetRisks').textContent.includes('could not be loaded'));
  assert.equal(ui.el('assetRisks').textContent, 'Risks could not be loaded: Risk register offline');
});

test('an asset page follows its address fragment to the tabs the reader has', async (t) => {
  const reader = await detail(t, 'isms-asset', {
    path: '/api/v1/isms/assets/x1',
    row: assetRecord('x1', 'Payroll'),
    me: { can_view_isms: true, can_view_risks: true },
    risks: () => ({ items: [] }),
  });
  for (const hash of ['#risks', '#changelog']) {
    reader.ui.window.location.hash = hash;
    reader.ui.window.dispatchEvent(new reader.ui.window.HashChangeEvent('hashchange'));
  }
  assert.deepEqual(reader.shown, ['assetRisksTab', 'assetChangelogTab']);

  const withoutRisks = await detail(t, 'isms-asset', {
    path: '/api/v1/isms/assets/x1',
    row: assetRecord('x1', 'Payroll'),
    me: { can_view_isms: true },
  });
  for (const hash of ['#risks', '#changelog']) {
    withoutRisks.ui.window.location.hash = hash;
    withoutRisks.ui.window.dispatchEvent(new withoutRisks.ui.window.HashChangeEvent('hashchange'));
  }
  assert.deepEqual(withoutRisks.shown, ['assetChangelogTab']);
});

for (const [who, me, editable] of [
  ['an ISMS manager', { can_manage_isms: true }, true],
  ['a risk manager', { can_manage_risks: true }, true],
  ['an ISMS reader', { can_view_isms: true }, false],
]) {
  test(`${who} ${editable ? 'is offered' : 'is not offered'} an objective's edit link`, async (t) => {
    const { ui } = await detail(t, 'isms-objective', {
      path: '/api/v1/isms/objectives/x1',
      row: objective('x1', 'Train operators'),
      me,
    });
    assert.equal(ui.el('objectiveTitle').textContent, 'Train operators');
    assert.equal(ui.el('editObjectiveLink').style.display, editable ? '' : 'none');
    assert.equal(
      ui.el('editObjectiveLink').getAttribute('href'),
      '/isms.html?tab=objectives&edit=objective%3Ax1&framework=A',
    );
    // Sampling needs audit management, which none of these accounts has.
    assert.equal(ui.el('sampleObjectiveAudit').style.display, 'none');
  });
}

test('a document without an upload shows no upload time, and an unnamed upload links as Uploaded file', async (t) => {
  const written = await detail(t, 'isms-document', {
    path: '/api/v1/isms/documents/x1',
    row: documentRecord('x1', 'Access policy', { content_html: '<p>Who may access what</p>' }),
  });
  assert.equal(written.ui.el('documentUploadedAt').textContent, '-');
  assert.equal(written.ui.el('documentFile').textContent, '-');

  const uploaded = await detail(t, 'isms-document', {
    path: '/api/v1/isms/documents/x1',
    row: documentRecord('x1', 'Scanned register', {
      has_file: true,
      filename: '',
      uploaded_at: '2026-03-02T10:00:00',
    }),
  });
  assert.equal(uploaded.ui.el('documentUploadedAt').textContent, '2026-03-02 10:00:00');
  const link = uploaded.ui.el('documentFile').querySelector('a');
  assert.equal(link.textContent, 'Uploaded file');
  assert.equal(link.getAttribute('href'), '/api/v1/isms/documents/x1/file');
});

test('an access row whose service was deleted shows a dash for its service', async (t) => {
  const { ui } = await detail(t, 'isms-access', {
    path: '/api/v1/isms/access-control-matrix/x1',
    row: accessRecord('x1', 'Rotate keys', null, [orgNode('n1', 'DBA')]),
  });
  assert.equal(ui.el('accessService').innerHTML, '-');
  assert.equal(ui.el('accessMeta').textContent, 'updated 2026-03-01 09:00:00');
  assert.equal(ui.el('accessRoles').textContent, 'DBA');
});

test('a measure control without a title is described by its reference', async (t) => {
  const parent = measure('m1', 'Ticket age', {
    controls: [control('c1', 'A.5.1', 'Policies'), control('c2', 'A.9.9', null)],
  });
  const { ui } = await detail(t, 'isms-effectiveness-metric', {
    path: '/api/v1/isms/effectiveness-metrics/x1',
    row: { ...metricEntry('x1', 'm1', { metric_value: 3, value_display: '3' }), measure: parent },
  });
  assert.deepEqual(
    [...ui.el('metricControls').querySelectorAll('a')].map((a) => [
      a.textContent,
      a.getAttribute('title'),
      a.getAttribute('href'),
    ]),
    [
      ['A.5.1', 'Policies', '/control.html?id=c1&framework=A'],
      ['A.9.9', 'A.9.9', '/control.html?id=c2&framework=A'],
    ],
  );
});
