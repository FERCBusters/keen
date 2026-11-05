// Evidence retention on the Admin page: count-based policies, overrides
// requested by link, repeated clicks while a request is in flight, purge job
// states, and saves refused or declined.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
const status = (extra = {}) => ({
  policy: { mode: 'disabled', value: null, updated_at: '2026-01-01T00:00:00' },
  overrides: [],
  choices: {
    source: [
      { key: 'github', label: 'GitHub' },
      { key: 'syslog', label: 'syslog' },
    ],
    rule: [{ key: 'specific', label: 'Specific rule' }],
  },
  jobs: [],
  storage: { pending_objects: 0, waiting_objects: 0, errors: [] },
  ingestion_pause: { paused: false },
  total_events: 10,
  audit_protected_events: 2,
  ...extra,
});
const counts = { eligible_events: 3, total_events: 10, audit_protected_events: 2 };

async function retention(t, { query = '', get, post, confirm = true } = {}) {
  const calls = [],
    notices = [],
    confirmations = [];
  const ui = await loadUi('pages/evidence-retention.js', {
    html,
    url: `https://keen.example/admin.html${query}#retention`,
    modules: {
      'pages/confirm-modal.js': {
        confirmAction: async (asked) => {
          confirmations.push(asked);
          return confirm;
        },
      },
    },
    globals: { setInterval: () => 0 },
    vendor: {
      apiGet: async (url) => {
        calls.push(['get', url]);
        return get ? get() : status();
      },
      apiPost: async (url, body) => {
        calls.push(['post', url, body]);
        return post ? post(url) : counts;
      },
      apiPut: async (url, body) => {
        calls.push(['put', url, body]);
        return { updated_at: '2026-01-02T00:00:00' };
      },
      toast: (element, message, kind) => notices.push([message, kind]),
    },
  });
  t.after(() => ui.close());
  ui.api.initEvidenceRetention(true);
  const $ = (suffix) => ui.document.getElementById('retention-' + suffix);
  const posts = () => calls.filter((call) => call[0] === 'post').map((call) => call[1]);
  const gets = () => calls.filter((call) => call[0] === 'get').length;
  return { ...ui, $, calls, notices, confirmations, posts, gets };
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 10));
function deferred() {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
}

test('a count-based policy explains which events it keeps', async (t) => {
  const ui = await retention(t);
  await settle(() => !ui.$('add').disabled);
  ui.$('mode').value = 'count';
  ui.$('mode').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.$('value-wrap').hidden, false);
  assert.equal(ui.$('value').required, true);
  assert.equal(ui.$('value').max, '1000000000');
  assert.equal(ui.$('value-label').textContent, 'Newest unsampled events to keep');
  assert.match(
    ui.$('explanation').textContent,
    /^Keeps the newest N unsampled events by event timestamp, plus all audit-protected events\./,
  );
});

test('a source linked from its page gets an override, and a source named by its key is offered once', async (t) => {
  const ui = await retention(t, {
    query: '?retention_source=github',
    get: () => status({ overrides: [{ scope: 'source', key: 'syslog', mode: 'age', value: 30 }] }),
  });
  await settle(() => ui.$('overrides').children.length === 2);
  const [saved, linked] = [...ui.$('overrides').children].map((row) =>
    row.querySelectorAll('select'),
  );
  assert.deepEqual(
    [linked[0].value, linked[1].value, linked[2].value],
    ['source', 'github', 'disabled'],
  );
  assert.equal(saved[1].value, 'syslog');
  assert.deepEqual(
    [...saved[1].options].map((option) => option.textContent),
    ['Choose a source type', 'GitHub (github)', 'syslog'],
  );
});

test('refreshing again while the status loads sends one request', async (t) => {
  const slow = deferred();
  let hold = false;
  const ui = await retention(t, {
    get: () => (hold ? slow.promise.then(() => status()) : status()),
  });
  await settle(() => !ui.$('add').disabled);
  hold = true;
  ui.$('refresh').click();
  ui.$('refresh').click();
  await tick();
  assert.equal(ui.gets(), 2);
  slow.resolve();
  await tick();
  ui.$('refresh').click();
  await tick();
  assert.equal(ui.gets(), 3);
});

test('a second action while a preview is running is ignored', async (t) => {
  const slow = deferred();
  const ui = await retention(t, { post: () => slow.promise.then(() => counts) });
  await settle(() => !ui.$('add').disabled);
  ui.$('preview').click();
  ui.$('preview').click();
  await tick();
  assert.deepEqual(ui.posts(), ['/api/v1/admin/evidence-retention/preview']);
  slow.resolve();
  await settle(() => ui.$('preview-result').textContent !== '');
  assert.equal(
    ui.$('preview-result').textContent,
    '3 events currently eligible; 2 audit-protected; 10 events in total.',
  );
});

test('the progress panel describes a paused ingestion and finished purges', async (t) => {
  const failed = await retention(t, {
    get: () =>
      status({
        ingestion_pause: { paused: true },
        jobs: [
          {
            id: 'j2',
            status: 'failed',
            deleted: 1200,
            automatic: true,
            last_error: '<b>Disk</b> full',
          },
        ],
      }),
  });
  await settle(() => failed.$('progress').textContent !== '');
  const notes = [...failed.$('progress').querySelectorAll('p')];
  assert.ok(
    notes.some(
      (p) =>
        p.className === 'text-warning' &&
        p.textContent === 'Ingestion paused for purge or its five-minute cooldown.',
    ),
  );
  const outcome = notes.find((p) => p.textContent.startsWith('failed'));
  assert.equal(outcome.className, 'text-danger');
  assert.equal(
    outcome.textContent,
    `failed · ${(1200).toLocaleString()} events removed from the database · automatic retention`,
  );
  assert.equal(outcome.querySelector('.spinner-border'), null);
  assert.equal(outcome.nextElementSibling.textContent, '<b>Disk</b> full');
  assert.equal(failed.$('progress').querySelector('b'), null);

  const done = await retention(t, {
    get: () => status({ jobs: [{ id: 'j1', status: 'completed', deleted: 4, automatic: false }] }),
  });
  await settle(() => done.$('progress').textContent !== '');
  const finished = [...done.$('progress').querySelectorAll('p')].find((p) =>
    p.textContent.startsWith('completed'),
  );
  assert.equal(finished.className, 'text-success');
  assert.equal(finished.textContent, 'completed · 4 events removed from the database');
  assert.doesNotMatch(done.$('progress').textContent, /Ingestion paused/);
});

test('saving before the policy has loaded sends nothing', async (t) => {
  const ui = await retention(t, {
    get: () => {
      throw new Error('Retention status unavailable');
    },
  });
  await settle(() => ui.notices.length === 1);
  assert.deepEqual(ui.notices[0], ['Retention status unavailable', 'danger']);
  ui.$('form').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await tick();
  assert.deepEqual(ui.posts(), []);
  assert.ok(!ui.calls.some((call) => call[0] === 'put'));
});

test('declining automatic deletion leaves the policy unsaved', async (t) => {
  const ui = await retention(t, { confirm: false });
  await settle(() => !ui.$('add').disabled);
  ui.$('mode').value = 'age';
  ui.$('mode').dispatchEvent(new ui.window.Event('change'));
  ui.$('value').value = '30';
  ui.$('form').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.confirmations.length === 1);
  await tick();
  assert.equal(ui.confirmations[0].title, 'Enable automatic deletion');
  assert.deepEqual(ui.posts(), ['/api/v1/admin/evidence-retention/preview']);
  assert.ok(!ui.calls.some((call) => call[0] === 'put'));
  assert.deepEqual(ui.notices, []);
});

test('a confirmed cancellation stops the active purge', async (t) => {
  const ui = await retention(t, {
    get: () => status({ jobs: [{ id: 'j1', status: 'running', deleted: 4, automatic: false }] }),
  });
  await settle(() => !ui.$('cancel').hidden);
  ui.$('cancel').click();
  await settle(() => ui.posts().length === 1);
  assert.deepEqual(ui.posts(), ['/api/v1/admin/evidence-retention/cancel']);
  assert.equal(ui.confirmations[0].confirmLabel, 'Cancel purge');
});
