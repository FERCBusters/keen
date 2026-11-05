// The audits page beyond the paths in audits-workflows.test.mjs: scope
// coverage as the API reports it, schedules with features switched off, and
// the schedule form's fallbacks.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';
import { plain, wireIsoDateTextField } from './support/audit-pages.mjs';

const d3 = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');

// Coverage as get_audit_scope_coverage returns it for one completed audit.
const source = {
  id: 'a1',
  audit_id: 'a1',
  title: 'Spring audit',
  status: 'completed',
  source: 'completed',
  audit_type: 'internal',
  start_date: '2026-03-01',
  occurrence_date: null,
  end_date: '2026-03-05',
  framework_slug: 'A',
};
const coverage = {
  framework_slug: 'A',
  mode: 'both',
  start: '2026-01-01',
  end: '2026-12-31',
  source_audit_count: 1,
  scheduled_occurrence_count: 0,
  summary: {
    controls: { total: 2, included: 1, gaps: 1 },
    clauses: { total: 0, included: 0, gaps: 0 },
    documents: null,
  },
  included: {
    controls: [
      {
        id: 'c1',
        ref: 'A1',
        title: null,
        type: 'annex_control',
        label: 'A1',
        coverage_count: 1,
        sources: [source],
      },
    ],
    clauses: [],
    documents: [],
  },
  gaps: {
    controls: [
      {
        id: 'c2',
        ref: 'A2',
        title: 'Backup',
        type: 'annex_control',
        label: 'A2 Backup',
        coverage_count: 0,
        sources: [],
      },
    ],
    clauses: [],
    documents: [],
  },
};

const templates = [
  {
    id: 's1',
    title: 'Quarterly review',
    framework_slug: 'A',
    status: 'template',
    audit_type: 'internal',
    schedule_recurrence: 'quarterly',
    schedule_interval: 1,
    schedule_date_rule: 'exact',
    schedule_next_run_date: '2026-04-01',
    attendees: [],
  },
];

async function auditsPage(t, options = {}) {
  const state = { posted: [], removed: [] };
  const ui = await page(t, 'audits', {
    query: options.query || '',
    scripts: [d3],
    me: options.me || { is_admin: true, user_id: 'u1' },
    confirm: options.confirm,
    vendor: { wireIsoDateTextField, ...(options.vendor || {}) },
    get: async (u) => {
      switch (u.pathname) {
        case '/api/v1/audits/users':
          return { items: [{ id: 'u2', username: 'zed', email: 'zed@example.test' }] };
        case '/api/v1/audits':
          return { total: 0, items: [] };
        case '/api/v1/audits/scheduled':
          return { total: templates.length, items: templates, occurrences: [] };
        case '/api/v1/isms/documents':
          return { items: [] };
        case '/api/v1/audits/scope-coverage':
          if (options.coverageFail) throw new Error('Coverage <b>offline</b>');
          return coverage;
        default:
          throw new Error('Unexpected GET ' + u);
      }
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      return { id: 'new' };
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      return { ok: true };
    },
  });
  await settle(() => ui.idle());
  return { ui, state };
}

const gets = (ui, path) => ui.calls.filter((c) => c.method === 'GET' && c.url.startsWith(path));

test('coverage names an untitled control by its reference and leaves out scheduled runs it lacks', async (t) => {
  const { ui } = await auditsPage(t, { query: '&tab=coverage' });
  await settle(() => ui.el('coverageIncludedRows').querySelector('a'));
  assert.equal(
    ui.el('coverageSummary').lastElementChild.textContent,
    'Audit scope entries analysed: 1 audit/template records. Window: 2026-01-01 → 2026-12-31.',
  );
  const included = ui.el('coverageIncludedRows').querySelector('tr');
  assert.equal(included.querySelector('a.fw-semibold').textContent, 'A1');
  assert.match(included.textContent, /2026-03-01 Spring audit\s*Completed/);
  assert.deepEqual(
    [...ui.el('coverageGapMatrix').querySelectorAll('a.gap-cell-link')].map((a) =>
      a.getAttribute('aria-label'),
    ),
    ['Control: A1 - included', 'Control: A2 Backup - gap'],
  );
});

test('searching coverage after a failed load keeps the failure in view', async (t) => {
  const { ui } = await auditsPage(t, { query: '&tab=coverage', coverageFail: true });
  await settle(() => /offline/.test(ui.el('coverageGapRows').textContent));
  ui.el('coverageQ').value = 'backup';
  ui.el('coverageQ').dispatchEvent(new ui.window.Event('input'));
  for (const id of ['coverageIncludedRows', 'coverageGapRows'])
    assert.equal(ui.el(id).textContent.trim(), 'Coverage <b>offline</b>', id);
  assert.equal(ui.el('coverageSummary').innerHTML, '');
  assert.equal(ui.el('coverageGapRows').querySelector('b'), null);
});

test('people without audit access are not shown scope coverage', async (t) => {
  const { ui } = await auditsPage(t, { query: '&tab=coverage', me: { user_id: 'u5' } });
  assert.equal(ui.el('status').textContent, 'audits.read permission required.');
  assert.deepEqual(gets(ui, '/api/v1/audits/scope-coverage'), []);
});

test('deleting a scheduled template can be cancelled', async (t) => {
  const { ui, state } = await auditsPage(t, { confirm: false });
  await settle(() => ui.document.querySelector('[data-delete-schedule="s1"]'));
  ui.document.querySelector('[data-delete-schedule="s1"]').click();
  await settle(() => ui.confirmations.length === 1);
  assert.equal(ui.confirmations[0].title, 'Delete scheduled audit template');
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(state.removed, []);
  assert.match(ui.el('scheduledRows').textContent, /Quarterly review/);
});

test('with audit scheduling switched off no schedules are requested', async (t) => {
  const { ui } = await auditsPage(t, {
    me: { is_admin: true, features: { enabled: { audit_scheduling: false } } },
  });
  assert.deepEqual(gets(ui, '/api/v1/audits/scheduled'), []);
  assert.equal(ui.document.querySelector('[data-delete-schedule]'), null);
  // The audit register still loads.
  assert.equal(gets(ui, '/api/v1/audits?').length, 1);
});

test('the schedule form offers no documents with documents switched off or no framework chosen', async (t) => {
  const off = await auditsPage(t, {
    me: { is_admin: true, features: { enabled: { documents: false } } },
  });
  assert.deepEqual(gets(off.ui, '/api/v1/isms/documents'), []);
  assert.equal(off.ui.el('schedDocuments').options.length, 0);

  const none = await auditsPage(t, { vendor: { getCurrentFramework: () => '' } });
  assert.deepEqual(gets(none.ui, '/api/v1/isms/documents'), []);
  assert.equal(none.ui.el('schedDocuments').options.length, 0);
});

test('resetting the schedule form shows today as the first run and clears marked dates', async (t) => {
  const { ui } = await auditsPage(t);
  const today = new Date().toISOString().slice(0, 10).split('-').reverse().join('/');
  assert.equal(ui.el('schedStartText').value, today);
  for (const id of ['schedEndText', 'schedUntilText']) {
    const text = ui.el(id);
    text.value = 'soon';
    text.dispatchEvent(new ui.window.Event('change'));
    assert.equal(text.classList.contains('is-invalid'), true, id);
  }
  ui.el('schedStartText').value = '';
  ui.el('scheduleReset').click();
  assert.equal(ui.el('schedStartText').value, today);
  for (const id of ['schedEndText', 'schedUntilText']) {
    assert.equal(ui.el(id).value, '', id);
    assert.equal(ui.el(id).classList.contains('is-invalid'), false, id);
  }
});

test('scheduled attendees fall back to the account name and email, or to the email alone', async (t) => {
  const { ui, state } = await auditsPage(t);
  const userSelect = ui.el('schedAttendeeUserId');
  userSelect.value = 'u2';
  userSelect.dispatchEvent(new ui.window.Event('change'));
  ui.el('schedAttendeeName').value = '';
  ui.el('schedAttendeeEmail').value = '';
  ui.el('schedAddAttendee').click();
  ui.el('schedAttendeeEmail').value = 'guest@example.test';
  ui.el('schedAddAttendee').click();
  const rows = [...ui.el('schedAttendees').querySelectorAll('.list-group-item')].map((row) => [
    row.querySelector('.fw-semibold').textContent,
    row.querySelector('.small-muted').textContent,
  ]);
  assert.deepEqual(rows, [
    ['zed', 'zed@example.test'],
    ['guest@example.test', 'guest@example.test'],
  ]);
  ui.el('schedTitle').value = 'Supplier review';
  ui.el('schedInterval').value = '';
  ui.el('scheduleForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.posted.length === 1);
  const body = state.posted[0].body;
  assert.deepEqual(body.attendees, [
    { user_id: 'u2', name: 'zed', email: 'zed@example.test', role: null },
    { user_id: null, name: 'guest@example.test', email: 'guest@example.test', role: null },
  ]);
  // A cleared interval runs every period.
  assert.equal(body.schedule_interval, 1);
  await settle(() => ui.idle());
});
