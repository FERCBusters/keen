// Source management in Admin → Evidence: opening a source's mapping rules,
// mailbox and feed sources, input labels and settings, inputs changed
// elsewhere, and what follows a save.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
const ROOT = '/api/v1/admin';

function connection(overrides = {}) {
  return {
    id: 'forge',
    source: 'forgejo',
    name: 'My Forgejo',
    enabled: true,
    version: 4,
    configuration: { base_url: 'https://git.example' },
    inputs: { users: [{ user: 'alice' }] },
    managed_by: 'database',
    ...overrides,
  };
}
const type = (source, label) => ({
  source,
  label,
  enabled: true,
  fields: ['base_url'],
  credential_fields: [],
});

async function hub(t, data = {}) {
  const calls = [];
  const events = [];
  const state = {
    definitions: data.definitions ?? { collectors: [], rules: [] },
    integrations: data.integrations ?? { connections: [], collectors: [], demo_mode: false },
    configurations: data.configurations ?? { items: [] },
    named: data.named ?? { types: [type('forgejo', 'Forgejo')], connections: [connection()] },
    failLoad: false,
  };
  const copy = (value) => (value === undefined ? undefined : JSON.parse(JSON.stringify(value)));
  const record = (method, handler) => async (url, body) => {
    calls.push({ method, url, body: copy(body) });
    if (!handler) throw new Error(`Unexpected ${method} ${url}`);
    return handler(url, body);
  };
  const ui = await loadUi('pages/connection-hub.js', {
    html,
    url: 'https://keen.example/admin.html',
    modules: {
      'pages/confirm-modal.js': { confirmAction: async () => data.confirm ?? true },
    },
    globals: { structuredClone },
    setup(w) {
      w.HTMLDialogElement.prototype.showModal = function () {
        this.open = true;
      };
      w.HTMLDialogElement.prototype.close = function () {
        this.open = false;
      };
      w.HTMLElement.prototype.scrollIntoView = () => {};
      for (const name of ['keen-collection-create', 'keen-filter-source'])
        w.addEventListener(name, (event) => events.push({ name, detail: copy(event.detail) }));
    },
    vendor: {
      // The design system's errorText reads the public detail of a JSON error body.
      errorText: (error) => {
        const raw = String(error?.message ?? error);
        try {
          return JSON.parse(raw).detail || raw;
        } catch {
          return raw;
        }
      },
      apiGet: async (url) => {
        calls.push({ method: 'GET', url });
        if (url === ROOT + '/evidence-definitions') return structuredClone(state.definitions);
        if (url === ROOT + '/integrations') return structuredClone(state.integrations);
        if (url === ROOT + '/managed-configurations') return structuredClone(state.configurations);
        if (url === ROOT + '/source-connections') {
          if (state.failLoad) throw new Error(JSON.stringify({ detail: 'Sources unavailable' }));
          return structuredClone(state.named);
        }
        if (data.get) return data.get(url);
        throw new Error('Unexpected GET ' + url);
      },
      apiPut: record('PUT', data.put),
      apiPost: record('POST', data.post),
    },
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById('hub-' + id);
  const list = el('list');
  await settle(() => list.childElementCount > 0);
  const loads = () =>
    calls.filter((c) => c.method === 'GET' && c.url === ROOT + '/source-connections').length;
  const card = (title) =>
    [...list.querySelectorAll('section')].find((s) => s.querySelector('h3').textContent === title);
  const button = (scope, text) =>
    [...scope.querySelectorAll('button')].find((b) => b.textContent === text);
  return { ...ui, calls, events, state, el, list, loads, card, button };
}

const forgejoInput = (connectionId, entry) => ({
  adapter: 'forgejo',
  connection_id: connectionId,
  section: 'users',
  key: entry.user,
  name: entry.user,
  entry,
});

test('View mapping rules opens the mapping view for an environment default source', async (t) => {
  const ui = await hub(t, {
    configurations: { items: [{ name: 'forgejo' }] },
    definitions: {
      collectors: [forgejoInput(null, { user: 'carol' }), forgejoInput('forge', { user: 'alice' })],
      rules: [],
    },
  });
  await ui.button(ui.card('Forgejo · Environment default'), 'View mapping rules').onclick();
  assert.deepEqual(ui.events, [
    {
      name: 'keen-filter-source',
      detail: { source: 'forgejo', connection_id: 'env:forgejo', input: null },
    },
  ]);
  assert.equal(ui.window.location.hash, '#evidence-config');
  assert.equal(ui.document.getElementById('evidence-mapping-view').hidden, false);
  // Adding a rule from a connection's input names that connection.
  await ui.button(ui.card('My Forgejo'), 'Add mapping rule').onclick();
  assert.deepEqual(ui.events.at(-1).detail.connection_id, 'forge');
  assert.equal(ui.events.at(-1).name, 'keen-collection-create');
});

test('mailbox sources show the folder they read, INBOX unless another is saved', async (t) => {
  const mailbox = (id, name, configuration) =>
    connection({ id, source: 'imap', name, configuration, inputs: {} });
  const ui = await hub(t, {
    named: {
      types: [type('imap', 'IMAP mail')],
      connections: [
        mailbox('m1', 'Default mailbox', { host: 'mail.example' }),
        mailbox('m2', 'Alerts mailbox', { host: 'mail.example', folder: '<b>Alerts</b>' }),
      ],
    },
  });
  assert.equal(
    ui.card('Default mailbox').querySelector('p').textContent,
    'IMAP mail · Enabled · Account connection · Folder: INBOX',
  );
  assert.equal(
    ui.card('Alerts mailbox').querySelector('p').textContent,
    'IMAP mail · Enabled · Account connection · Folder: <b>Alerts</b>',
  );
  assert.equal(ui.list.querySelector('b'), null);
  // A mailbox collects without separate inputs.
  assert.ok(ui.button(ui.card('Default mailbox'), 'Collect now'));
  assert.equal(ui.button(ui.card('Default mailbox'), 'Add input'), undefined);
});

test('a feed source is ready once it has its feed', async (t) => {
  const feed = (id, name, feeds) =>
    connection({
      id,
      source: 'rss',
      name,
      configuration: { base_url: 'https://news.example' },
      inputs: { feeds },
    });
  const ui = await hub(t, {
    named: {
      types: [type('rss', 'RSS')],
      connections: [
        feed('f1', 'Security news', [{ url: 'https://news.example/feed.xml' }]),
        feed('f2', 'Empty feed', []),
      ],
    },
  });
  const ready = ui.card('Security news');
  assert.ok(ui.button(ready, 'Collect now'));
  assert.ok(ui.button(ready, 'View mapping rules'));
  assert.doesNotMatch(ready.textContent, /Add an input to choose/);
  const empty = ui.card('Empty feed');
  assert.equal(ui.button(empty, 'Collect now'), undefined);
  assert.match(empty.textContent, /Add an input to choose what this source collects/);
});

test('Risk Ledger and Redmine inputs explain their wildcard identifiers', async (t) => {
  const ui = await hub(t, {
    named: {
      types: [type('riskledger', 'Risk Ledger'), type('redmine', 'Redmine')],
      connections: [
        connection({ id: 'rl', source: 'riskledger', name: 'Vendors', inputs: {} }),
        connection({ id: 'rm', source: 'redmine', name: 'Tracker', inputs: {} }),
      ],
    },
  });
  const labels = () =>
    [...ui.el('collection-fields').querySelectorAll('label')].map((l) => l.textContent);
  await ui.button(ui.card('Vendors'), 'Add input').onclick();
  assert.deepEqual(labels(), ["Organisation UUID (or * for the API key's organisation)", 'Label']);
  await ui.button(ui.card('Tracker'), 'Add input').onclick();
  assert.deepEqual(labels(), [
    'Project ID / identifier (or * for all accessible projects)',
    'Label',
  ]);
});

test('clearing the additional settings saves just the entered fields', async (t) => {
  const ui = await hub(t, { put: async (url, body) => body });
  await ui.button(ui.card('My Forgejo'), 'Add input').onclick();
  ui.el('collection-type').value = 'users';
  ui.el('collection-type').dispatchEvent(new ui.window.Event('change'));
  ui.el('collection-fields').querySelector('[data-field="user"]').value = 'erin';
  ui.el('collection-extra').value = '';
  await ui.el('collection-save').onclick();
  assert.equal(ui.el('collection-error').textContent, '');
  assert.deepEqual(ui.calls.find((c) => c.method === 'PUT').body.inputs, {
    users: [{ user: 'alice' }, { user: 'erin' }],
  });
});

test('after saving an input, Collect now queues that source and says where to look', async (t) => {
  const ui = await hub(t, {
    put: async (url, body) => body,
    post: async () => ({ queued: true }),
  });
  await ui.button(ui.card('My Forgejo'), 'Add input').onclick();
  ui.el('collection-fields').querySelector('[data-field="org"]').value = 'platform';
  await ui.el('collection-save').onclick();
  const status = ui.el('status');
  assert.equal(
    status.querySelector('p').textContent,
    'Input saved for My Forgejo. Collect evidence now, or add a mapping rule.',
  );
  await ui.button(status, 'Collect now').onclick();
  assert.deepEqual(ui.calls.at(-1), {
    method: 'POST',
    url: ROOT + '/source-connections/forge/run',
    body: {},
  });
  assert.equal(
    status.querySelector('p').textContent,
    'Collection queued for My Forgejo. Check the Events view for collected evidence.',
  );
});

test('removing an input already gone from the source saves the source without it', async (t) => {
  const ui = await hub(t, {
    named: {
      types: [type('forgejo', 'Forgejo')],
      connections: [connection({ inputs: { organizations: [{ org: 'platform' }] } })],
    },
    definitions: { collectors: [forgejoInput('forge', { user: 'alice' })], rules: [] },
    put: async (url, body) => body,
  });
  await ui.button(ui.card('My Forgejo'), 'Remove input').onclick();
  const put = ui.calls.find((c) => c.method === 'PUT');
  assert.equal(put.url, ROOT + '/source-connections/forge');
  assert.deepEqual(put.body.inputs, { organizations: [{ org: 'platform' }], users: [] });
  assert.equal(put.body.version, 4);
});

test('opening source management without naming a source lists every source', async (t) => {
  const ui = await hub(t, {
    named: {
      types: [type('forgejo', 'Forgejo'), type('loki', 'Loki')],
      connections: [connection(), connection({ id: 'logs', source: 'loki', name: 'Logs' })],
    },
  });
  ui.el('search').value = 'loki';
  ui.el('search').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.list.querySelectorAll('section').length, 1);
  ui.window.dispatchEvent(new ui.window.CustomEvent('keen-manage-source'));
  assert.equal(ui.el('search').value, '');
  assert.deepEqual(
    [...ui.list.querySelectorAll('h3')].map((h) => h.textContent),
    ['My Forgejo', 'Logs'],
  );
});

test('Refresh reloads the sources and reports a failed reload', async (t) => {
  const ui = await hub(t);
  assert.equal(ui.loads(), 1);
  ui.el('refresh').click();
  await settle(() => ui.loads() === 2);
  ui.state.failLoad = true;
  ui.el('refresh').click();
  await settle(() => ui.el('status').hidden === false);
  assert.equal(ui.el('status').textContent, 'Sources unavailable');
  assert.equal(ui.el('status').className, 'alert alert-danger');
});

test('a new source whose list cannot reload says so after saving', async (t) => {
  const ui = await hub(t, {
    post: async (url, body) => {
      ui.state.failLoad = true;
      return { ...body, id: 'new', version: 1, managed_by: 'database' };
    },
  });
  ui.el('start-setup').click();
  const dialog = [...ui.document.querySelectorAll('body > dialog')].at(-1);
  dialog.querySelector('input[required]').value = 'Second forge';
  dialog.querySelector('[data-key="base_url"]').value = 'https://git2.example';
  await dialog.querySelector('form').onsubmit({ preventDefault() {} });
  await settle(() => ui.el('status').hidden === false);
  assert.equal(ui.el('status').textContent, 'Sources unavailable');
  // The input editor waits for a list that loads.
  assert.equal(ui.el('collection').open, false);
});

test('Clear inputs on an environment default previews the evidence it removes', async (t) => {
  const ui = await hub(t, {
    configurations: { items: [{ name: 'forgejo' }] },
    definitions: { collectors: [forgejoInput(null, { user: 'carol' })], rules: [] },
    get: async (url) => {
      assert.equal(url, ROOT + '/evidence-connections/builtin%3Aforgejo/removal');
      return {
        kind: 'builtin',
        name: 'forgejo',
        collections: 1,
        definitions: [],
        automatic_mappings: 0,
        fingerprint: 'f1',
      };
    },
  });
  await ui.button(ui.card('Forgejo · Environment default'), 'Clear inputs').onclick();
  assert.equal(ui.el('confirm').open, true);
  assert.equal(ui.el('confirm-heading').textContent, 'Clear connection');
  ui.el('cancel').click();
  assert.equal(ui.el('confirm').open, false);
});
