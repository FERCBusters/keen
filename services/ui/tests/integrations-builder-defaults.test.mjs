// The API ingester builder: defaults for sparse definitions, cleared JSON
// settings, run and preview states, a gateway error body, and requests that
// fail while a preview is followed.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
const plain = (v) => JSON.parse(JSON.stringify(v));
const root = '/api/v1/admin/integrations';

const definition = {
  method: 'GET',
  path: '/v1/checks',
  query: {},
  headers: {},
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
  fields: {},
};
const collector = (overrides = {}) => ({
  id: 'c1',
  name: 'Uptime checks',
  connection_id: 'k1',
  version: 4,
  live_revision: 2,
  enabled: true,
  last_success: '2026-10-01T10:00:00',
  next_run: '2026-10-01T11:00:00',
  failures: 0,
  draft: structuredClone(definition),
  ...overrides,
});
const baseData = () => ({
  connections: [
    {
      id: 'k1',
      name: 'Monitoring API',
      base_url: 'https://monitor.example.test',
      auth_kind: 'bearer',
      has_secret: true,
      version: 3,
      auth_options: {},
    },
  ],
  collectors: [collector()],
  runs: [],
  templates: [{ id: 'generic', name: 'Generic', description: 'Any JSON list', definition }],
  measures: [{ id: 'm1', name: 'Uptime', framework: 'A', unit: 'percent' }],
});

async function builder(t, options = {}) {
  const state = {
    data: options.data ?? baseData(),
    posted: [],
    gets: [],
    intervals: [],
    gate: null,
    runReply: options.runReply,
    blobs: [],
  };
  const ui = await loadUi('pages/integrations.js', {
    html,
    url: 'https://keen.example/admin.html#integrations',
    setup(window) {
      Object.defineProperty(window.document, 'hidden', { value: false });
    },
    globals: {
      setInterval: (fn, ms) => state.intervals.push({ fn, ms }),
      Blob,
      URL: Object.assign(class extends URL {}, {
        createObjectURL: (blob) => (state.blobs.push(blob), 'blob:keen/1'),
        revokeObjectURL: () => {},
      }),
    },
    modules: { 'pages/confirm-modal.js': { confirmAction: async () => true } },
    vendor: {
      apiGet: async (url) => {
        state.gets.push(url);
        if (url === '/api/v1/me') return { is_admin: true };
        if (url === root) {
          if (state.gate) await state.gate;
          return structuredClone(state.data);
        }
        if (url.endsWith('/revisions')) return { items: [] };
        if (url === `${root}/runs/run-new`) return state.runReply();
        throw new Error('Unexpected GET ' + url);
      },
      apiPost: async (url, body) => {
        state.posted.push({ url, body: plain(body) });
        if (options.post) return options.post(url, body);
        return { ok: true };
      },
      apiPut: async (url, body) => ({
        ...collector(),
        version: body.version + 1,
        draft: body.definition,
      }),
    },
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById('ig-' + id);
  const buttonIn = (scope, text) =>
    [...scope.querySelectorAll('button')].find((b) => b.textContent === text);
  await settle(() => state.gets.includes(root));
  await new Promise((r) => setTimeout(r, 5));
  const card = (name) =>
    [...el('list').querySelectorAll('.ig-card')].find(
      (c) => c.querySelector('h3').textContent === name,
    );
  const importFile = (text) => {
    Object.defineProperty(el('import'), 'files', {
      value: [{ size: text.length, text: async () => text }],
      configurable: true,
    });
    el('import').dispatchEvent(new ui.window.Event('change'));
  };
  return { ui, el, state, buttonIn, card, importFile };
}

test('a gateway error without a detail is shown as it was sent', async (t) => {
  const { el, buttonIn, card } = await builder(t, {
    post: () => {
      throw new Error('{"message":"Service unavailable"}');
    },
  });
  buttonIn(card('Uptime checks'), 'Run now').click();
  await settle(() => el('status').className === 'alert alert-danger');
  assert.equal(el('status').textContent, '{"message":"Service unavailable"}');
});

test('several paused ingesters are raised together', async (t) => {
  const data = baseData();
  data.collectors = [
    collector({ id: 'c1', name: 'First', failures: 3 }),
    collector({ id: 'c2', name: 'Second', failures: 5 }),
  ];
  const { el } = await builder(t, { data });
  assert.match(
    el('attention').textContent,
    /^Some custom API ingesters are paused after repeated failures\./,
  );
});

test('a run without a message waits for its worker, and progress starts at zero', async (t) => {
  const data = baseData();
  data.runs = [
    {
      id: 'r1',
      collector_id: 'c1',
      status: 'running',
      created_at: '2026-10-01',
      revision: 2,
      result: { records: 8 },
    },
  ];
  const { el, card } = await builder(t, { data });
  assert.match(card('Uptime checks').textContent, /Waiting for worker/);
  const progress = el('runs').querySelector('progress');
  assert.equal(progress.getAttribute('max'), '8');
  assert.equal(progress.getAttribute('value'), '0');
});

test('a refresh while the list is loading is not repeated', async (t) => {
  const { el, state } = await builder(t);
  const loads = () => state.gets.filter((u) => u === root).length;
  let release;
  state.gate = new Promise((resolve) => {
    release = resolve;
  });
  el('refresh').click();
  state.intervals.find((i) => i.ms === 5000).fn();
  await settle(() => loads() === 2);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(loads(), 2);
  release();
  state.gate = null;
  await settle(() => !el('refresh').disabled);
});

test('an imported definition with only a path takes the default settings', async (t) => {
  const { el, importFile } = await builder(t);
  el('new').click();
  importFile(JSON.stringify({ path: '/v1/items' }));
  await settle(() => el('path').value === '/v1/items');
  const values = Object.fromEntries(
    [
      'method',
      'enrichments',
      'query',
      'headers',
      'body',
      'records',
      'paging',
      'param',
      'next-path',
      'size',
      'size-param',
      'start',
      'interval',
      'since',
      'days',
      'pages',
      'record-limit',
      'output',
      'measure',
      'unit',
    ].map((id) => [id, el(id).value]),
  );
  assert.deepEqual(values, {
    method: '',
    enrichments: '[]',
    query: '{}',
    headers: '{}',
    body: '',
    records: '',
    paging: 'none',
    param: 'page',
    'next-path': '/next',
    size: '100',
    'size-param': 'limit',
    start: '1',
    interval: '60',
    since: '',
    days: '7',
    pages: '20',
    'record-limit': '2000',
    output: 'evidence',
    measure: '',
    unit: '',
  });
  assert.equal(el('fields').children.length, 0);
  // A request body is shown as formatted JSON.
  importFile(JSON.stringify({ path: '/v1/search', body: { status: 'open' } }));
  await settle(() => el('path').value === '/v1/search');
  assert.deepEqual(JSON.parse(el('body').value), { status: 'open' });
});

test('cleared JSON settings are written as empty values', async (t) => {
  const { el } = await builder(t);
  el('new').click();
  el('enrichments').value = '[{"path": "/owner"}]';
  el('query').value = '{"state": "open"}';
  el('headers').value = '{"Accept": "application/json"}';
  el('write-json').click();
  const typed = JSON.parse(el('json').value);
  assert.deepEqual(
    [typed.enrichments, typed.query, typed.headers],
    [[{ path: '/owner' }], { state: 'open' }, { Accept: 'application/json' }],
  );
  for (const id of ['enrichments', 'query', 'headers']) {
    el(id).value = '';
    el('write-json').click();
    assert.equal(el('status').hidden, true, id);
  }
  const written = JSON.parse(el('json').value);
  assert.deepEqual([written.enrichments, written.query, written.headers], [[], {}, {}]);
});

test('choosing no measure clears its unit', async (t) => {
  const { ui, el } = await builder(t);
  el('new').click();
  el('measure').value = 'm1';
  el('measure').dispatchEvent(new ui.window.Event('change'));
  assert.equal(el('unit').value, 'percent');
  el('measure').value = '';
  el('measure').dispatchEvent(new ui.window.Event('change'));
  assert.equal(el('unit').value, '');
});

test('sample previews label unsummarised evidence and count skipped records as returned', async (t) => {
  let reply;
  const { el, buttonIn, card, state } = await builder(t, {
    post: (url) => (url === `${root}/preview-sample` ? reply : { ok: true }),
  });
  // Testing a sample while editing names the ingester being edited.
  buttonIn(card('Uptime checks'), 'Edit / test').click();
  el('sample-json').value = '[{"id": 1}]';
  reply = {
    status: 'succeeded',
    message: 'Sample processed',
    result: { samples: [{ evidence: {} }] },
  };
  el('sample-test').click();
  await settle(() => el('preview-output').querySelector('summary'));
  assert.equal(el('preview-output').querySelector('summary').textContent, 'Sample evidence');
  assert.equal(state.posted.at(-1).body.collector_id, 'c1');
  reply = {
    status: 'succeeded',
    message: 'All skipped',
    result: { samples: [], skipped: { count: 2 } },
  };
  el('sample-test').click();
  await settle(() => /All skipped/.test(el('preview-output').textContent));
  assert.doesNotMatch(el('preview-output').textContent, /No records returned/);
});

test('a preview whose progress cannot be read stops being followed and says why', async (t) => {
  let reads = 0;
  const { el, state, buttonIn, card } = await builder(t, {
    post: (url) =>
      url.endsWith('/preview')
        ? { id: 'run-new', status: 'queued', message: 'Queued' }
        : { ok: true },
    runReply: () => {
      reads++;
      throw new Error(JSON.stringify({ detail: 'Run not found' }));
    },
  });
  buttonIn(card('Uptime checks'), 'Edit / test').click();
  el('preview').click();
  await settle(() => /Queued/.test(el('preview-output').textContent));
  const poll = state.intervals.find((i) => i.ms === 5000).fn;
  poll();
  await settle(() => el('status').textContent === 'Run not found');
  assert.equal(el('status').className, 'alert alert-danger');
  poll();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(reads, 1);
});

test('the builder form is never submitted by the browser', async (t) => {
  const { ui, el } = await builder(t);
  el('new').click();
  const submit = new ui.window.Event('submit', { cancelable: true });
  el('builder').dispatchEvent(submit);
  assert.equal(submit.defaultPrevented, true);
  assert.equal(el('editor').hidden, false);
});

test('an export with invalid JSON settings explains the problem and downloads nothing', async (t) => {
  const { el, state } = await builder(t);
  el('new').click();
  el('headers').value = '{"Accept": ';
  el('export').click();
  assert.equal(el('status').className, 'alert alert-danger');
  assert.notEqual(el('status').textContent, '');
  assert.deepEqual(state.blobs, []);
});
