// How the audit page shows and sends dates, finding kinds, evidence titles
// and attendees as the API records them.
import test from 'node:test';
import assert from 'node:assert/strict';
import { settle } from './support/page-fixture.mjs';
import {
  apiAudit,
  auditPage,
  change,
  eventEvidence,
  reloaded,
  submit,
} from './support/audit-pages.mjs';

const template = (overrides = {}) =>
  apiAudit({
    status: 'template',
    start_date: '2027-03-01',
    end_date: '2027-03-05',
    schedule_recurrence: 'yearly',
    schedule_next_run_date: '2027-03-01',
    schedule_until_date: '2027-12-31',
    ...overrides,
  });

test('the date fields show the audit and template dates in the configured format', async (t) => {
  const { ui } = await auditPage(t);
  assert.equal(ui.el('auditStartText').value, '01/01/2026');
  assert.equal(ui.el('auditEndText').value, '31/01/2026');

  const tmpl = await auditPage(t, { audit: template() });
  assert.equal(tmpl.ui.el('auditStartText').value, '01/03/2027');
  assert.equal(tmpl.ui.el('tmplScheduleStartText').value, '01/03/2027');
  assert.equal(tmpl.ui.el('tmplUntilText').value, '31/12/2027');
});

test('after another section is saved, each date field shows the date it holds', async (t) => {
  const { ui, state } = await auditPage(t, { audit: template() });
  // Text the date field cannot read leaves its date unchanged.
  for (const id of ['auditStartText', 'auditEndText', 'tmplScheduleStartText', 'tmplUntilText'])
    change(ui, ui.el(id), { value: 'next week' });
  assert.equal(ui.el('auditStart').value, '2027-03-01');
  ui.el('auditNotes').value = '<p>Kept</p>';
  submit(ui, 'reportNotesForm');
  await settle(() => state.patched.length === 1);
  await reloaded(ui);
  assert.deepEqual(
    ['auditStartText', 'auditEndText', 'tmplScheduleStartText', 'tmplUntilText'].map(
      (id) => ui.el(id).value,
    ),
    ['01/03/2027', '05/03/2027', '01/03/2027', '31/12/2027'],
  );
  assert.equal(ui.el('auditNotes').value, '<p>Kept</p>');
});

test('an audit without dates says so and saves its dates as empty', async (t) => {
  const { ui, state } = await auditPage(t, {
    audit: apiAudit({ start_date: null, end_date: null }),
  });
  assert.match(ui.el('meta').textContent, /^A · Internal · Open · No dates set · Author: alice$/);
  assert.equal(ui.el('auditStartText').value, '');
  submit(ui, 'detailsForm');
  await settle(() => state.patched.length === 1);
  assert.equal(state.patched[0].body.start_date, null);
  assert.equal(state.patched[0].body.end_date, null);
  await reloaded(ui);
});

test('each kind of finding is named in full', async (t) => {
  const finding = (id, kind, status) => ({
    id,
    audit_id: 'a1',
    kind,
    title: `Finding ${id}`,
    description: '',
    status,
    control: null,
  });
  const { ui } = await auditPage(t, {
    audit: apiAudit({
      findings: [
        finding('f1', 'minor_nc', 'open'),
        finding('f2', 'ofi', 'accepted'),
        finding('f3', 'best_practice', 'closed'),
      ],
    }),
  });
  const badges = [...ui.el('findings').querySelectorAll('[data-finding-card]')].map((card) =>
    [...card.querySelectorAll('.badge')].map((b) => b.textContent),
  );
  assert.deepEqual(badges, [
    ['Minor non-conformity', 'Open'],
    ['Opportunity for improvement', 'Accepted'],
    ['Best practice', 'Closed'],
  ]);
});

test('evidence without a title is named by its link, or as evidence', async (t) => {
  const quiet = eventEvidence('e1', 'ev1', { title: null });
  quiet.event.summary = '';
  const link = {
    ...eventEvidence('e2', null, { title: null }),
    event_id: null,
    event: null,
    evidence_url: 'https://evidence.example/<b>report</b>',
  };
  const { ui } = await auditPage(t, { audit: apiAudit({ evidence: [quiet, link] }) });
  const titles = [...ui.el('evidence').querySelectorAll('a.fw-semibold')].map((a) => [
    a.textContent,
    a.getAttribute('href'),
  ]);
  assert.deepEqual(titles, [
    ['Evidence', '/event.html?id=ev1&framework=A'],
    ['https://evidence.example/<b>report</b>', 'https://evidence.example/%3Cb%3Ereport%3C/b%3E'],
  ]);
  assert.equal(ui.el('evidence').querySelector('b'), null);
});

test('a template schedule with its interval cleared runs every period', async (t) => {
  const { ui, state } = await auditPage(t, { audit: template() });
  ui.el('tmplInterval').value = '';
  submit(ui, 'templateScheduleForm');
  await settle(() => state.patched.length === 1);
  assert.equal(state.patched[0].path, '/api/v1/audits/scheduled/a1');
  assert.equal(state.patched[0].body.schedule_interval, 1);
  await reloaded(ui);
});

test('a person attendee is sent by reference, with the details left empty sent as null', async (t) => {
  const { ui, state } = await auditPage(t, {
    responses: {
      '/api/v1/isms/people': {
        items: [{ id: 'p2', name: 'Erin', email: '', username: null, user_id: null }],
      },
    },
  });
  change(ui, ui.el('attendeePersonId'), { value: 'p2' });
  assert.equal(ui.el('attendeeName').value, 'Erin');
  ui.el('attendeeName').value = '';
  submit(ui, 'attendeeForm');
  await settle(() => state.posted.length === 1);
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/audits/a1/attendees',
    body: { user_id: null, person_id: 'p2', name: null, email: null, role: null },
  });
  await reloaded(ui);
});

test('a Keen account attendee keeps the account email when the email field is cleared', async (t) => {
  const { ui, state } = await auditPage(t);
  change(ui, ui.el('attendeeUserId'), { value: 'u2' });
  assert.equal(ui.el('attendeeEmail').value, 'zed@example.test');
  ui.el('attendeeEmail').value = '';
  submit(ui, 'attendeeForm');
  await settle(() => state.posted.length === 1);
  assert.deepEqual(state.posted[0].body, {
    user_id: 'u2',
    person_id: null,
    name: 'zed',
    email: 'zed@example.test',
    role: null,
  });
  await reloaded(ui);
});
