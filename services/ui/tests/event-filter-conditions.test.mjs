// Text filter conditions: the "field:operator:value" form the API reads, as the
// Events page parses, describes and edits it.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

// Objects from the page's realm are compared as plain data.
const plain = (value) => JSON.parse(JSON.stringify(value));

async function conditions(t) {
  const ui = await loadUi('pages/event-filter-conditions.js', {
    html: '<div id="host"></div><button id="add" type="button">Add</button>',
  });
  t.after(ui.close);
  return ui;
}

test('a condition needs a known field, a known operator and a value', async (t) => {
  const { api } = await conditions(t);
  assert.deepEqual(plain(api.parseCondition('actor:contains:alice')), {
    field: 'actor',
    op: 'contains',
    value: 'alice',
  });
  // The value keeps its colons; field and operator are read case-insensitively.
  assert.deepEqual(plain(api.parseCondition(' Summary : Regex :^a:b$')), {
    field: 'summary',
    op: 'regex',
    value: '^a:b$',
  });
  for (const raw of [
    '',
    null,
    'actor',
    'actor:contains',
    'actor:contains:   ',
    'colour:contains:red',
    'actor:sounds_like:alice',
  ])
    assert.equal(api.parseCondition(raw), null, String(raw));
  assert.equal(api.formatCondition({ field: 'any', op: 'equals', value: 'x:y' }), 'any:equals:x:y');
});

test('conditions are described in words, and unreadable ones say nothing', async (t) => {
  const { api } = await conditions(t);
  assert.equal(api.describeCondition('outcome:not_equals:ok'), 'Outcome does not equal “ok”');
  assert.equal(api.describeCondition('any:starts_with:dep'), 'Any field begins with “dep”');
  assert.equal(api.describeCondition('nonsense'), '');
  assert.equal(api.describeCondition('actor::alice'), '');
});

test('the editor shows readable rows, drops unreadable ones and stops at ten', async (t) => {
  const ui = await conditions(t);
  const host = ui.document.getElementById('host');
  const addButton = ui.document.getElementById('add');
  let changes = 0;
  const editor = ui.api.createConditionEditor({ host, addButton, onChange: () => changes++ });
  assert.equal(addButton.disabled, false);

  editor.set(['actor:contains:<b>alice</b>', 'broken', 'system:equals:prod']);
  const rows = () => [...host.querySelectorAll('[data-condition-row]')];
  assert.equal(rows().length, 2);
  assert.deepEqual(plain(editor.values()), ['actor:contains:<b>alice</b>', 'system:equals:prod']);
  assert.equal(host.querySelector('b'), null);
  assert.equal(host.querySelector('[data-condition-value]').value, '<b>alice</b>');

  // A row without a value is left out of the filters.
  addButton.click();
  assert.equal(rows().length, 3);
  assert.deepEqual(plain(editor.values()), ['actor:contains:<b>alice</b>', 'system:equals:prod']);
  assert.equal(ui.document.activeElement, rows()[2].querySelector('[data-condition-value]'));

  // Removing a row reports a change; clicks elsewhere in the host do nothing.
  rows()[0].querySelector('[data-condition-remove]').click();
  assert.equal(changes, 1);
  assert.deepEqual(plain(editor.values()), ['system:equals:prod']);
  host.click();
  assert.equal(changes, 1);
  assert.equal(rows().length, 2);

  editor.set(Array.from({ length: 12 }, (_, i) => `actor:contains:person${i}`));
  assert.equal(rows().length, ui.api.MAX_CONDITIONS);
  assert.equal(addButton.disabled, true);
  editor.set(null);
  assert.equal(rows().length, 0);
  assert.equal(addButton.disabled, false);
});
