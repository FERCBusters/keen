import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { page, settle } from './support/page-fixture.mjs';

const d3 = await readFile(new URL('./node_modules/d3/dist/d3.min.js', import.meta.url), 'utf8');
const plain = (v) => JSON.parse(JSON.stringify(v));

const templates = [
  {
    id: 's1',
    title: '<b>Quarterly</b> review',
    audit_type: 'external',
    framework_slug: 'A',
    schedule_next_run_date: '2026-04-01',
    schedule_recurrence: 'quarterly',
    schedule_interval: 2,
    schedule_until_date: '2027-12-31',
    attendees: [{}, {}],
  },
  {
    id: 's2',
    title: 'Annual',
    schedule_recurrence: 'yearly',
    schedule_date_rule: 'first_monday_of_month',
    schedule_anchor_month: 3,
  },
  {
    id: 's3',
    title: 'Odd',
    schedule_recurrence: 'fortnightly',
    schedule_date_rule: 'first_weekday_of_month',
    schedule_anchor_month: 99,
  },
];

const coverage = {
  framework_slug: 'A',
  start: '2026-01-01',
  end: '2026-12-31',
  source_audit_count: 3,
  scheduled_occurrence_count: 2,
  summary: {
    controls: { included: 2, gaps: 1, total: 3 },
    clauses: { included: 0, gaps: 2, total: 2 },
    documents: {},
  },
  included: {
    controls: [
      {
        id: 'c1',
        ref: 'A1',
        title: '<i>Access</i>',
        sources: Array.from({ length: 9 }, (_, i) => ({
          audit_id: 'a' + i,
          title: 'Audit ' + i,
          start_date: '2026-0' + ((i % 9) + 1) + '-01',
          source: i % 2 ? 'scheduled' : 'completed',
        })),
      },
      { id: 'c2', ref: 'A2', title: 'Backup', sources: [] },
    ],
    documents: [
      {
        id: 'd1',
        title: 'Policy',
        document_type: 'policy',
        status: 'approved',
        sources: [
          { id: 'a1', occurrence_date: '2026-05-01', title: 'Planned', source: 'scheduled' },
        ],
      },
    ],
  },
  gaps: {
    controls: [{ id: 'c3', ref: 'A3', title: 'Crypto', description: 'keys' }],
    clauses: [
      { id: 'k1', ref: '4.1', title: 'Context' },
      { id: 'k2', ref: '4.2', title: 'Parties' },
    ],
  },
};

async function auditsPage(t, options = {}) {
  const state = { posted: [], removed: [], scheduledQueries: [], coverageQueries: [] };
  const ui = await page(t, 'audits', {
    query: options.query || '',
    scripts: options.noD3 ? [] : [d3],
    me: options.me || { is_admin: true, user_id: 'u1' },
    globals: options.globals,
    vendor: options.vendor,
    get: async (u) => {
      const p = u.pathname;
      if (p === '/api/v1/audits/users') {
        if (options.usersFail) throw new Error('Users offline');
        return {
          items: [
            { id: 'u2', username: 'zed', email: 'zed@example.test' },
            { id: 'u3', username: 'amy' },
          ],
        };
      }
      if (p === '/api/v1/audits')
        return {
          items: [
            { id: 'a1', title: 'Annual', framework_slug: 'A', status: 'template' },
            {
              id: 'a2',
              title: 'Spring',
              status: 'in_progress',
              audit_type: 'external',
              scope_count: 3,
              document_scope_count: 1,
              clause_scope_count: 1,
              control_scope_count: 1,
            },
          ],
        };
      if (p === '/api/v1/audits/scheduled') {
        state.scheduledQueries.push(Object.fromEntries(u.searchParams));
        if (options.scheduledFail) throw new Error('<b>Schedules offline</b>');
        const start = u.searchParams.get('start');
        return {
          items: options.templates ?? templates,
          occurrences: [
            { date: start.slice(0, 8) + '10', title: 'Quarterly <b>review</b>' },
            ...Array.from({ length: 5 }, (_, i) => ({
              date: start.slice(0, 8) + '15',
              title: 'Busy ' + i,
            })),
          ],
        };
      }
      if (p === '/api/v1/isms/documents') {
        if (options.documentsFail) throw new Error('Docs offline');
        return {
          items: [
            {
              id: 'd1',
              title: 'Access policy',
              document_type: 'policy',
              clauses: [{ id: 'k41' }],
              controls: [{ id: 'c9' }],
            },
            { id: 'd2', title: 'Plan' },
          ],
        };
      }
      if (p === '/api/v1/controls')
        return {
          items: [
            { id: 'c1', ref: 'A1', title: 'Access' },
            { id: 'c9', ref: 'A9', title: 'Logging' },
            { id: 'k0', type: 'clause' },
          ],
        };
      if (p === '/api/v1/clauses')
        return {
          items: [
            { id: 'k4', ref: '4', title: 'Context' },
            { id: 'k41', ref: '4.1', title: 'Organisation' },
          ],
        };
      if (p === '/api/v1/graph/clause_control') {
        if (options.scopeFail) throw new Error('<i>Graph offline</i>');
        return {
          links: [
            { source: 'clause:k41', target: 'control:c1' },
            { source: '', target: 'x' },
          ],
        };
      }
      if (p === '/api/v1/audits/scope-coverage') {
        state.coverageQueries.push(Object.fromEntries(u.searchParams));
        if (options.coverageFail) throw new Error('Coverage offline');
        return coverage;
      }
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: plain(b) });
      if (options.postFail) throw new Error('Rejected');
      return { id: 'new', framework_slug: b.framework_slug };
    },
    remove: async (u) => {
      state.removed.push(u.pathname);
      if (options.removeFail) throw new Error('Delete refused');
      return { ok: true };
    },
  });
  await settle(
    () =>
      ui.el('scheduledRows').querySelector('td:not([colspan])') ||
      /No scheduled|Schedules offline/.test(ui.el('scheduledRows').textContent) ||
      !options.me?.is_admin,
  );
  await new Promise((r) => setTimeout(r, 20));
  return { ui, state };
}

test('audit register renders scope breakdowns and hides deletion for templates', async (t) => {
  const { ui } = await auditsPage(t);
  const rows = [...ui.el('rows').querySelectorAll('tr')];
  assert.equal(rows[0].querySelector('[data-delete-audit]'), null);
  assert.ok(rows[1].querySelector('[data-delete-audit="a2"]'));
  assert.match(rows[1].textContent, /External/);
  assert.match(rows[1].textContent, /1 documents, 1 clauses, 1 controls/);
  assert.match(rows[1].textContent, /In progress/);
  assert.match(rows[0].textContent, /Template/);
  ui.el('statusFilter').value = 'completed';
  ui.el('statusFilter').dispatchEvent(new ui.window.Event('change'));
  await settle(() => ui.calls.some((c) => c.url.includes('status=completed')));
});

test('scheduled templates list recurrence rules and a month calendar', async (t) => {
  const { ui, state } = await auditsPage(t);
  const rows = [...ui.el('scheduledRows').querySelectorAll('tr')];
  assert.equal(ui.el('scheduledRows').querySelector('b'), null);
  assert.match(rows[0].textContent, /Quarterly × 2[\s\S]*Until 2027-12-31/);
  assert.match(rows[0].textContent, /External · A/);
  assert.match(rows[1].textContent, /Yearly · first Monday of Mar/);
  assert.match(rows[2].textContent, /fortnightly · first weekday of month/);
  const calendar = ui.el('scheduleCalendar');
  assert.equal(calendar.querySelector('b'), null);
  assert.match(calendar.textContent, /Quarterly <b>review<\/b>/);
  assert.match(calendar.textContent, /\+1 more/);
  const month = ui.el('schedMonth').value;
  assert.equal(state.scheduledQueries[0].start, month + '-01');
  ui.el('schedNextMonth').click();
  await settle(() => state.scheduledQueries.length === 2);
  assert.notEqual(ui.el('schedMonth').value, month);
  ui.el('schedPrevMonth').click();
  await settle(() => state.scheduledQueries.length === 3);
  assert.equal(ui.el('schedMonth').value, month);
  // Deleting a template asks first.
  await settle(() => ui.document.querySelector('[data-delete-schedule="s1"]'));
  ui.document.querySelector('[data-delete-schedule="s1"]').click();
  await settle(() => ui.el('status').textContent === 'Scheduled audit template deleted');
  assert.deepEqual(state.removed, ['/api/v1/audits/scheduled/s1']);
});

test('scheduled template failures and empty lists are reported safely', async (t) => {
  const failed = await auditsPage(t, { scheduledFail: true });
  assert.match(failed.ui.el('scheduledRows').textContent, /<b>Schedules offline<\/b>/);
  assert.equal(failed.ui.el('scheduledRows').querySelector('b'), null);
  const empty = await auditsPage(t, { templates: [] });
  assert.match(empty.ui.el('scheduledRows').textContent, /No scheduled audit templates yet/);
});

test('creating a scheduled template collects fuzzy dates, attendees and documents', async (t) => {
  const { ui, state } = await auditsPage(t);
  await settle(() => ui.el('schedDocuments').options.length === 2);
  assert.match(
    ui.el('schedDocuments').options[0].textContent,
    /Access policy \(policy; 1 clauses, 1 controls\)/,
  );
  assert.deepEqual(
    [...ui.el('schedAttendeeUserId').options].map((o) => o.value),
    ['', 'u3', 'u2'],
  );
  ui.el('schedAddAttendee').click();
  assert.match(
    ui.el('status').textContent,
    /Choose a Keen user, or enter an attendee name or email first/,
  );
  ui.el('schedAttendeeUserId').value = 'u2';
  ui.el('schedAttendeeUserId').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('schedAttendeeName').value, 'zed');
  assert.equal(ui.el('schedAttendeeEmail').value, 'zed@example.test');
  ui.el('schedAttendeeRole').value = 'Lead';
  ui.el('schedAddAttendee').click();
  ui.el('schedAttendeeName').value = '<b>External</b> auditor';
  ui.el('schedAddAttendee').click();
  ui.el('schedAttendeeName').value = 'Temp';
  ui.el('schedAddAttendee').click();
  assert.equal(ui.el('schedAttendees').querySelectorAll('[data-sched-attendee-remove]').length, 3);
  assert.equal(ui.el('schedAttendees').querySelector('b'), null);
  ui.el('schedAttendees').querySelector('[data-sched-attendee-remove="2"]').click();
  // Fuzzy rules move a one-off schedule to yearly and disable weekly recurrence.
  ui.el('schedDateRule').value = 'first_friday_of_month';
  ui.el('schedFuzzyYear').value = '2026';
  ui.el('schedFuzzyMonth').value = '5';
  ui.el('schedFuzzyMonth').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('schedRecurrence').value, 'yearly');
  assert.equal(ui.el('schedRecurrence').querySelector('option[value="weekly"]').disabled, true);
  assert.equal(ui.el('schedFuzzyMonthLabel').textContent, 'First Friday of');
  // 1 May 2026 is a Friday.
  assert.equal(ui.el('schedStart').value, '2026-05-01');
  assert.match(
    ui.el('schedDatePreview').textContent,
    /First run will be 2026-05-01; future runs keep the first Friday rule/,
  );
  ui.el('schedTitle').value = '  Supplier review ';
  ui.el('schedDocuments').options[0].selected = true;
  ui.el('scheduleForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => state.posted.length === 1);
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/audits/scheduled',
    body: {
      title: 'Supplier review',
      framework_slug: 'A',
      audit_type: 'internal',
      start_date: '2026-05-01',
      end_date: null,
      schedule_recurrence: 'yearly',
      schedule_interval: 1,
      schedule_date_rule: 'first_friday_of_month',
      schedule_anchor_month: 5,
      schedule_until_date: null,
      report_notes: '',
      documents: ['d1'],
      attendees: [
        { user_id: 'u2', name: 'zed', email: 'zed@example.test', role: 'Lead' },
        { user_id: null, name: '<b>External</b> auditor', email: null, role: null },
      ],
    },
  });
  await settle(() => /Scheduled audit template created/.test(ui.el('status').textContent));
  assert.equal(ui.el('schedAttendees').textContent, 'No attendees added.');
});

test('exact schedules require a first run date', async (t) => {
  const { ui, state } = await auditsPage(t);
  ui.el('schedTitle').value = 'X';
  ui.el('schedStart').value = '';
  ui.el('scheduleForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Enter a valid first run date/.test(ui.el('status').textContent));
  assert.equal(state.posted.length, 0);
  ui.el('schedDateRule').value = 'first_weekday_of_month';
  ui.el('schedDateRule').dispatchEvent(new ui.window.Event('change'));
  ui.el('schedDateRule').value = 'exact';
  ui.el('schedDateRule').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('schedDatePreview').textContent, '');
  assert.equal(ui.el('schedExactDateFields').style.display, '');
  ui.el('scheduleReset').click();
  assert.equal(ui.el('schedTitle').value, '');
});

test('audit readers cannot create or delete audits or schedules', async (t) => {
  const { ui, state } = await auditsPage(t, { me: { can_view_audits: true } });
  await settle(() => ui.el('scheduledRows').querySelector('td:not([colspan])'));
  assert.ok(ui.el('showCreate').classList.contains('d-none'));
  assert.equal(ui.document.querySelector('[data-delete-schedule]'), null);
  assert.ok(!ui.calls.some((c) => c.url === '/api/v1/audits/users'));
  ui.el('scheduleForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /audits.manage permission required/.test(ui.el('status').textContent));
  assert.equal(state.posted.length, 0);
});

test('users without audit access see a permission message', async (t) => {
  const { ui } = await auditsPage(t, { me: {} });
  assert.match(ui.el('status').textContent, /audits.read permission required/);
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/audits?')));
});

test('scope coverage summarises included items and gaps with links and a gap matrix', async (t) => {
  const { ui, state } = await auditsPage(t, { query: '&tab=coverage' });
  await settle(() => ui.el('coverageIncludedRows').querySelector('a'));
  // Default window runs from 1 January to today.
  assert.equal(state.coverageQueries[0].start, `${new Date().getFullYear()}-01-01`);
  assert.equal(state.coverageQueries[0].mode, 'both');
  assert.match(
    ui.el('coverageSummary').textContent,
    /Audit scope entries analysed: 3 audit\/template records across 2 scheduled occurrences\. Window: 2026-01-01 → 2026-12-31\./,
  );
  const included = [...ui.el('coverageIncludedRows').querySelectorAll('tr')];
  assert.equal(included.length, 3);
  assert.equal(ui.el('coverageIncludedRows').querySelector('i'), null);
  assert.match(included[0].textContent, /\+1 more/);
  assert.match(
    included[0].querySelector('a').getAttribute('href'),
    /controls\.html\?selected=c1&framework=A/,
  );
  assert.match(included[2].textContent, /policy · approved[\s\S]*2026-05-01 Planned Scheduled/);
  assert.match(
    included[2].querySelector('a').getAttribute('href'),
    /isms\.html\?tab=documents&edit=document/,
  );
  const gaps = [...ui.el('coverageGapRows').querySelectorAll('tr')];
  assert.equal(gaps.length, 3);
  assert.match(gaps[1].querySelector('a').getAttribute('href'), /clauses\.html\?selected=k1/);
  assert.match(gaps[0].textContent, /Not included in selected window/);
  // The chart and matrix are drawn with D3.
  assert.equal(ui.el('coverageChart').querySelectorAll('g.coverage-row').length, 3);
  assert.match(ui.el('coverageChart').textContent, /67% included · 1 gaps/);
  assert.equal(
    ui.el('coverageGapMatrix').querySelectorAll('rect.gap-cell[fill="#dc3545"]').length,
    3,
  );
  assert.equal(
    ui.el('coverageGapMatrix').querySelectorAll('rect.gap-cell[fill="#198754"]').length,
    3,
  );
  // Search covers titles, descriptions and source details.
  ui.el('coverageQ').value = 'keys';
  ui.el('coverageQ').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('coverageGapRows').querySelectorAll('tr').length, 1);
  assert.match(ui.el('coverageIncludedRows').textContent, /No included scope entries match/);
  ui.el('coverageQ').value = 'audit 3';
  ui.el('coverageQ').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.el('coverageIncludedRows').querySelectorAll('tr').length, 1);
  assert.match(ui.el('coverageGapRows').textContent, /No gaps match/);
  ui.el('coverageMode').value = 'completed';
  ui.el('coverageMode').dispatchEvent(new ui.window.Event('change'));
  await settle(() => state.coverageQueries.at(-1)?.mode === 'completed');
});

test('scope coverage explains missing D3 and load failures', async (t) => {
  const noD3 = await auditsPage(t, { query: '&tab=coverage', noD3: true });
  await settle(() =>
    /D3 visualisations are unavailable/.test(noD3.ui.el('coverageChart').textContent),
  );
  const failed = await auditsPage(t, { coverageFail: true });
  failed.ui.el('coverage-tab').dispatchEvent(new failed.ui.window.Event('shown.bs.tab'));
  await settle(() => /Coverage offline/.test(failed.ui.el('coverageGapRows').textContent));
  assert.equal(failed.ui.el('coverageSummary').innerHTML, '');
});

test('attendee and document lookups degrade with warnings', async (t) => {
  const { ui } = await auditsPage(t, { usersFail: true, documentsFail: true });
  await settle(() => /Failed to load/.test(ui.el('status').textContent));
  assert.deepEqual(
    [...ui.el('schedAttendeeUserId').options].map((o) => o.value),
    [''],
  );
});

test('a refused audit or template deletion is reported and the row stays', async (t) => {
  const { ui, state } = await auditsPage(t, {
    removeFail: true,
    globals: { confirm: () => true },
  });
  ui.document.querySelector('[data-delete-audit="a2"]').click();
  await settle(() => ui.el('status').textContent === 'Failed: Error: Delete refused');
  assert.equal(ui.el('status').dataset.alert, 'danger');
  assert.match(ui.el('rows').textContent, /Spring/);
  ui.el('status').textContent = '';

  ui.document.querySelector('[data-delete-schedule="s1"]').click();
  await settle(() => ui.el('status').textContent === 'Failed: Error: Delete refused');
  assert.match(ui.el('scheduledRows').textContent, /Quarterly/);
  assert.deepEqual(state.removed, ['/api/v1/audits/a2', '/api/v1/audits/scheduled/s1']);
});

test('a schedule with a malformed end or until date is not sent, and a refused one is reported', async (t) => {
  const invalid = new Set();
  const { ui, state } = await auditsPage(t, {
    postFail: true,
    vendor: {
      wireIsoDateTextField: (input) => ({ syncHiddenFromText: () => !invalid.has(input.id) }),
    },
  });
  ui.el('schedTitle').value = 'Supplier review';
  ui.el('schedStart').value = '2026-05-01';
  for (const field of ['schedEnd', 'schedUntil']) {
    invalid.clear();
    invalid.add(field);
    ui.el('scheduleForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
    assert.equal(ui.el('status').textContent, 'Enter dates using the configured date format.');
    assert.equal(ui.el('status').dataset.alert, 'warning');
  }
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(state.posted.length, 0);

  invalid.clear();
  ui.el('scheduleForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('status').textContent === 'Failed: Error: Rejected');
  assert.equal(state.posted[0].path, '/api/v1/audits/scheduled');
  assert.equal(ui.el('schedTitle').value, 'Supplier review');
});

test('starting an audit opens the wizard, and older links to the register go there too', async (t) => {
  const { ui } = await auditsPage(t);
  assert.equal(ui.el('showCreate').getAttribute('href'), '/audit-wizard.html');
  assert.equal(ui.el('createCard'), null);

  const replaced = [];
  const location = { hash: '', replace: (url) => replaced.push(url) };
  await auditsPage(t, { query: '&new=1', globals: { location } });
  assert.deepEqual(replaced, ['/audit-wizard.html?framework=A']);

  const reader = { hash: '', replace: (url) => replaced.push(url) };
  await auditsPage(t, {
    query: '&new=1',
    me: { can_view_audits: true },
    globals: { location: reader },
  });
  assert.equal(replaced.length, 1, 'readers stay on the register');
});
