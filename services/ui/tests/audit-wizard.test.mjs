// The new-audit wizard: details, scope, attendees, a review, and the choice
// of where to begin once the audit exists.
import test from 'node:test';
import assert from 'node:assert/strict';
import { page, settle } from './support/page-fixture.mjs';

const plain = (value) => JSON.parse(JSON.stringify(value));
const HOSTILE = '<img src=x onerror=alert(1)>';

async function wizard(t, options = {}) {
  const state = { posted: [], stored: {} };
  const ui = await page(t, 'audit-wizard', {
    me: options.me || { is_admin: true },
    setup: options.setup,
    globals: {
      sessionStorage: {
        getItem: (key) => state.stored[key] ?? null,
        setItem: (key, value) => {
          state.stored[key] = value;
        },
      },
    },
    get: async (u) => {
      const fail = options.fail || {};
      switch (u.pathname) {
        case '/api/v1/controls':
          if (fail.controls) throw new Error('Controls <b>offline</b>');
          return {
            items: [
              { id: 'c1', ref: 'A.5.1', title: 'Policies' },
              { id: 'c9', ref: 'A.8.15', title: 'Logging ' + HOSTILE },
              { id: 'k0', ref: '4', type: 'clause' },
            ],
          };
        case '/api/v1/clauses':
          return {
            items: [
              { id: 'k4', ref: '4', title: 'Context' },
              { id: 'k41', ref: '4.1', title: 'Understanding the organisation' },
            ],
          };
        case '/api/v1/graph/clause_control':
          if (fail.graph) throw new Error('Graph offline');
          return {
            links: [
              { source: 'clause:k41', target: 'control:c1' },
              { source: '', target: 'control:x' },
            ],
          };
        case '/api/v1/isms/documents':
          if (fail.documents) throw new Error('Documents offline');
          return {
            items: [
              {
                id: 'd1',
                title: 'Access policy',
                document_type: 'policy',
                clauses: [{ id: 'k41' }],
                controls: [{ id: 'c9' }],
              },
              { id: 'd2', title: 'Backup plan' },
            ],
          };
        case '/api/v1/isms/swot':
          return {
            items: options.swot ?? [{ id: 's1', title: 'Supplier reliance', kind: 'threat' }],
          };
        case '/api/v1/isms/people':
          return { items: [{ id: 'p1', name: 'Pat Auditee', email: 'pat@example.test' }] };
        case '/api/v1/audits/users':
          if (fail.users) throw new Error('Users offline');
          return { items: [{ id: 'u2', username: 'zed', email: 'zed@example.test' }] };
        default:
          throw new Error('Unexpected GET ' + u);
      }
    },
    post: async (u, body) => {
      state.posted.push({ path: u.pathname, body: plain(body) });
      if (options.createFail) throw new Error('Title <b>taken</b>');
      return { id: 'a9', title: plain(body).title, framework_slug: 'A' };
    },
  });
  await settle(() => !ui.el('wizardSteps').hidden || ui.el('status').textContent);
  return { ui, state };
}

const next = (ui) =>
  ui.el('wizardForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
const visible = (ui) =>
  [...ui.document.querySelectorAll('.risk-wizard-step')].find((s) => !s.hidden).dataset.step;
const tick = (ui, key, value, checked = true) => {
  const box = ui.document.querySelector(`[data-scope-key="${key}"][value="${value}"]`);
  box.checked = checked;
  box.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
};
const tabs = (ui) => [...ui.el('scopeTabs').querySelectorAll('button')].map((b) => b.textContent);

test('an audit starts with its details, checked before moving on', async (t) => {
  const { ui } = await wizard(t);
  assert.equal(visible(ui), 'details');
  assert.deepEqual(
    [...ui.el('wizardSteps').querySelectorAll('button')].map((b) => [
      b.textContent,
      b.disabled,
      b.getAttribute('aria-current'),
    ]),
    [
      ['1. Details', false, 'step'],
      ['2. Scope', true, null],
      ['3. Attendees', true, null],
      ['4. Check and create', true, null],
    ],
  );
  assert.equal(ui.el('auditFramework').textContent, 'A');
  assert.equal(ui.el('backStep').hidden, true);
  assert.equal(ui.el('skipStep').hidden, true);

  next(ui);
  assert.equal(ui.el('status').textContent, 'Give the audit a title.');
  assert.equal(visible(ui), 'details');
  ui.el('auditTitle').value = '  Surveillance audit ';
  ui.el('auditStart').value = '2026-06-03';
  ui.el('auditEnd').value = '2026-06-01';
  next(ui);
  assert.equal(ui.el('status').textContent, 'The audit cannot end before it begins.');
  ui.el('auditEnd').value = '2026-06-05';

  const external = ui.document.querySelector('input[value="external"]');
  external.checked = true;
  external.dispatchEvent(new ui.window.Event('change'));
  assert.equal(
    ui.document.querySelector('[data-audit-type="external"]').classList.contains('selected'),
    true,
  );
  assert.equal(
    ui.document.querySelector('[data-audit-type="internal"]').classList.contains('selected'),
    false,
  );
  next(ui);
  assert.equal(visible(ui), 'scope');
  assert.equal(ui.el('status').style.display, 'none');
  assert.equal(ui.el('backStep').hidden, false);
  assert.equal(ui.el('skipStep').hidden, false);
  await settle(() => ui.document.querySelector('[data-scope-key="controls"]'));
});

test('a type chosen while the page is still loading is the one highlighted', async (t) => {
  const { ui } = await wizard(t, {
    setup(window) {
      window.document.querySelector('input[value="external"]').checked = true;
    },
  });
  const selected = [...ui.document.querySelectorAll('[data-audit-type].selected')];
  assert.deepEqual(
    selected.map((card) => card.dataset.auditType),
    ['external'],
  );
});

test('scope covers controls, clauses, policies and SWOT items with their links', async (t) => {
  const { ui } = await wizard(t);
  ui.el('auditTitle').value = 'Scoped';
  next(ui);
  await settle(() =>
    ui.document.querySelector('[data-scope-key="d1"], [data-scope-key="documents"]'),
  );
  assert.deepEqual(tabs(ui), ['Controls', 'Clauses', 'Policies and procedures', 'SWOT items']);
  assert.equal(ui.document.querySelectorAll('[data-scope-key="controls"]').length, 2);
  assert.equal(ui.document.querySelector('[data-scope-items="controls"] img'), null);
  // Clauses are indented by depth.
  assert.equal(
    ui.document.querySelector('[data-scope-key="clauses"][value="k41"]').closest('label').style
      .marginLeft,
    '1rem',
  );

  // A policy brings its clause, the clause's control and its own control.
  tick(ui, 'documents', 'd1');
  assert.equal(ui.el('scopeNote').textContent, 'Added 3 linked clauses and controls as well.');
  assert.deepEqual(tabs(ui), [
    'Controls (2)',
    'Clauses (1)',
    'Policies and procedures (1)',
    'SWOT items',
  ]);
  assert.equal(ui.document.querySelector('[data-scope-key="controls"][value="c9"]').checked, true);
  tick(ui, 'documents', 'd2');
  assert.equal(ui.el('scopeNote').textContent, '');
  tick(ui, 'controls', 'c1', false);
  tick(ui, 'clauses', 'k41', false);
  tick(ui, 'clauses', 'k41');
  assert.equal(ui.el('scopeNote').textContent, 'Added 1 linked clause or control as well.');
  tick(ui, 'swot', 's1');

  // Tabs switch the list on show; filters narrow it.
  ui.el('scopeTabs').querySelector('[data-scope-tab="swot"]').click();
  assert.equal(ui.document.querySelector('[data-scope-pane="swot"]').hidden, false);
  assert.equal(ui.document.querySelector('[data-scope-pane="controls"]').hidden, true);
  const filter = ui.document.querySelector('[data-scope-filter="controls"]');
  filter.value = 'zzz';
  filter.dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  assert.match(
    ui.document.querySelector('[data-scope-items="controls"]').textContent,
    /Nothing matches/,
  );
  filter.value = 'logging';
  filter.dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  assert.equal(ui.document.querySelectorAll('[data-scope-key="controls"]').length, 1);

  next(ui);
  assert.equal(visible(ui), 'people');
  next(ui);
  assert.match(
    ui.el('reviewSummary').textContent,
    /2 controls, 1 clause, 2 policies and procedures, 1 SWOT item/,
  );
});

test('list failures stay in their own list and empty lists say so', async (t) => {
  const { ui } = await wizard(t, { fail: { documents: true, graph: true }, swot: [] });
  ui.el('auditTitle').value = 'Partial';
  next(ui);
  await settle(() => /Could not load this list/.test(ui.el('scopePanes').textContent));
  assert.equal(
    ui.document.querySelector('[data-scope-items="documents"]').textContent,
    'Could not load this list: Documents offline',
  );
  assert.match(
    ui.document.querySelector('[data-scope-items="swot"]').textContent,
    /No SWOT items in this framework yet/,
  );
  // Without the graph a clause brings nothing with it.
  tick(ui, 'clauses', 'k41');
  assert.equal(ui.el('scopeNote').textContent, '');
  // Going back and forward again keeps the loaded lists and choices.
  ui.el('backStep').click();
  next(ui);
  assert.equal(ui.document.querySelector('[data-scope-key="clauses"][value="k41"]').checked, true);
});

test('attendees come from the register, Keen accounts or plain details', async (t) => {
  const { ui } = await wizard(t);
  ui.el('auditTitle').value = 'People';
  next(ui);
  ui.el('skipStep').click();
  assert.equal(visible(ui), 'people');
  await settle(() => ui.el('attendeeWho').options.length === 3);
  assert.deepEqual(
    [...ui.el('attendeeWho').options].map((o) => [o.value, o.textContent]),
    [
      ['', 'Someone else (enter their details)'],
      ['person:p1', 'Pat Auditee · pat@example.test'],
      ['user:u2', 'zed · zed@example.test'],
    ],
  );
  ui.el('addAttendee').click();
  assert.equal(ui.el('status').textContent, 'Choose a person or enter a name.');

  ui.el('attendeeWho').value = 'person:p1';
  ui.el('attendeeWho').dispatchEvent(new ui.window.Event('change'));
  assert.deepEqual(
    [ui.el('attendeeName').value, ui.el('attendeeEmail').value],
    ['Pat Auditee', 'pat@example.test'],
  );
  ui.el('attendeeRole').value = 'Auditee';
  ui.el('addAttendee').click();
  ui.el('attendeeWho').value = 'person:p1';
  ui.el('attendeeWho').dispatchEvent(new ui.window.Event('change'));
  ui.el('addAttendee').click();
  assert.equal(ui.el('status').textContent, 'Pat Auditee is already an attendee.');

  ui.el('attendeeWho').value = 'user:u2';
  ui.el('attendeeWho').dispatchEvent(new ui.window.Event('change'));
  ui.el('attendeeName').value = '';
  ui.el('attendeeEmail').value = '';
  ui.el('attendeeRole').value = 'Lead auditor';
  ui.el('addAttendee').click();

  ui.el('attendeeName').value = HOSTILE + 'Jo';
  ui.el('attendeeEmail').value = 'not an address';
  ui.el('addAttendee').click();
  assert.equal(ui.el('status').textContent, 'Enter a valid email address, or leave it blank.');
  ui.el('attendeeEmail').value = 'jo@example.test';
  ui.el('addAttendee').click();
  ui.el('attendeeName').value = 'Removed later';
  ui.el('addAttendee').click();

  const rows = [...ui.el('attendeeList').querySelectorAll('li')];
  assert.deepEqual(
    rows.map((li) => [
      li.querySelector('.fw-semibold').textContent,
      li.querySelector('.badge')?.textContent || '',
      li.querySelector('.small-muted').textContent,
    ]),
    [
      ['Pat Auditee', 'Auditee', 'Person · pat@example.test'],
      ['zed', 'Lead auditor', 'Keen account'],
      [`${HOSTILE}Jo`, '', 'Guest · jo@example.test'],
      ['Removed later', '', 'Guest'],
    ],
  );
  assert.equal(ui.el('attendeeList').querySelector('img'), null);
  rows[3].querySelector('[data-remove-attendee]').click();
  assert.equal(ui.el('attendeeList').querySelectorAll('li').length, 3);
  // The fields clear after each addition.
  assert.deepEqual(
    ['attendeeWho', 'attendeeName', 'attendeeEmail', 'attendeeRole'].map((id) => ui.el(id).value),
    ['', '', '', ''],
  );
});

test('the review creates the audit with its scope and attendees, then offers where to start', async (t) => {
  const { ui, state } = await wizard(t);
  ui.el('auditTitle').value = ' Spring audit ';
  ui.el('auditStart').value = '2026-03-01';
  next(ui);
  await settle(() => ui.document.querySelector('[data-scope-key="controls"]'));
  tick(ui, 'controls', 'c1');
  next(ui);
  await settle(() => ui.el('attendeeWho').options.length === 3);
  ui.el('attendeeName').value = 'Visitor';
  ui.el('addAttendee').click();
  next(ui);
  assert.equal(visible(ui), 'review');
  assert.equal(ui.el('nextStep').textContent, 'Create audit');
  const summary = [...ui.el('reviewSummary').querySelectorAll('dt')].map((dt) => [
    dt.textContent,
    dt.nextElementSibling.querySelector('span').textContent,
  ]);
  assert.deepEqual(summary, [
    ['Title', 'Spring audit'],
    ['Type', 'Internal audit'],
    ['Dates', '2026-03-01 to not set'],
    ['Framework', 'A'],
    ['Scope', '1 control'],
    ['Attendees', 'Visitor'],
  ]);
  // Change returns to a step; the stepper then goes forward again.
  ui.el('reviewSummary').querySelector('[data-goto-step="scope"]').click();
  assert.equal(visible(ui), 'scope');
  ui.el('wizardSteps').querySelector('[data-goto-step="details"]').click();
  assert.equal(visible(ui), 'details');
  next(ui);
  next(ui);
  next(ui);
  assert.equal(visible(ui), 'review');

  next(ui);
  await settle(() => visible(ui) === 'done');
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/audits',
    body: {
      title: 'Spring audit',
      framework_slug: 'A',
      audit_type: 'internal',
      start_date: '2026-03-01',
      end_date: null,
      controls: ['c1'],
      clauses: [],
      documents: [],
      attendees: [{ user_id: null, person_id: null, name: 'Visitor', email: null, role: null }],
    },
  });
  assert.equal(state.stored['keen.samplingAudit'], 'a9');
  assert.equal(ui.el('doneTitle').textContent, 'Spring audit is ready');
  assert.equal(ui.el('wizardFooter').hidden, true);
  assert.equal(ui.el('wizardSteps').hidden, true);
  assert.deepEqual(
    [...ui.el('startChoices').querySelectorAll('a')].map((a) => [
      a.textContent.trim().replace(/\s+/g, ' ').split(' ').slice(0, 5).join(' '),
      a.getAttribute('href'),
    ]),
    [
      ['Look for evidence by event', '/events.html?framework=A'],
      ['Look for evidence by control', '/controls.html?framework=A'],
      ['Look for evidence in the', '/isms.html?framework=A'],
      ['Start writing notes Open the', '/audit.html?id=a9&focus=notes&framework=A'],
      ['Open the audit', '/audit.html?id=a9&framework=A'],
    ],
  );
});

test('SWOT scope is sent when chosen and a refused audit stays on the review', async (t) => {
  const { ui, state } = await wizard(t, { createFail: true });
  ui.el('auditTitle').value = 'Refused';
  next(ui);
  await settle(() => ui.document.querySelector('[data-scope-key="swot"]'));
  tick(ui, 'swot', 's1');
  next(ui);
  next(ui);
  next(ui);
  await settle(() => /Could not create/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Could not create the audit: Title <b>taken</b>');
  assert.deepEqual(state.posted[0].body.swot_ids, ['s1']);
  assert.equal(visible(ui), 'review');
  assert.equal(ui.el('nextStep').disabled, false);
  assert.equal(state.stored['keen.samplingAudit'], undefined);
});

test('readers cannot start audits, and lists follow features and ISMS access', async (t) => {
  const reader = await wizard(t, { me: { can_view_audits: true } });
  assert.equal(
    reader.ui.el('status').textContent,
    'You need audits.manage permission to start an audit.',
  );
  assert.equal(reader.ui.el('wizardForm').hidden, true);

  const manager = await wizard(t, {
    me: { can_manage_audits: true, features: { enabled: { documents: false } } },
    fail: { users: true },
  });
  const { ui } = manager;
  ui.el('auditTitle').value = 'Narrow';
  next(ui);
  await settle(() => ui.document.querySelector('[data-scope-key="controls"]'));
  assert.deepEqual(tabs(ui), ['Controls', 'Clauses']);
  next(ui);
  await settle(() => /Could not list Keen accounts/.test(ui.el('status').textContent));
  assert.deepEqual(
    [...ui.el('attendeeWho').options].map((o) => o.value),
    [''],
  );
  assert.ok(!ui.calls.some((c) => c.url.startsWith('/api/v1/isms/')));
});

test('typing in the wizard warns before leaving and clears when the audit is created', async (t) => {
  const { ui } = await wizard(t);
  const leave = () => {
    const event = new ui.window.Event('beforeunload', { cancelable: true });
    ui.window.dispatchEvent(event);
    return event.defaultPrevented;
  };
  assert.equal(leave(), false);
  assert.equal(ui.el('unsaved').hidden, true);

  ui.el('auditTitle').value = 'Half-finished audit';
  ui.el('auditTitle').dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  assert.equal(ui.el('unsaved').hidden, false);
  assert.equal(leave(), true);

  next(ui);
  await settle(() => visible(ui) === 'scope');
  // Moving between steps keeps the unsaved warning until creation succeeds.
  assert.equal(leave(), true);

  next(ui);
  await settle(() => ui.el('attendeeWho').options.length === 3);
  next(ui);
  next(ui);
  await settle(() => visible(ui) === 'review');
  next(ui);
  await settle(() => visible(ui) === 'done');
  assert.equal(ui.el('unsaved').hidden, true);
  assert.equal(leave(), false);
});
