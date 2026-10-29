import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
const plain = (v) => JSON.parse(JSON.stringify(v));
const HOSTILE = '<img src=x onerror="globalThis.pwned=1">';
const root = '/api/v1/admin/integrations';

const templateDefinition = {
  method: 'GET',
  path: '/v1/checks',
  query: { status: 'all' },
  headers: { Accept: 'application/json' },
  body: null,
  records_path: '/results',
  pagination: { mode: 'page', parameter: 'page', size: 50, size_parameter: 'per_page', start: 1 },
  interval_minutes: 30,
  since_parameter: 'since',
  initial_days: 14,
  max_pages: 5,
  max_records: 500,
  output: 'evidence',
  enrichments: [],
  fields: {
    id: { path: '/id', transform: 'text', values: {} },
    summary: { path: '/title', transform: 'text' },
  },
};
const baseData = () => ({
  connections: [
    {
      id: 'k1',
      name: HOSTILE + 'Monitoring API',
      base_url: 'https://monitor.example.test',
      auth_kind: 'bearer',
      has_secret: true,
      version: 3,
      auth_options: { token_path: '/token', scope: 'read' },
      auth_name: 'Authorization',
      username: '',
    },
  ],
  collectors: [
    {
      id: 'c1',
      name: HOSTILE + 'Uptime checks',
      connection_id: 'k1',
      version: 4,
      live_revision: 2,
      enabled: true,
      last_success: '2026-10-01T10:00:00',
      next_run: '2026-10-01T11:00:00',
      failures: 0,
      draft: { ...templateDefinition, path: '/v1/uptime' },
    },
    {
      id: 'c2',
      name: 'Drafted',
      connection_id: 'gone',
      version: 1,
      live_revision: null,
      enabled: false,
      failures: 0,
    },
    {
      id: 'c3',
      name: 'Paused one',
      connection_id: 'k1',
      version: 1,
      live_revision: 1,
      enabled: false,
      failures: 0,
    },
    {
      id: 'c4',
      name: 'Broken',
      connection_id: 'k1',
      version: 1,
      live_revision: 1,
      enabled: true,
      failures: 3,
    },
    {
      id: 'c5',
      name: 'Failing',
      connection_id: 'k1',
      version: 1,
      live_revision: 1,
      enabled: true,
      failures: 1,
    },
    {
      id: 'c6',
      name: 'Waiting',
      connection_id: 'k1',
      version: 1,
      live_revision: 1,
      enabled: true,
      failures: 0,
    },
    {
      id: 'c7',
      name: 'Busy',
      connection_id: 'k1',
      version: 1,
      live_revision: 1,
      enabled: true,
      failures: 0,
    },
  ],
  runs: [
    {
      id: 'run-7',
      collector_id: 'c7',
      status: 'running',
      created_at: '2026-10-01T10:05:00',
      revision: 1,
      message: HOSTILE + 'Collecting',
      result: { records: 10, processed: 4 },
    },
    {
      id: 'run-5',
      collector_id: 'c5',
      status: 'failed',
      created_at: '2026-10-01T10:00:00',
      revision: 1,
      message: 'HTTP 500',
    },
    {
      id: 'run-p',
      collector_id: 'c1',
      preview: true,
      status: 'succeeded',
      created_at: '2026-10-01T09:00:00',
      revision: 4,
      result: { samples: [{ evidence: { summary: 'Sample check' }, raw: { id: 1 } }] },
    },
    {
      id: 'run-x',
      collector_id: 'deleted',
      status: 'queued',
      created_at: '2026-10-01T08:00:00',
      revision: 1,
    },
  ],
  templates: [
    {
      id: 'generic',
      name: 'Generic JSON API',
      description: 'Any JSON list endpoint',
      definition: templateDefinition,
    },
    {
      id: 'other',
      name: 'Other template',
      description: 'Second template',
      definition: { ...templateDefinition, path: '/v2/items', fields: {} },
    },
  ],
  measures: [{ id: 'm1', name: 'Uptime', framework: 'A', unit: 'percent' }],
});

async function builder(t, options = {}) {
  const state = {
    data: options.data ?? baseData(),
    posted: [],
    put: [],
    gets: [],
    prompts: [],
    intervals: [],
    downloads: [],
  };
  const fail = options.fail || {};
  const ui = await loadUi('pages/integrations.js', {
    html,
    url: 'https://keen.example/admin.html' + (options.hash || '#integrations'),
    setup(window) {
      // jsdom reports every document as hidden; the page pauses polling for hidden tabs.
      Object.defineProperty(window.document, 'hidden', { value: false });
      window.HTMLDialogElement.prototype.showModal = function () {
        this.open = true;
      };
      window.HTMLDialogElement.prototype.close = function () {
        this.open = false;
      };
    },
    globals: {
      setInterval: (fn, ms) => (state.intervals.push({ fn, ms }), state.intervals.length),
      Blob,
      ...options.globals,
    },
    modules: {
      'pages/confirm-modal.js': {
        confirmAction: async (asked) => {
          state.prompts.push(asked);
          return options.confirm ?? true;
        },
      },
    },
    vendor: {
      apiGet: async (url) => {
        state.gets.push(url);
        if (url === '/api/v1/me') {
          if (fail.me) throw new Error('Session expired');
          return options.me ?? { is_admin: true };
        }
        if (url === root) {
          if (fail.load) throw new Error('Integrations offline');
          return structuredClone(state.data);
        }
        if (url === `${root}/collectors/c1/revisions`) {
          if (fail.revisions) throw new Error(JSON.stringify({ detail: 'Revisions unavailable' }));
          return {
            items: [
              { revision: 2, created_at: '2026-09-30T10:00:00' },
              { revision: 1, created_at: '2026-09-01T10:00:00' },
            ],
          };
        }
        if (url.startsWith(`${root}/collectors/`) && url.endsWith('/revisions'))
          return { items: [] };
        if (url === `${root}/runs/run-new`)
          return (
            options.watchResult ?? {
              id: 'run-new',
              status: 'succeeded',
              message: 'Preview complete',
              result: { samples: [{ evidence: { summary: HOSTILE }, fields: {} }] },
            }
          );
        throw new Error('Unexpected GET ' + url);
      },
      apiPost: async (url, body) => {
        state.posted.push({ url, body: plain(body) });
        if (fail.post) throw new Error(fail.post);
        if (url === `${root}/collectors`) {
          const created = {
            id: 'c9',
            name: body.name,
            connection_id: body.connection_id,
            version: 1,
            draft: body.definition,
            enabled: false,
            failures: 0,
          };
          state.data.collectors.push(created);
          return created;
        }
        if (url.endsWith('/preview')) return { id: 'run-new', status: 'queued', message: 'Queued' };
        if (url === `${root}/preview-sample`)
          return { status: 'succeeded', message: 'Sample processed', result: { samples: [] } };
        return { ok: true };
      },
      apiPut: async (url, body) => {
        state.put.push({ url, body: plain(body) });
        if (url.startsWith(`${root}/collectors/`)) {
          const id = url.split('/').pop();
          const c = state.data.collectors.find((x) => x.id === id);
          Object.assign(c, { name: body.name, version: c.version + 1, draft: body.definition });
          return structuredClone(c);
        }
        return {};
      },
      esc: (value) =>
        String(value ?? '').replace(
          /[&<>"']/g,
          (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
        ),
    },
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById('ig-' + id);
  const buttonIn = (scope, text) =>
    [...scope.querySelectorAll('button')].find((b) => b.textContent === text);
  await settle(
    () => state.gets.includes(root) || el('status').textContent || state.gets.length === 1,
  );
  await new Promise((r) => setTimeout(r, 5));
  return { ui, el, state, buttonIn };
}
test('without paused ingesters the admin overview shows no alert', async (t) => {
  const data = baseData();
  data.collectors = data.collectors.map((c) => ({ ...c, failures: 0 }));
  const { el } = await builder(t, { data });
  assert.equal(el('attention').textContent, '');
  assert.match(el('overview').textContent, /Need attention0/);
});

const cardFor = (ctx, name) =>
  [...ctx.el('list').querySelectorAll('.ig-card')].find(
    (c) => c.querySelector('h3').textContent === name,
  );

test('the overview summarises integration health and each status', async (t) => {
  const ctx = await builder(t);
  const { el, ui } = ctx;
  assert.deepEqual(
    [...el('overview').querySelectorAll('.kpi')].map((s) => s.textContent),
    ['Custom API ingesters7', 'Scheduled5', 'Need attention1'],
  );
  // The counters sit with the builder; the admin overview only raises failures.
  assert.ok(el('overview').closest('#pane-integrations'));
  assert.equal(el('attention').closest('#pane-overview').id, 'pane-overview');
  assert.equal(el('attention').querySelector('.kpi'), null);
  assert.match(
    el('attention').textContent,
    /A custom API ingester is paused after repeated failures\. Review them in API ingester builder\./,
  );
  assert.equal(ui.document.querySelector('#ig-list img, #ig-runs img, #ig-connections img'), null);
  assert.equal(ui.window.pwned, undefined);
  const status = (name) => cardFor(ctx, name).querySelector('.ig-state').textContent;
  assert.equal(status(HOSTILE + 'Uptime checks'), 'healthy');
  assert.equal(status('Drafted'), 'draft');
  assert.equal(status('Paused one'), 'paused');
  assert.equal(status('Broken'), 'paused after failures');
  assert.equal(status('Failing'), 'failed');
  assert.equal(status('Waiting'), 'scheduled');
  assert.equal(status('Busy'), 'running');
  assert.ok(cardFor(ctx, 'Busy').querySelector('.spinner-border'));
  assert.equal(cardFor(ctx, 'Broken').querySelector('.ig-state').className, 'ig-state bad');
  assert.match(
    cardFor(ctx, HOSTILE + 'Uptime checks').textContent,
    /Monitoring API · Live revision 2 · Draft 4/,
  );
  assert.match(
    cardFor(ctx, 'Drafted').textContent,
    /Connection · Unpublished · Draft 1.*Last success: Never.*Next run: Paused/s,
  );
  assert.match(
    cardFor(ctx, 'Busy').textContent,
    /<img src=x onerror="globalThis\.pwned=1">Collecting/,
  );
  assert.equal(ctx.buttonIn(cardFor(ctx, 'Paused one'), 'Resume').textContent, 'Resume');

  const runs = [...el('runs').querySelectorAll('.ig-run')];
  assert.equal(runs.length, 4);
  assert.equal(runs[0].querySelector('progress').getAttribute('value'), '4');
  assert.equal(runs[0].querySelector('progress').getAttribute('max'), '10');
  assert.match(runs[2].textContent, /Uptime checks · Preview/);
  assert.match(runs[3].textContent, /deleted.*Waiting for worker/s);
  assert.match(
    el('connections').textContent,
    /Monitoring API · https:\/\/monitor\.example\.test · bearer \(credential saved\)/,
  );

  el('search').value = 'BROK';
  el('search').dispatchEvent(new ui.window.Event('input'));
  assert.equal(el('list').querySelectorAll('.ig-card').length, 1);
  el('search').value = 'nothing';
  el('search').dispatchEvent(new ui.window.Event('input'));
  assert.match(el('list').textContent, /Build your first custom API ingester/);
});

test('collector actions post the draft version and report the outcome', async (t) => {
  const ctx = await builder(t);
  const { el, state, buttonIn } = ctx;
  buttonIn(cardFor(ctx, 'Waiting'), 'Run now').click();
  await settle(
    () => el('status').textContent === 'Collection queued. Follow progress in Recent runs.',
  );
  assert.deepEqual(state.posted[0], { url: `${root}/collectors/c6/run`, body: { version: 1 } });
  buttonIn(cardFor(ctx, 'Waiting'), 'Pause').click();
  await settle(() => el('status').textContent === 'pause completed.');
  buttonIn(cardFor(ctx, 'Paused one'), 'Resume').click();
  await settle(() => el('status').textContent === 'resume completed.');
  assert.equal(state.posted[2].url, `${root}/collectors/c3/resume`);
  assert.equal(el('status').className, 'alert alert-success');

  const events = [];
  ctx.ui.window.addEventListener('keen-integration-rule', (e) => events.push(plain(e.detail)));
  buttonIn(cardFor(ctx, 'Waiting'), 'Evidence definitions').click();
  assert.deepEqual(events, [{ source: 'integration:c6', name: 'Waiting' }]);
  assert.equal(ctx.ui.window.location.hash, '#evidence-config');
});

test('API errors show the server detail, including validation lists', async (t) => {
  const list = await builder(t, {
    fail: {
      post: JSON.stringify({ detail: [{ msg: 'version is stale' }, { msg: 'reload first' }] }),
    },
  });
  list.buttonIn(cardFor(list, 'Waiting'), 'Run now').click();
  await settle(() => list.el('status').textContent === 'version is stale; reload first');
  assert.equal(list.el('status').className, 'alert alert-danger');
  const button = list.buttonIn(cardFor(list, 'Waiting'), 'Run now');
  assert.equal(button.disabled, false);
  const text = await builder(t, { fail: { post: 'Worker unavailable' } });
  text.buttonIn(cardFor(text, 'Waiting'), 'Pause').click();
  await settle(() => text.el('status').textContent === 'Worker unavailable');
});

test('demo mode disables configuration and explains why', async (t) => {
  const data = { ...baseData(), demo_mode: true, collectors: [] };
  const ctx = await builder(t, { data });
  const { el, state, buttonIn } = ctx;
  assert.equal(el('new').disabled, true);
  assert.equal(el('add-connection').disabled, true);
  assert.match(el('new').title, /demo environment/);
  assert.match(el('status').textContent, /You are running in a demo environment/);
  assert.equal(el('connection-form').hidden, true);
  assert.match(el('list').textContent, /API integrations in your own Keen installation/);
  const deleteConnection = buttonIn(el('connections'), 'Delete connection');
  assert.equal(deleteConnection.disabled, true);
  el('status').textContent = '';
  buttonIn(el('connections'), 'Edit').click();
  assert.match(el('status').textContent, /demo environment/);
  assert.equal(el('connection-form').hidden, true);
  assert.equal(state.posted.length, 0);
});

test('demo mode blocks collector actions and editing', async (t) => {
  const data = { ...baseData(), demo_mode: true };
  const ctx = await builder(t, { data });
  const { el, state, buttonIn } = ctx;
  el('status').textContent = '';
  buttonIn(cardFor(ctx, 'Waiting'), 'Run now').click();
  await settle(() => /demo environment/.test(el('status').textContent));
  buttonIn(cardFor(ctx, 'Waiting'), 'Edit / test').click();
  assert.equal(el('editor').hidden, true);
  assert.equal(buttonIn(cardFor(ctx, 'Waiting'), 'Delete ingester').disabled, true);
  assert.equal(state.posted.length, 0);
});

test('connections are created and edited without echoing credentials', async (t) => {
  const { el, state, buttonIn, ui } = await builder(t);
  const form = el('connection-form');
  const save = form.querySelector('button:not([type])');
  el('add-connection').click();
  assert.equal(form.hidden, false);
  assert.equal(el('ca').value, 'none');
  assert.equal(el('token-path').value, '/oauth/token');
  el('cn').value = 'Ticketing';
  el('cu').value = 'https://tickets.example.test';
  el('ca').value = 'basic';
  el('cuser').value = 'svc';
  el('csecret').value = 'synthetic-token-123';
  el('token-path').value = '';
  form.requestSubmit(save);
  await settle(() => el('status').textContent === 'Connection saved.');
  assert.deepEqual(state.posted[0], {
    url: `${root}/connections`,
    body: {
      name: 'Ticketing',
      base_url: 'https://tickets.example.test',
      auth_kind: 'basic',
      auth_name: '',
      username: 'svc',
      auth_options: { token_path: '/oauth/token', scope: '' },
      secret: 'synthetic-token-123',
      version: 0,
    },
  });
  assert.equal(el('csecret').value, '');
  assert.equal(form.hidden, true);

  buttonIn(el('connections'), 'Edit').click();
  assert.equal(el('connection-id').value, 'k1');
  assert.equal(el('ch').value, 'Authorization');
  assert.equal(el('scope').value, 'read');
  assert.equal(el('token-path').value, '/token');
  assert.equal(el('csecret').value, '');
  el('status').textContent = '';
  form.requestSubmit(save);
  await settle(() => state.put.length === 1 && el('status').textContent === 'Connection saved.');
  assert.equal(state.put[0].url, `${root}/connections/k1`);
  // A blank credential field keeps the stored secret.
  assert.equal(state.put[0].body.secret, null);
  assert.equal(state.put[0].body.version, 3);
  el('add-connection').click();
  el('cancel-connection').click();
  assert.equal(form.hidden, true);
  assert.equal(ui.document.activeElement === el('cn'), true);
});

test('building an integration without connections starts with a connection', async (t) => {
  const data = { ...baseData(), connections: [], collectors: [] };
  const { el } = await builder(t, { data });
  assert.match(el('connections').textContent, /No custom API connections yet/);
  el('new').click();
  assert.equal(el('status').textContent, 'Add a connection first, then build your integration.');
  assert.equal(el('connections').closest('details').open, true);
  assert.equal(el('connection-form').hidden, false);
  assert.equal(el('editor').hidden, true);
});

test('the guided editor fills from a template and saves a new draft', async (t) => {
  const { el, state, ui, buttonIn } = await builder(t);
  el('new').click();
  assert.equal(el('editor').hidden, false);
  assert.equal(el('home').hidden, true);
  assert.equal(el('heading').textContent, 'Build an integration');
  assert.equal(el('connection').value, 'k1');
  assert.equal(el('path').value, '/v1/checks');
  assert.equal(el('paging').value, 'page');
  assert.equal(el('size-param').value, 'per_page');
  assert.equal(el('fields').children.length, 2);
  assert.equal(el('revisions').textContent, 'Publish your first revision to start a history.');
  assert.equal(el('measure').options[1].textContent, 'Uptime · A (percent)');

  // Step navigation.
  const visibleStep = () =>
    [...ui.document.querySelectorAll('[data-ig-step]')].find((s) => !s.hidden).dataset.igStep;
  assert.equal(visibleStep(), '0');
  assert.equal(el('prev').disabled, true);
  el('next').click();
  assert.equal(visibleStep(), '1');
  buttonIn(el('steps'), '5. Test & publish').click();
  assert.equal(visibleStep(), '4');
  assert.equal(el('next').hidden, true);
  assert.equal(el('steps').querySelector('[aria-current="step"]').textContent, '5. Test & publish');
  el('prev').click();
  assert.equal(visibleStep(), '3');

  el('template').value = 'other';
  el('template').dispatchEvent(new ui.window.Event('change'));
  assert.equal(el('template-description').textContent, 'Second template');
  el('apply-template').click();
  assert.equal(el('path').value, '/v2/items');
  assert.equal(el('fields').children.length, 0);

  el('add-field').click();
  const [name, path, constant, transform] = el('fields').lastElementChild.children;
  assert.equal(name.value, 'system');
  name.value = 'summary';
  path.value = '/title';
  transform.value = 'upper';
  el('add-field').click();
  el('fields').lastElementChild.children[1].value = '/id';
  el('fields').lastElementChild.children[0].value = 'id';
  el('fields').lastElementChild.children[2].value = 'fixed';
  el('measure').value = 'm1';
  el('measure').dispatchEvent(new ui.window.Event('change'));
  assert.equal(el('unit').value, 'percent');
  el('body').value = '{"filter": "all"}';
  el('name').value = 'New ingester';

  el('save').click();
  await settle(() => el('status').textContent === 'Draft saved. Your live revision is unchanged.');
  const saved = state.posted.find((p) => p.url === `${root}/collectors`);
  assert.equal(saved.body.name, 'New ingester');
  assert.equal(saved.body.connection_id, 'k1');
  assert.equal(saved.body.version, 0);
  assert.equal(saved.body.definition.path, '/v2/items');
  assert.equal(saved.body.definition.measure_id, 'm1');
  assert.equal(saved.body.definition.unit, 'percent');
  assert.deepEqual(saved.body.definition.body, { filter: 'all' });
  assert.deepEqual(saved.body.definition.fields, {
    summary: { path: '/title', value: null, transform: 'upper', values: {} },
    id: { path: '/id', value: 'fixed', transform: 'text', values: {} },
  });
  assert.equal(constant.value, '');

  // Removing a field row marks the draft as changed.
  el('fields').lastElementChild.querySelector('button').click();
  assert.equal(el('fields').children.length, 1);
});

test('the editor validates names and duplicate field mappings', async (t) => {
  const { el, state } = await builder(t);
  el('new').click();
  el('save').click();
  await settle(
    () => el('status').textContent === 'Give the integration a name and select a connection.',
  );
  el('name').value = 'Duplicate fields';
  el('add-field').click();
  el('add-field').click();
  el('save').click();
  await settle(() => el('status').textContent === 'Each evidence field can be mapped only once');
  assert.equal(state.posted.length, 0);
  el('query').value = '{not json';
  el('write-json').click();
  assert.equal(el('status').className, 'alert alert-danger');
});

test('editing an existing draft previews, publishes and restores revisions', async (t) => {
  const ctx = await builder(t);
  const { el, state, buttonIn } = ctx;
  buttonIn(cardFor(ctx, HOSTILE + 'Uptime checks'), 'Edit / test').click();
  assert.equal(el('heading').textContent, 'Edit ' + HOSTILE + 'Uptime checks');
  assert.equal(el('path').value, '/v1/uptime');
  await settle(() => el('revisions').children.length === 2);
  assert.match(el('revisions').textContent, /Revision 2/);

  el('preview').click();
  await settle(() => /Queued/.test(el('preview-output').textContent));
  assert.equal(state.put[0].url, `${root}/collectors/c1`);
  assert.equal(state.put[0].body.version, 4);
  assert.deepEqual(state.posted.at(-1), {
    url: `${root}/collectors/c1/preview`,
    body: { version: 5 },
  });
  // The polling loop follows the preview run until it finishes.
  const poll = state.intervals.find((i) => i.ms === 5000).fn;
  poll();
  await settle(() => /Preview complete/.test(el('preview-output').textContent));
  const sample = el('preview-output').querySelector('details');
  assert.equal(sample.querySelector('summary').textContent, HOSTILE);
  assert.equal(el('preview-output').querySelector('img'), null);
  const loads = state.gets.filter((u) => u === root).length;
  poll();
  await new Promise((r) => setTimeout(r, 10));
  // The editor is open and nothing is being watched, so polling does not reload.
  assert.equal(state.gets.filter((u) => u === root).length, loads);

  el('publish').click();
  await settle(
    () =>
      el('status').textContent ===
      'Published. Scheduled collection is enabled unless paused after failures.',
  );
  assert.equal(state.posted.at(-1).url, `${root}/collectors/c1/publish`);

  buttonIn(el('revisions'), 'Restore live revision').click();
  await settle(() => el('status').textContent === 'rollback completed.');
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(state.posted.at(-1).url, `${root}/collectors/c1/rollback`);
  assert.deepEqual(state.posted.at(-1).body, { version: 6, revision: 2 });
  assert.equal(state.prompts.at(-1).title, 'Restore live revision');

  el('reset').click();
  await settle(() => el('status').textContent === 'reset-cursor completed.');
  assert.equal(state.posted.at(-1).url, `${root}/collectors/c1/reset-cursor`);
  assert.equal(state.prompts.at(-1).title, 'Replay history on the next run');
});

test('declined confirmations keep the draft and cursor unchanged', async (t) => {
  const ctx = await builder(t, { confirm: false });
  const { el, state, buttonIn, ui } = ctx;
  buttonIn(cardFor(ctx, HOSTILE + 'Uptime checks'), 'Edit / test').click();
  await settle(() => el('revisions').children.length === 2);
  buttonIn(el('revisions'), 'Restore live revision').click();
  el('reset').click();
  el('path').value = '/changed';
  el('builder').dispatchEvent(new ui.window.Event('input'));
  el('apply-template').click();
  assert.equal(el('path').value, '/changed');
  const unload = new ui.window.Event('beforeunload', { cancelable: true });
  ui.window.dispatchEvent(unload);
  assert.equal(unload.defaultPrevented, true);
  el('close').click();
  assert.equal(el('editor').hidden, false);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(state.posted.length, 0);
  assert.deepEqual(
    state.prompts.map((p) => p.title),
    [
      'Restore live revision',
      'Replay history on the next run',
      'Use this template',
      'Discard unsaved changes',
    ],
  );
});

test('a revision history failure is reported', async (t) => {
  const ctx = await builder(t, { fail: { revisions: true } });
  ctx.buttonIn(cardFor(ctx, HOSTILE + 'Uptime checks'), 'Edit / test').click();
  await settle(() => ctx.el('status').textContent === 'Revisions unavailable');
});

test('JSON definitions round-trip through the form, files and sample previews', async (t) => {
  const created = [];
  const ctx = await builder(t, {
    globals: {
      URL: Object.assign(class extends URL {}, {
        createObjectURL: (blob) => (created.push(blob), 'blob:keen/1'),
        revokeObjectURL: () => {},
      }),
    },
  });
  const { el, state, ui } = ctx;
  el('new').click();
  el('write-json').click();
  assert.equal(JSON.parse(el('json').value).path, '/v1/checks');
  el('json').value = JSON.stringify({ ...templateDefinition, path: '/from-json' });
  el('read-json').click();
  assert.equal(el('path').value, '/from-json');
  assert.equal(
    el('status').textContent,
    'JSON loaded into the form. Save and test before publishing.',
  );
  el('json').value = '{oops';
  el('read-json').click();
  assert.equal(el('status').className, 'alert alert-danger');

  el('export').click();
  assert.equal(created.length, 1);
  assert.equal(JSON.parse(await created[0].text()).path, '/from-json');
  assert.equal(created[0].type, 'application/json');

  const importFile = (file) => {
    Object.defineProperty(el('import'), 'files', { value: [file], configurable: true });
    el('import').dispatchEvent(new ui.window.Event('change'));
  };
  importFile({ size: 70000, text: async () => '{}' });
  await settle(() => el('status').textContent === 'Definition exceeds 64 KiB');
  importFile({
    size: 100,
    text: async () => JSON.stringify({ ...templateDefinition, path: '/imported' }),
  });
  await settle(() => el('path').value === '/imported');
  Object.defineProperty(el('import'), 'files', { value: [], configurable: true });
  el('import').dispatchEvent(new ui.window.Event('change'));

  el('sample-json').value = '[{"id": "s1"}]';
  el('sample-test').click();
  await settle(() => /No records returned/.test(el('preview-output').textContent));
  const sample = state.posted.find((p) => p.url === `${root}/preview-sample`);
  assert.deepEqual(sample.body.response, [{ id: 's1' }]);
  assert.equal(sample.body.collector_id, null);
  assert.equal(sample.body.definition.path, '/imported');
});

test('a recent preview run can be reopened from the run list', async (t) => {
  const { el, buttonIn, ui } = await builder(t);
  buttonIn(el('runs'), 'View preview').click();
  assert.equal(el('editor').hidden, false);
  assert.equal(
    [...ui.document.querySelectorAll('[data-ig-step]')].find((s) => !s.hidden).dataset.igStep,
    '4',
  );
  assert.equal(el('preview-output').querySelector('summary').textContent, 'Sample check');
  el('close').click();
  assert.equal(el('home').hidden, false);
});

test('the list refreshes on its schedule, on demand and after connection changes', async (t) => {
  const { el, state, ui } = await builder(t);
  const loads = () => state.gets.filter((u) => u === root).length;
  assert.equal(loads(), 1);
  // A load in progress ignores further refreshes, so let each one finish.
  const tick = () => new Promise((r) => setTimeout(r, 10));
  state.intervals.find((i) => i.ms === 5000).fn();
  await settle(() => loads() === 2);
  await tick();
  el('refresh').click();
  await settle(() => loads() === 3);
  await tick();
  ui.window.dispatchEvent(new ui.window.Event('keen-connections-changed'));
  await settle(() => loads() === 4);
  await tick();
  ui.window.location.hash = '#evidence-config';
  state.intervals.find((i) => i.ms === 5000).fn();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(loads(), 4);
});

test('non-administrators and failed sessions never load integrations', async (t) => {
  const reader = await builder(t, { me: { is_admin: false } });
  assert.ok(!reader.state.gets.includes(root));
  reader.state.intervals.find((i) => i.ms === 5000).fn();
  await new Promise((r) => setTimeout(r, 10));
  assert.ok(!reader.state.gets.includes(root));
  const expired = await builder(t, { fail: { me: true } });
  await settle(() => expired.el('status').textContent === 'Session expired');
  assert.ok(!expired.state.gets.includes(root));
});
