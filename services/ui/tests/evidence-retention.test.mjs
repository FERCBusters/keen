import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';
const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
async function fixture(t, admin = true, options = {}) {
  const calls = [];
  const notices = [];
  const confirmations = [];
  const intervals = [];
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
    globals: {
      setInterval: (fn) => (intervals.push(fn), 0),
      confirm: (message) => {
        confirmations.push(message);
        return options.confirm ?? true;
      },
    },
    vendor: {
      apiGet: async () => {
        calls.push(['get']);
        return options.data ? options.data(data) : data;
      },
      apiPost: async (url, body) => {
        calls.push(['post', url, body]);
        if (options.postFail) throw new Error(options.postFail);
        return { eligible_events: 3, total_events: 10, audit_protected_events: 2 };
      },
      apiPut: async (url, body) => {
        calls.push(['put', url, body]);
        return { ...data.policy, updated_at: '2026-01-02T00:00:00' };
      },
      toast: (element, message, kind) => notices.push([message, kind]),
    },
  });
  t.after(() => ui.close());
  ui.api.initEvidenceRetention(admin);
  if (admin)
    await settle(() => ui.document.querySelectorAll('#retention-overrides > div').length === 2);
  const $ = (suffix) => ui.document.getElementById('retention-' + suffix);
  return { ...ui, calls, notices, confirmations, intervals, $ };
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

test('a purge needs the typed confirmation, a preview and the administrator’s agreement', async (t) => {
  const ui = await fixture(t, true, { confirm: false });
  assert.equal(ui.$('purge').disabled, true);
  ui.$('confirm').value = 'purge evidence';
  ui.$('confirm').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.$('purge').disabled, true);
  ui.$('confirm').value = 'PURGE EVIDENCE';
  ui.$('confirm').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.$('purge').disabled, false);

  // Declining the preview sends no purge.
  ui.$('purge').click();
  await settle(() => ui.confirmations.length === 1);
  await settle(() => ui.calls.filter((c) => c[0] === 'get').length >= 2);
  assert.match(
    ui.confirmations[0],
    /^3 events currently eligible; 2 audit-protected; 10 events in total\./,
  );
  assert.match(ui.confirmations[0], /Permanently purge all eligible evidence/);
  assert.deepEqual(
    ui.calls.filter((c) => c[0] === 'post').map((c) => c[1]),
    ['/api/v1/admin/evidence-retention/preview-all'],
  );
  assert.equal(ui.$('confirm').value, 'PURGE EVIDENCE');

  const agreed = await fixture(t);
  agreed.$('confirm').value = 'PURGE EVIDENCE';
  agreed.$('confirm').dispatchEvent(new agreed.window.Event('input'));
  agreed.$('purge').click();
  await settle(() => agreed.notices.length === 1);
  assert.deepEqual(agreed.notices[0], [
    'Purge queued. This screen refreshes while cleanup runs.',
    'success',
  ]);
  assert.deepEqual(
    JSON.parse(
      JSON.stringify(agreed.calls.filter((c) => c[0] === 'post').map((c) => [c[1], c[2]])),
    ),
    [
      ['/api/v1/admin/evidence-retention/preview-all', {}],
      ['/api/v1/admin/evidence-retention/purge', { mode: 'all', confirmation: 'PURGE EVIDENCE' }],
    ],
  );
  assert.equal(agreed.$('confirm').value, '');
});

test('previews report the count, clear when the policy changes and show failures', async (t) => {
  const ui = await fixture(t);
  ui.$('mode').value = 'age';
  ui.$('mode').dispatchEvent(new ui.window.Event('change'));
  ui.$('value').value = '90';
  ui.$('preview').click();
  await settle(() => ui.$('preview-result').textContent !== '');
  assert.equal(
    ui.$('preview-result').textContent,
    '3 events currently eligible; 2 audit-protected; 10 events in total.',
  );
  const preview = ui.calls.find((c) => c[0] === 'post' && c[1].endsWith('/preview'));
  assert.equal(preview[2].mode, 'age');
  assert.equal(preview[2].value, 90);
  // Editing the limit or an override's key makes the preview stale.
  ui.$('value').value = '91';
  ui.$('value').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.$('preview-result').textContent, '');
  ui.$('preview').click();
  await settle(() => ui.$('preview-result').textContent !== '');
  const key = ui.document
    .querySelectorAll('#retention-overrides > div')[0]
    .querySelectorAll('select')[1];
  key.dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.$('preview-result').textContent, '');
  // An invalid form is never previewed.
  ui.$('value').value = '';
  const before = ui.calls.length;
  ui.$('preview').click();
  await settle(() => ui.calls.length > before);
  assert.ok(!ui.calls.slice(before).some((c) => c[0] === 'post'));

  const failing = await fixture(t, true, { postFail: 'Preview unavailable' });
  failing.$('preview').click();
  await settle(() => failing.notices.length === 1);
  assert.deepEqual(failing.notices[0], ['Error: Preview unavailable', 'danger']);
});

test('an active purge can be cancelled after confirmation, and stuck files retried', async (t) => {
  const ui = await fixture(t, true, {
    data: (data) => ({
      ...data,
      jobs: [{ id: 'j1', status: 'running', deleted: 4, automatic: false }],
      storage: { pending_objects: 2, waiting_objects: 1, errors: ['Bucket <b>denied</b>'] },
    }),
    confirm: false,
  });
  assert.equal(ui.$('cancel').hidden, false);
  assert.equal(ui.$('retry').hidden, false);
  assert.equal(ui.$('purge').disabled, true);
  assert.match(ui.$('progress').textContent, /running · 4 events removed from the database/);
  assert.match(ui.$('progress').textContent, /2 stored files awaiting cleanup · 1 waiting/);
  assert.match(ui.$('progress').textContent, /Bucket <b>denied<\/b>/);
  assert.equal(ui.$('progress').querySelector('b'), null);

  ui.$('cancel').click();
  await settle(() => ui.confirmations.length === 1);
  assert.match(
    ui.confirmations[0],
    /Cancel the active purge\? Completed deletions cannot be undone/,
  );
  await settle(() => ui.calls.filter((c) => c[0] === 'get').length >= 2);
  assert.ok(!ui.calls.some((c) => c[0] === 'post'));

  ui.$('retry').click();
  await settle(() => ui.notices.length === 1);
  assert.deepEqual(ui.notices[0], [
    'Stored-file cleanup will retry on the next worker pass.',
    'success',
  ]);
  assert.deepEqual(
    ui.calls.filter((c) => c[0] === 'post').map((c) => c[1]),
    ['/api/v1/admin/evidence-retention/retry-storage'],
  );

  // The periodic refresh runs only while the retention tab is in view.
  const gets = () => ui.calls.filter((c) => c[0] === 'get').length;
  const seen = gets();
  ui.intervals[0]();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(gets(), seen);
  ui.document.getElementById('pane-retention').classList.add('active');
  ui.intervals[0]();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(gets(), seen, 'a hidden tab still refreshes nothing');
  Object.defineProperty(ui.document, 'hidden', { value: false, configurable: true });
  ui.intervals[0]();
  await settle(() => gets() === seen + 1);
});
