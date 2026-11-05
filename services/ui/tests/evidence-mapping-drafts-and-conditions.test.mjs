// Drafting a mapping from an event and building its field conditions: events
// with unusual sources or no payload, malformed event links, sample events
// without an activity, and condition rows edited in different orders.
import test from 'node:test';
import assert from 'node:assert/strict';
import { editor } from './support/evidence-mapping-editor.mjs';

const id = '22222222-2222-4222-8222-222222222222';
// Steps 2 and 3 with a mapping name and one control, then a predefined save.
async function publish(ui, description = 'Collected evidence') {
  await ui.click('next-step');
  ui.$('description').value = description;
  ui.$('control').value = 'A1';
  ui.$('add-target').click();
  await ui.click('next-step');
  ui.$('predefine').checked = true;
  await ui.click('save-rule');
  return ui.writes.filter((write) => write.method === 'PUT').at(-1);
}
const rows = (ui) => [...ui.$('field-rows').children];

test('a draft from an event with an unlisted source and no payload names the rule evidence', async (t) => {
  const ui = await editor(t, {
    query: `?from_event=${id}&framework=A`,
    events: {
      [id]: { id, source: '日志', action: null, system: null, normalized_payload: null },
    },
  });
  assert.ok(ui.calls.includes(`/api/v1/events/${id}?framework=A`));
  assert.equal(ui.$('definition-adapter').value, '日志');
  assert.equal(ui.$('definition-adapter').selectedOptions[0].textContent, '日志');
  assert.equal(ui.$('description').value, 'Evidence: 日志');
  // Only the optional Summary row: the event has no payload fields to draft.
  assert.equal(rows(ui).length, 1);
  assert.equal(rows(ui)[0].fieldInputs.path.value, 'event:summary');
  const saved = await publish(ui, '証跡');
  // Neither the source nor the name gives the rule ID a usable character.
  assert.equal(saved.path, '/api/v1/admin/evidence-definitions/evidence');
  assert.equal(saved.body.rule.id, 'evidence');
  assert.equal(saved.body.rule.when.source, '日志');
});

test('an event link with a malformed identifier opens no draft', async (t) => {
  const ui = await editor(t, { query: '?from_event=..%2Fadmin' });
  assert.equal(ui.$('status').textContent, 'Invalid event identifier for mapping draft.');
  assert.equal(ui.$('status').getAttribute('role'), 'alert');
  assert.ok(!ui.calls.some((call) => call.startsWith('/api/v1/events')));
  assert.equal(ui.$('editor').hidden, true);
});

test('one suggested target from the event page is added and named in the draft notice', async (t) => {
  const ui = await editor(t, {
    query: `?from_event=${id}&targets=A1,Z9`,
    events: { [id]: { id, source: 'keen-agent', action: 'login', normalized_payload: {} } },
  });
  assert.match(
    ui.$('draft-notice').textContent,
    /1 suggested target added from the event page \(A1\); confirm each one is appropriate before saving\.$/,
  );
  assert.deepEqual(
    [...ui.$('targets').querySelectorAll('.fw-semibold')].map((ref) => ref.textContent),
    ['Framework A / A1'],
  );
});

test('field choices list each path once and keep an unchosen row blank', async (t) => {
  const ui = await editor(t, {
    query: `?from_event=${id}`,
    events: {
      [id]: {
        id,
        source: 'keen-agent',
        action: 'login',
        normalized_payload: { fields: { 'service.name': 'sshd' } },
      },
    },
    samples: [
      {
        id: 's1',
        source: 'keen-agent',
        action: 'login',
        summary: 'Login',
        fields: [{ path: ['fields', 'service.name'], value: 'cron' }],
      },
    ],
  });
  const paths = [...ui.$('field-paths').options].map((option) => option.value);
  assert.deepEqual(paths, ['["fields","service.name"]']);
  ui.$('field-add').click();
  const blank = rows(ui).at(-1).fieldInputs.path;
  assert.equal(blank.value, '');
  await ui.choose('sample', '0');
  assert.equal(blank.value, '');
  assert.equal(ui.$('status').textContent, '');
});

test('condition rows keep a typed value, can be removed, and save an existence check without a value', async (t) => {
  const ui = await editor(t, {
    samples: [
      {
        id: 's1',
        source: 'keen-agent',
        action: 'login',
        outcome: 'success',
        summary: 'Login',
        fields: [{ path: ['fields', 'user.name'], value: 'dana' }],
      },
    ],
  });
  const path = '["fields","user.name"]';
  await ui.openInput('keen-agent');
  await ui.choose('sample', '0');
  const change = (select, value) => {
    select.value = value;
    select.dispatchEvent(new ui.window.Event('change'));
  };

  ui.$('field-add').click();
  const typed = rows(ui).at(-1).fieldInputs;
  typed.value.value = 'alice';
  change(typed.path, path);
  // A value typed first is kept when the field is chosen.
  assert.equal(typed.value.value, 'alice');

  ui.$('field-add').click();
  const exists = rows(ui).at(-1).fieldInputs;
  change(exists.path, path);
  assert.equal(exists.value.value, 'dana');
  change(exists.op, 'exists');
  assert.equal(exists.value.hidden, true);

  ui.$('field-add').click();
  assert.equal(rows(ui).length, 4);
  rows(ui).at(-1).querySelector('.btn-outline-danger').click();
  assert.equal(rows(ui).length, 3);

  const saved = await publish(ui, 'Agent logins');
  assert.deepEqual(saved.body.rule.when.fields, [
    { path: ['fields', 'user.name'], operator: 'equals', value: 'alice' },
    { path: ['fields', 'user.name'], operator: 'exists' },
  ]);
});

test('samples without an activity or result read plainly, and Use this event needs a choice', async (t) => {
  const ui = await editor(t, {
    samples: [
      {
        id: 's1',
        source: 'keen-agent',
        system: null,
        action: null,
        outcome: null,
        severity: null,
        summary: 'Heartbeat',
        fields: [],
      },
    ],
  });
  await ui.openInput('keen-agent');
  assert.equal(ui.$('sample').options[1].textContent, '(no action) · (no result) · Heartbeat');
  ui.$('when-action').value = 'typed';
  await ui.click('use-sample');
  assert.equal(ui.$('when-action').value, 'typed');
  assert.equal(ui.$('rule-id').value, '');

  await ui.choose('sample', '0');
  assert.equal(
    ui.$('sample-details').textContent,
    'Which system? (optional): (not supplied) · What happened? (action): (not supplied) · What was the result? (outcome): (not supplied) · Who did it? (actor): (not supplied) · Severity number: (not supplied)',
  );
  await ui.click('use-sample');
  assert.deepEqual(
    ['system', 'action', 'outcome'].map((field) => ui.$('when-' + field).value),
    ['', '', ''],
  );
  assert.equal(ui.$('rule-id').value, 'keen-agent');

  await ui.choose('sample', '');
  assert.equal(ui.$('sample-details').textContent, 'Choose a recent event to inspect its fields.');
});

test('a failure starting a rule for an integration is reported', async (t) => {
  const ui = await editor(t, { sources: ['forgejo', 'keen-agent'] });
  assert.equal(ui.$('definition-adapter').value, 'forgejo');
  ui.state.failConfiguration = 'Configuration store unavailable';
  ui.window.dispatchEvent(
    new ui.window.CustomEvent('keen-integration-rule', {
      detail: { source: 'integration:okta', name: 'Okta sign-ins' },
    }),
  );
  await ui.idle();
  assert.equal(ui.$('status').textContent, 'Configuration store unavailable');
  assert.equal(ui.$('status').getAttribute('role'), 'alert');
  assert.notEqual(ui.$('description').value, 'Okta sign-ins');
});
