// How the ISMS register loads its sections: a section retried after a failed
// request, records that arrive after a refresh, actions taken while a section
// is still loading, SWOT pages, the
// People directory when it is switched off, deep links to measures that are
// gone or that the reader cannot record against, and filters that cannot be
// made collapsible.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  deferred,
  documentLink,
  documentRecord,
  measure,
  meeting,
  objective,
  register,
  settle,
  summaryResponse,
} from './support/isms-register-fixture.mjs';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('a section whose records failed to load fetches only its records when opened again', async (t) => {
  let failures = 1;
  const records = { documents: [documentRecord('d1', 'Security policy')] };
  const ui = await register(t, {
    tab: 'documents',
    records,
    get: (u) => {
      if (
        u.pathname === '/api/v1/isms/summary' &&
        u.searchParams.get('section') === 'documents' &&
        failures-- > 0
      )
        return new Error('Gateway timeout');
    },
  });
  await settle(() => /Gateway timeout/.test(ui.el('status').textContent));
  assert.doesNotMatch(ui.el('documentsRows').textContent, /Security policy/);
  ui.openTab('overview');
  ui.openTab('documents');
  await settle(() => ui.el('documentsRows').textContent.includes('Security policy'));
  assert.equal(ui.requests('/api/v1/isms/meta', 'documents').length, 1);
  assert.equal(ui.requests('/api/v1/isms/summary', 'documents').length, 2);
});

test('records that arrive after the register was refreshed are fetched again when shown', async (t) => {
  // Meetings name the documents they link to, so a document renamed while the
  // meetings were loading reaches them only through a fresh request.
  let title = 'Security policy';
  let slow = null;
  const meetingsNow = () =>
    summaryResponse('meetings', {
      meetings: [
        meeting('m1', 'Quarterly review', '2026-02-01', {
          links: [documentLink('l1', documentRecord('d1', title))],
        }),
      ],
    });
  const ui = await register(t, {
    tab: 'documents',
    get: (u) => {
      const section = u.searchParams.get('section');
      if (u.pathname === '/api/v1/isms/summary' && section === 'documents')
        return summaryResponse('documents', { documents: [documentRecord('d1', title)] });
      if (u.pathname === '/api/v1/isms/summary' && section === 'meetings') {
        if (!slow) return meetingsNow();
        const held = slow;
        slow = null;
        return held.promise;
      }
    },
    respond: (method, u, body) => {
      if (method === 'PATCH') title = body.title;
    },
  });
  await settle(() => ui.document.querySelector('[data-isms-edit="document"][data-isms-id="d1"]'));
  const early = deferred();
  slow = early;
  ui.openTab('meetings');
  await settle(() => ui.requests('/api/v1/isms/summary', 'meetings').length === 1);
  const before = meetingsNow();

  ui.openTab('documents');
  ui.click('[data-isms-edit="document"][data-isms-id="d1"]');
  ui.el('docTitle').value = 'Access policy';
  ui.submit('documentForm');
  await ui.statusIs('Document updated');
  early.resolve(before);
  await settle(() => ui.el('meetingsRows').textContent.includes('Security policy'));

  ui.openTab('meetings');
  await settle(() => ui.el('meetingsRows').textContent.includes('Access policy'));
  assert.equal(ui.requests('/api/v1/isms/summary', 'meetings').length, 2);
});

test('adding a record while its section loads waits for that same load', async (t) => {
  const slow = deferred();
  const ui = await register(t, {
    tab: 'objectives',
    get: (u) =>
      u.pathname === '/api/v1/isms/summary' && u.searchParams.get('section') === 'objectives'
        ? slow.promise
        : undefined,
  });
  await settle(() => ui.requests('/api/v1/isms/summary', 'objectives').length === 1);
  ui.click('[data-isms-add="objective"]');
  await sleep(20);
  assert.equal(ui.el('objectiveFormCard').style.display, 'none');
  slow.resolve(summaryResponse('objectives', { objectives: [objective('o1', 'Train operators')] }));
  await settle(() => ui.el('objectiveFormCard').style.display === '');
  assert.match(ui.el('objectivesRows').textContent, /Train operators/);
  assert.equal(ui.requests('/api/v1/isms/summary', 'objectives').length, 1);
  assert.equal(ui.requests('/api/v1/isms/meta', 'objectives').length, 1);
});

test('reopening SWOT while it loads asks once, and an empty SWOT counts no items', async (t) => {
  const slow = deferred();
  const ui = await register(t, {
    get: (u) => (u.pathname === '/api/v1/isms/swot' ? slow.promise : undefined),
  });
  await settle(() => ui.el('overviewCards').querySelector('svg'));
  ui.openTab('swot');
  ui.openTab('overview');
  ui.openTab('swot');
  await sleep(20);
  assert.equal(ui.requests('/api/v1/isms/swot').length, 1);
  slow.resolve({ items: [], total: 0 });
  await settle(() => ui.el('swotSummary').textContent === '0-0 of 0 items.');
  assert.equal(ui.el('swotNext').disabled, true);
  assert.equal(ui.el('swotPrevious').disabled, true);
});

test('with the People directory switched off, meetings load without it', async (t) => {
  const ui = await register(t, {
    tab: 'meetings',
    me: { is_admin: true, features: { enabled: { people: false } } },
    records: { meetings: [meeting('m1', 'Quarterly review', '2026-02-01')] },
  });
  await settle(() => ui.el('meetingsRows').textContent.includes('Quarterly review'));
  assert.equal(ui.requests('/api/v1/isms/people').length, 0);
  assert.equal(ui.el('meetingPersonAttendees').options.length, 0);
});

const measures = {
  effectiveness_measures: [
    measure('e1', 'Ticket age', { source_type: 'jira', target_unit: 'days' }),
  ],
};
const sources = [
  { id: 'jira', source: 'jira', label: 'Jira', color: '' },
  { id: 'other', source: 'other', label: 'Other', color: '' },
];

test('a link to a measure no longer in the register highlights nothing', async (t) => {
  const ui = await register(t, {
    query: '&measure=e9',
    records: measures,
    lists: { effectiveness_metric_sources: sources },
  });
  await settle(() => ui.el('effectivenessRows').querySelector('[data-isms-id="e1"]'));
  await sleep(220);
  assert.equal(ui.el('effectivenessTab').classList.contains('active'), true);
  assert.equal(ui.el('effectivenessRows').querySelector('.table-warning'), null);
});

test('a link to record a metric opens nothing for someone who cannot record', async (t) => {
  const ui = await register(t, {
    query: '&record=e1',
    me: { can_view_isms: true },
    records: measures,
    lists: { effectiveness_metric_sources: sources },
  });
  await settle(() => ui.el('effectivenessRows').querySelector('[data-isms-id="e1"]'));
  await sleep(220);
  assert.equal(ui.el('metricEntryFormCard').style.display, 'none');
});

test('a link to record against a missing measure starts from Other with no unit', async (t) => {
  const ui = await register(t, {
    query: '&record=e9',
    records: measures,
    lists: { effectiveness_metric_sources: sources },
  });
  await settle(() => ui.el('metricEntryFormCard').style.display === '');
  assert.equal(ui.el('metricMeasureId').value, '');
  assert.equal(ui.el('metricSourceType').value, 'other');
  assert.equal(ui.el('metricUnit').value, '');
});

test('the register still loads when its filters cannot be made collapsible', async (t) => {
  let win = null;
  let failed = false;
  const ui = await register(t, {
    tab: 'objectives',
    records: { objectives: [objective('o1', 'Train operators')] },
    setup: (window) => {
      win = window;
    },
    vendor: {
      // The design-system helper fails on the first filter sections it meets,
      // which are the ones this page adds.
      initCollapsibleFilterSections: () => {
        if (failed || !win.document.querySelector('[data-isms-table-filter]')) return;
        failed = true;
        throw new Error('Filter sections unavailable');
      },
    },
  });
  assert.equal(failed, true);
  await settle(() => ui.el('objectivesRows').textContent.includes('Train operators'));
  assert.ok(ui.el('objectivesTableFilter'));
  ui.el('objectivesTableFilter').value = 'nothing like it';
  ui.el('objectivesTableFilter').dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  assert.equal(ui.el('objectivesRows').querySelector('[data-isms-id="o1"]').style.display, 'none');
});
