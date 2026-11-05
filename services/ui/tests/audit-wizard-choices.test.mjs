// The new-audit wizard's filters, attendee choices and review beyond the
// paths in audit-wizard.test.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (value) => JSON.parse(JSON.stringify(value));

async function wizard(t) {
  const state = { posted: [] };
  const ui = await page(t, 'audit-wizard', {
    globals: { sessionStorage: { getItem: () => null, setItem: () => {} } },
    get: async (u) => {
      switch (u.pathname) {
        case '/api/v1/controls':
          return {
            items: [
              { id: 'c1', ref: 'A.5.1', title: 'Policies', type: 'annex_control' },
              { id: 'c2', ref: 'A.8.2', title: null, type: 'annex_control' },
            ],
          };
        case '/api/v1/clauses':
          return { items: [{ id: 'k4', ref: '4', title: 'Context', parent_id: null }] };
        case '/api/v1/graph/clause_control':
          return { nodes: [], links: [] };
        case '/api/v1/isms/documents':
          return {
            items: [
              {
                id: 'd1',
                title: 'Access policy',
                document_type: 'policy',
                clauses: [],
                controls: [],
              },
              {
                id: 'd2',
                title: 'Backup plan',
                document_type: 'procedure',
                clauses: [],
                controls: [],
              },
            ],
          };
        case '/api/v1/isms/swot':
          return {
            items: [
              { id: 's1', title: 'Supplier reliance', kind: 'threat' },
              { id: 's2', title: 'Skilled team', kind: 'strength' },
            ],
          };
        case '/api/v1/isms/people':
          return { items: [{ id: 'p1', name: 'Pat', email: 'pat@example.test', username: null }] };
        case '/api/v1/audits/users':
          return { items: [{ id: 'u2', username: 'zed', email: '' }] };
        default:
          throw new Error('Unexpected GET ' + u);
      }
    },
    post: async (u, body) => {
      state.posted.push({ path: u.pathname, body: plain(body) });
      return { id: 'a9', title: body.title, framework_slug: 'A' };
    },
  });
  await settle(() => !ui.el('wizardSteps').hidden);
  return { ui, state };
}

const next = (ui) =>
  ui.el('wizardForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
const visible = (ui) =>
  [...ui.document.querySelectorAll('.risk-wizard-step')].find((s) => !s.hidden).dataset.step;
const listed = (ui, key) =>
  [...ui.document.querySelectorAll(`[data-scope-key="${key}"]`)].map((box) => box.value);
const filter = (ui, key, value, type = 'input') => {
  const box = ui.document.querySelector(`[data-scope-filter="${key}"]`);
  box.value = value;
  box.dispatchEvent(new ui.window.Event(type, { bubbles: true }));
};
const choose = (ui, value) => {
  ui.el('attendeeWho').value = value;
  ui.el('attendeeWho').dispatchEvent(new ui.window.Event('change'));
};

test('scope filters search policies by type and SWOT items by quadrant', async (t) => {
  const { ui } = await wizard(t);
  ui.el('auditTitle').value = 'Filtered';
  next(ui);
  await settle(() => listed(ui, 'swot').length === 2);
  filter(ui, 'documents', 'procedure');
  assert.deepEqual(listed(ui, 'documents'), ['d2']);
  filter(ui, 'swot', 'STRENGTH');
  assert.deepEqual(listed(ui, 'swot'), ['s2']);
  // An untitled control is found by its reference, never by the word "null".
  filter(ui, 'controls', 'nul');
  assert.deepEqual(listed(ui, 'controls'), []);
  filter(ui, 'controls', 'a.8');
  assert.deepEqual(listed(ui, 'controls'), ['c2']);
  assert.equal(
    ui.document.querySelector('[data-scope-key="controls"]').closest('label').textContent.trim(),
    'A.8.2',
  );
});

test('leaving a filter or clicking between the tabs changes no choice', async (t) => {
  const { ui } = await wizard(t);
  ui.el('auditTitle').value = 'Steady';
  next(ui);
  await settle(() => listed(ui, 'controls').length === 2);
  const box = ui.document.querySelector('[data-scope-key="controls"][value="c1"]');
  box.checked = true;
  box.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  // A filter reports a change when it loses focus after typing.
  filter(ui, 'controls', 'A.5', 'change');
  ui.el('scopeTabs').dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true }));
  assert.equal(
    ui.el('scopeTabs').querySelector('[aria-selected="true"]').textContent,
    'Controls (1)',
  );
  assert.equal(ui.document.querySelector('[data-scope-pane="controls"]').hidden, false);
  assert.equal(ui.el('scopeNote').textContent, '');
  next(ui);
  next(ui);
  assert.match(ui.el('reviewSummary').textContent, /Scope1 control/);
  await settle(() => ui.idle());
});

test('attendee choices show who has no email and clear when someone else is chosen', async (t) => {
  const { ui } = await wizard(t);
  ui.el('auditTitle').value = 'People';
  next(ui);
  next(ui);
  await settle(() => ui.el('attendeeWho').options.length === 3);
  assert.deepEqual(
    [...ui.el('attendeeWho').options].map((o) => o.textContent),
    ['Someone else (enter their details)', 'Pat · pat@example.test', 'zed'],
  );
  choose(ui, 'person:p1');
  assert.deepEqual(
    [ui.el('attendeeName').value, ui.el('attendeeEmail').value],
    ['Pat', 'pat@example.test'],
  );
  choose(ui, '');
  assert.deepEqual([ui.el('attendeeName').value, ui.el('attendeeEmail').value], ['', '']);

  choose(ui, 'user:u2');
  ui.el('addAttendee').click();
  choose(ui, 'user:u2');
  ui.el('attendeeName').value = '';
  ui.el('addAttendee').click();
  assert.equal(ui.el('status').textContent, 'This person is already an attendee.');
  // Clicking an attendee's name removes nothing.
  ui.el('attendeeList').querySelector('.fw-semibold').click();
  assert.equal(ui.el('attendeeList').querySelectorAll('li').length, 1);
  await settle(() => ui.idle());
});

test('an external audit is reviewed with attendee roles and created with its people and accounts', async (t) => {
  const { ui, state } = await wizard(t);
  ui.el('auditTitle').value = 'Certification';
  const external = ui.document.querySelector('input[value="external"]');
  external.checked = true;
  external.dispatchEvent(new ui.window.Event('change'));
  next(ui);
  next(ui);
  await settle(() => ui.el('attendeeWho').options.length === 3);
  choose(ui, 'person:p1');
  ui.el('attendeeRole').value = 'Auditee';
  ui.el('addAttendee').click();
  choose(ui, 'user:u2');
  ui.el('attendeeRole').value = 'Lead <b>auditor</b>';
  ui.el('addAttendee').click();
  next(ui);
  const summary = Object.fromEntries(
    [...ui.el('reviewSummary').querySelectorAll('dt')].map((dt) => [
      dt.textContent,
      dt.nextElementSibling.querySelector('span').textContent,
    ]),
  );
  assert.equal(summary.Type, 'External audit');
  assert.equal(summary.Attendees, 'Pat (Auditee), zed (Lead <b>auditor</b>)');
  assert.equal(ui.el('reviewSummary').querySelector('b'), null);
  next(ui);
  await settle(() => visible(ui) === 'done');
  assert.equal(state.posted[0].body.audit_type, 'external');
  assert.deepEqual(state.posted[0].body.attendees, [
    { user_id: null, person_id: 'p1', name: 'Pat', email: 'pat@example.test', role: 'Auditee' },
    { user_id: 'u2', person_id: null, name: 'zed', email: null, role: 'Lead <b>auditor</b>' },
  ]);
});
