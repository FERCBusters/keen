import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';
const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
async function fixture(t, admin = true) {
  const calls = [];
  const data = {
    policy: { mode: 'disabled', value: null, updated_at: '2026-01-01T00:00:00' },
    overrides: [{ scope: 'source', key: '<img src=x>', mode: 'age', value: 30 }],
    choices: {
      source: [{ key: 'github', label: 'GitHub' }],
      rule: [{ key: 'specific', label: 'Specific rule' }],
    },
    jobs: [],
    storage: { pending_objects: 0, waiting_objects: 0 },
    total_events: 10,
    audit_protected_events: 2,
  };
  const ui = await loadUi('pages/evidence-retention.js', {
    html,
    url: 'https://keen.example/admin.html?retention_rule=specific#retention',
    globals: { setInterval: () => 0 },
    vendor: {
      apiGet: async () => {
        calls.push(['get']);
        return data;
      },
      apiPost: async (url, body) => {
        calls.push(['post', url, body]);
        return { eligible_events: 3, total_events: 10, audit_protected_events: 2 };
      },
      apiPut: async (url, body) => {
        calls.push(['put', url, body]);
        return { ...data.policy, updated_at: '2026-01-02T00:00:00' };
      },
      toast: () => {},
    },
  });
  t.after(() => ui.close());
  ui.api.initEvidenceRetention(admin);
  if (admin)
    await settle(() => ui.document.querySelectorAll('#retention-overrides > div').length === 2);
  return { ...ui, calls };
}
test('retention edits include scoped policies, safe identifiers and concurrency token', async (t) => {
  const ui = await fixture(t);
  const rows = ui.document.querySelectorAll('#retention-overrides > div');
  assert.equal(rows[0].querySelectorAll('select')[1].value, '<img src=x>');
  assert.equal(rows[0].querySelector('img'), null);
  assert.equal(rows[1].querySelectorAll('select')[1].value, 'specific');
  assert.equal(rows[1].querySelector('select').value, 'rule');
  ui.document
    .getElementById('retention-form')
    .dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.calls.some((call) => call[0] === 'put'));
  const body = ui.calls.find((call) => call[0] === 'put')[2];
  assert.equal(body.overrides.length, 2);
  assert.equal(body.overrides[0].value, 30);
  assert.equal(body.expected_updated_at, '2026-01-01T00:00:00');
  rows[0].querySelector('button').click();
  assert.equal(ui.document.querySelectorAll('#retention-overrides > div').length, 1);
});
test('retention controls do not fetch or initialise for a non-administrator', async (t) => {
  const ui = await fixture(t, false);
  assert.equal(ui.calls.length, 0);
  assert.equal(ui.document.querySelectorAll('#retention-overrides > div').length, 0);
});

test('scope changes offer only existing values and clear incompatible selections', async (t) => {
  const ui = await fixture(t);
  const row = ui.document.querySelectorAll('#retention-overrides > div')[1];
  const [scope, key] = row.querySelectorAll('select');
  assert.equal(row.querySelectorAll('input').length, 1); // numeric limit only
  key.value = 'invented-rule';
  assert.equal(key.value, '');
  assert.equal(key.checkValidity(), false);
  scope.value = 'source';
  scope.dispatchEvent(new ui.window.Event('change'));
  assert.equal(key.value, '');
  assert.ok([...key.options].some((option) => option.value === 'github'));
  assert.ok(![...key.options].some((option) => option.value === 'specific'));
  assert.match(
    [...key.options].find((option) => option.value === '<img src=x>').textContent,
    /Unavailable/,
  );
});
