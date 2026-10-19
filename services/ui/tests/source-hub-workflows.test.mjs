import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

// Source management in Admin → Evidence: listing, collecting, input editing,
// removal and the follow-on steps after a source is defined.
const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
const ROOT = '/api/v1/admin';

function forgejo(overrides = {}) {
  return {
    id: 'forge',
    source: 'forgejo',
    name: 'My Forgejo',
    enabled: true,
    version: 4,
    configuration: { base_url: 'https://git.example' },
    inputs: { users: [{ user: 'alice' }, { user: 'bob' }] },
    managed_by: 'database',
    ...overrides,
  };
}

function input(entry, overrides = {}) {
  return {
    adapter: 'forgejo',
    connection_id: 'forge',
    section: 'users',
    key: entry.user,
    name: entry.user,
    entry,
    ...overrides,
  };
}

const FORGEJO_TYPE = { source: 'forgejo', label: 'Forgejo', enabled: true, fields: ['base_url'] };

async function hub(t, data = {}) {
  const calls = [];
  const events = [];
  const state = {
    definitions: data.definitions ?? { collectors: [], rules: [] },
    integrations: data.integrations ?? { connections: [], collectors: [], demo_mode: false },
    configurations: data.configurations ?? { items: [] },
    named: data.named ?? { types: [FORGEJO_TYPE], connections: [forgejo()] },
  };
  // Copies keep page-realm values comparable with assert.deepEqual.
  const copy = (value) => (value === undefined ? undefined : JSON.parse(JSON.stringify(value)));
  const record = (method, handler) => async (url, body) => {
    calls.push({ method, url, body: copy(body) });
    if (!handler) throw new Error(`Unexpected ${method} ${url}`);
    return handler(url, body);
  };
  const ui = await loadUi('pages/connection-hub.js', {
    html,
    url: 'https://keen.example/admin.html',
    globals: {
      structuredClone,
      confirm: (message) => {
        calls.push({ method: 'CONFIRM', message });
        return data.confirm ?? true;
      },
    },
    setup(w) {
      w.HTMLDialogElement.prototype.showModal = function () {
        this.open = true;
      };
      w.HTMLDialogElement.prototype.close = function () {
        this.open = false;
      };
      w.HTMLElement.prototype.scrollIntoView = () => {};
      for (const name of [
        'keen-collection-create',
        'keen-filter-source',
        'keen-connections-changed',
      ])
        w.addEventListener(name, (event) => events.push({ name, detail: copy(event.detail) }));
    },
    vendor: {
      apiGet: async (url) => {
        calls.push({ method: 'GET', url });
        if (url === ROOT + '/evidence-definitions') return structuredClone(state.definitions);
        if (url === ROOT + '/integrations') return structuredClone(state.integrations);
        if (url === ROOT + '/managed-configurations') return structuredClone(state.configurations);
        if (url === ROOT + '/source-connections') return structuredClone(state.named);
        if (data.get) return data.get(url);
        throw new Error('Unexpected GET ' + url);
      },
      apiPut: record('PUT', data.put),
      apiPost: record('POST', data.post),
      apiDelete: record('DELETE', data.remove),
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

test('API integrations are listed as sources and map their own ingesters', async (t) => {
  const ui = await hub(t, {
    named: { types: [FORGEJO_TYPE], connections: [] },
    integrations: {
      demo_mode: false,
      connections: [{ id: 'api1', name: 'Ticketing API', base_url: 'https://tickets.example' }],
      collectors: [
        { id: 'col1', connection_id: 'api1', name: 'Closed tickets' },
        { id: 'col2', connection_id: 'other', name: 'Another connection' },
      ],
    },
    definitions: {
      collectors: [],
      rules: [{ id: 'r1', when: { source: 'integration:col1' } }],
    },
    get: async (url) => {
      assert.equal(url, ROOT + '/evidence-connections/api%3Aapi1/removal');
      return { blocked: 'Disable the ingesters before deleting this connection' };
    },
  });
  const card = ui.card('Ticketing API');
  assert.equal(
    card.querySelector('p').textContent,
    'API integrations · https://tickets.example · 1 inputs',
  );
  assert.doesNotMatch(card.textContent, /Another connection/);
  // The card offers rule viewing; new rules start from an ingester.
  assert.deepEqual(
    [...card.querySelectorAll('.mt-3 button')].map((b) => b.textContent),
    ['View mapping rules', 'Delete source'],
  );

  await ui.button(card, 'Add mapping rule').onclick();
  assert.deepEqual(ui.events.at(-1), {
    name: 'keen-collection-create',
    detail: {
      source: 'integration:col1',
      connection_id: null,
      input: { id: 'col1', connection_id: 'api1', name: 'Closed tickets' },
    },
  });
  assert.equal(ui.document.getElementById('evidence-mapping-view').hidden, false);

  // Removal first asks the server what it would affect.
  await ui.button(card, 'Delete source').onclick();
  assert.equal(ui.el('status').hidden, false);
  assert.equal(ui.el('status').className, 'alert alert-danger');
  assert.equal(
    ui.el('status').textContent,
    'Disable the ingesters before deleting this connection',
  );
});

test('a webhook source shows its receiving address and is not collected on demand', async (t) => {
  const hook = (id, configuration) => ({
    id,
    source: 'webhooks',
    name: 'Hooks ' + id,
    enabled: true,
    version: 1,
    configuration,
    inputs: {},
    managed_by: 'database',
  });
  const ui = await hub(t, {
    named: {
      types: [{ source: 'webhooks', label: 'Webhooks', enabled: true }],
      connections: [hook('one', { provider: 'github' }), hook('two', {})],
    },
  });

  const one = ui.card('Hooks one');
  assert.match(
    one.textContent,
    /https:\/\/keen\.example\/api\/v1\/webhooks\/connections\/one\/github\/EVENT_TYPE/,
  );
  assert.match(
    ui.card('Hooks two').textContent,
    /\/api\/v1\/webhooks\/connections\/two\/provider\/EVENT_TYPE/,
  );
  const labels = [...one.querySelectorAll('button')].map((b) => b.textContent);
  assert.deepEqual(labels, [
    'Edit source',
    'Add mapping rule',
    'View mapping rules',
    'Delete source',
  ]);
  assert.doesNotMatch(one.textContent, /Add an input/);
});

test('Delete source asks first, deletes the version shown and reloads', async (t) => {
  const declined = await hub(t, { confirm: false });
  await declined.button(declined.card('My Forgejo'), 'Delete source').onclick();
  assert.equal(declined.calls.filter((c) => c.method === 'DELETE').length, 0);
  assert.equal(
    declined.calls.find((c) => c.method === 'CONFIRM').message,
    'Delete My Forgejo? Collected evidence and its connection identity will be retained.',
  );

  const ui = await hub(t, { remove: async () => ({}) });
  const before = ui.loads();
  await ui.button(ui.card('My Forgejo'), 'Delete source').onclick();
  assert.deepEqual(
    ui.calls.filter((c) => c.method === 'DELETE').map((c) => c.url),
    [ROOT + '/source-connections/forge?version=4'],
  );
  assert.equal(ui.loads(), before + 1);
});

test('sources managed in deployment files, and demo sources, cannot be deleted here', async (t) => {
  const files = await hub(t, {
    named: { types: [FORGEJO_TYPE], connections: [forgejo({ managed_by: 'file' })] },
  });
  const card = files.card('My Forgejo');
  assert.equal(files.button(card, 'Delete source').disabled, true);
  // A file-managed source has no editing controls either.
  assert.equal(files.button(card, 'Edit source'), undefined);
  assert.equal(files.button(card, 'Add input'), undefined);

  const demo = await hub(t, {
    integrations: { connections: [], collectors: [], demo_mode: true },
  });
  assert.equal(demo.button(demo.card('My Forgejo'), 'Delete source').disabled, true);
});

test('Collect now queues a collection and reports it', async (t) => {
  const ui = await hub(t, {
    definitions: { collectors: [input({ user: 'alice' })], rules: [] },
    post: async () => ({ queued: true }),
  });
  const card = ui.card('My Forgejo');

  await ui.button(card, 'Collect now').onclick();

  assert.deepEqual(ui.calls.at(-1), {
    method: 'POST',
    url: ROOT + '/source-connections/forge/run',
    body: {},
  });
  assert.equal(ui.el('status').className, 'alert alert-success');
  assert.equal(ui.el('status').textContent, 'Collection queued.');
});

test('removing an input keeps the other inputs and the source settings', async (t) => {
  const ui = await hub(t, {
    definitions: {
      collectors: [input({ user: 'alice' }), input({ user: 'bob' })],
      rules: [],
    },
    put: async (url, body) => body,
  });
  const alice = [...ui.card('My Forgejo').querySelectorAll('.my-2')].find((line) =>
    /alice/.test(line.textContent),
  );
  const before = ui.loads();

  await ui.button(alice, 'Remove input').onclick();

  const put = ui.calls.find((c) => c.method === 'PUT');
  assert.equal(put.url, ROOT + '/source-connections/forge');
  assert.deepEqual(put.body, {
    source: 'forgejo',
    name: 'My Forgejo',
    enabled: true,
    configuration: { base_url: 'https://git.example' },
    inputs: { users: [{ user: 'bob' }] },
    version: 4,
  });
  await settle(() => ui.loads() >= before + 1);
  assert.ok(ui.events.some((e) => e.name === 'keen-connections-changed'));
});

test('declining input removal changes nothing', async (t) => {
  const ui = await hub(t, {
    confirm: false,
    definitions: { collectors: [input({ user: 'alice' })], rules: [] },
  });
  await ui.button(ui.card('My Forgejo'), 'Remove input').onclick();
  assert.equal(ui.calls.filter((c) => c.method === 'PUT').length, 0);
  assert.match(ui.calls.at(-1).message, /Existing evidence and mapping rules are retained/);
});

test('server errors show their detail and leave the action available', async (t) => {
  const ui = await hub(t, {
    remove: async () => {
      throw new Error(JSON.stringify({ detail: 'The source changed; reload and try again' }));
    },
  });
  const remove = ui.button(ui.card('My Forgejo'), 'Delete source');

  await remove.onclick();

  assert.equal(ui.el('status').textContent, 'The source changed; reload and try again');
  assert.equal(ui.el('status').className, 'alert alert-danger');
  assert.equal(remove.disabled, false);
});

test('search narrows the sources and explains an empty list', async (t) => {
  const ui = await hub(t, {
    named: {
      types: [FORGEJO_TYPE, { source: 'loki', label: 'Loki', enabled: true }],
      connections: [
        forgejo(),
        forgejo({ id: 'logs', source: 'loki', name: 'Production logs', inputs: {} }),
      ],
    },
  });
  const titles = () => [...ui.list.querySelectorAll('h3')].map((h) => h.textContent);
  assert.deepEqual(titles(), ['My Forgejo', 'Production logs']);

  // Names and source types both match, regardless of case.
  ui.el('search').value = 'LOKI';
  ui.el('search').dispatchEvent(new ui.window.Event('input'));
  assert.deepEqual(titles(), ['Production logs']);
  ui.el('search').value = 'nothing like this';
  ui.el('search').dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.list.textContent, 'No matching sources.');
});

test('with no sources the list explains how to define one', async (t) => {
  const ui = await loadUi('pages/connection-hub.js', {
    html,
    globals: { structuredClone },
    vendor: {
      apiGet: async (url) => {
        if (url.endsWith('/evidence-definitions')) return { collectors: [], rules: [] };
        if (url.endsWith('/integrations')) return { connections: [], collectors: [] };
        if (url.endsWith('/managed-configurations')) return { items: [] };
        if (url.endsWith('/source-connections')) return { types: [], connections: [] };
        throw new Error(url);
      },
    },
  });
  t.after(ui.close);
  const list = ui.document.getElementById('hub-list');
  await settle(() => list.textContent !== '');
  assert.equal(
    list.textContent,
    'No sources defined. Choose Define a source to enter its address and credentials.',
  );
});

test('editing an environment default input replaces that entry in its configuration', async (t) => {
  const ui = await hub(t, {
    named: { types: [FORGEJO_TYPE], connections: [] },
    configurations: { items: [{ name: 'forgejo' }] },
    definitions: {
      collectors: [input({ user: 'alice', label: 'Alice' }, { connection_id: null })],
      rules: [],
    },
    get: async (url) => {
      assert.equal(url, ROOT + '/managed-configurations/forgejo');
      return {
        version: 7,
        document: { users: [{ user: 'alice', label: 'Alice' }, { user: 'carol' }] },
      };
    },
    put: async (url, body) => body,
  });
  const card = ui.card('Forgejo · Environment default');
  assert.equal(ui.button(card, 'Remove input'), undefined);

  await ui.button(card, 'Edit input').onclick();

  const dialog = ui.el('collection');
  assert.equal(dialog.open, true);
  assert.equal(ui.el('collection-title').textContent, 'Edit Forgejo input · Environment default');
  const type = ui.el('collection-type');
  assert.equal(type.value, 'users');
  assert.equal(type.disabled, true);
  const field = (name) => ui.el('collection-fields').querySelector(`[data-field="${name}"]`);
  assert.equal(field('user').value, 'alice');
  // Clearing a field removes the setting rather than saving an empty value.
  field('label').value = '  ';

  await ui.el('collection-save').onclick();

  const put = ui.calls.find((c) => c.method === 'PUT');
  assert.deepEqual(put, {
    method: 'PUT',
    url: ROOT + '/managed-configurations/forgejo',
    body: { version: 7, document: { users: [{ user: 'alice' }, { user: 'carol' }] } },
  });
  assert.equal(dialog.open, false);
  assert.equal(
    ui.el('status').querySelector('p').textContent,
    'Input saved for Forgejo. Collect evidence now, or add a mapping rule.',
  );
  assert.deepEqual(
    [...ui.el('status').querySelectorAll('button')].map((b) => b.textContent),
    ['Add mapping rule'],
  );
});

test('an input edited elsewhere in the meantime is not overwritten', async (t) => {
  const ui = await hub(t, {
    named: { types: [FORGEJO_TYPE], connections: [] },
    configurations: { items: [{ name: 'forgejo' }] },
    definitions: {
      collectors: [input({ user: 'alice', label: 'Alice' }, { connection_id: null })],
      rules: [],
    },
    get: async () => ({ version: 8, document: { users: [{ user: 'alice', label: 'Renamed' }] } }),
  });
  await ui.button(ui.card('Forgejo · Environment default'), 'Edit input').onclick();

  await ui.el('collection-save').onclick();

  assert.equal(
    ui.el('collection-error').textContent,
    'Collection changed. Close and reopen the editor.',
  );
  assert.equal(ui.el('collection').open, true);
  assert.equal(ui.calls.filter((c) => c.method === 'PUT').length, 0);
  assert.equal(ui.el('collection-save').disabled, false);
});

test('additional input settings must be a JSON object', async (t) => {
  const ui = await hub(t, { put: async (url, body) => body });
  await ui.button(ui.card('My Forgejo'), 'Add input').onclick();
  assert.equal(ui.el('collection-title').textContent, 'Add Forgejo input · My Forgejo');
  const type = ui.el('collection-type');
  assert.deepEqual(
    [...type.options].map((o) => [o.value, o.textContent]),
    [
      ['organizations', 'Organisation'],
      ['users', 'User'],
      ['feeds', 'Feed'],
    ],
  );
  assert.equal(type.disabled, false);
  type.value = 'users';
  type.dispatchEvent(new ui.window.Event('change'));
  ui.el('collection-fields').querySelector('[data-field="user"]').value = 'dave';

  for (const extra of ['[1]', 'null', '"text"']) {
    ui.el('collection-extra').value = extra;
    await ui.el('collection-save').onclick();
    assert.equal(
      ui.el('collection-error').textContent,
      'Additional settings must be a JSON object.',
      extra,
    );
  }
  ui.el('collection-extra').value = '{not json';
  await ui.el('collection-save').onclick();
  assert.notEqual(ui.el('collection-error').textContent, '');
  assert.equal(ui.calls.filter((c) => c.method === 'PUT').length, 0);

  // Extra settings join the fields, and a new entry follows the existing ones.
  ui.el('collection-extra').value = '{"include_private": true}';
  await ui.el('collection-save').onclick();
  assert.deepEqual(ui.calls.find((c) => c.method === 'PUT').body.inputs, {
    users: [{ user: 'alice' }, { user: 'bob' }, { include_private: true, user: 'dave' }],
  });
});

test('numeric identifiers are saved as numbers', async (t) => {
  const taiga = forgejo({
    id: 'tracker',
    source: 'taiga',
    name: 'Tracker',
    inputs: {},
  });
  const ui = await hub(t, {
    named: { types: [{ source: 'taiga', label: 'Taiga', enabled: true }], connections: [taiga] },
    put: async (url, body) => body,
  });
  await ui.button(ui.card('Tracker'), 'Add input').onclick();
  const field = (name) => ui.el('collection-fields').querySelector(`[data-field="${name}"]`);
  assert.equal(field('id').closest('label').textContent, 'Id');
  field('id').value = '42';
  field('label').value = 'Operations';

  await ui.el('collection-save').onclick();

  assert.deepEqual(ui.calls.find((c) => c.method === 'PUT').body.inputs, {
    projects: [{ id: 42, label: 'Operations' }],
  });
});

test('specialised collection types are edited in the evidence editor', async (t) => {
  const ui = await hub(t, {
    named: {
      types: [{ source: 'imap_custom', label: 'Custom', enabled: true }],
      connections: [forgejo({ id: 'custom', source: 'imap_custom', name: 'Custom', inputs: {} })],
    },
  });
  await ui.button(ui.card('Custom'), 'Add input').onclick();
  assert.equal(
    ui.el('status').textContent,
    'Use the evidence editor for this specialised collection type.',
  );
  assert.equal(ui.el('collection').open, false);
});

test('arrow, Home and End keys move between the source and mapping tabs', async (t) => {
  const ui = await hub(t);
  const press = (id, key) => {
    const event = new ui.window.KeyboardEvent('keydown', { key, cancelable: true });
    ui.el(id).dispatchEvent(event);
    return event;
  };
  const selected = () =>
    ['source', 'mapping'].filter((name) => ui.el(name).getAttribute('aria-selected') === 'true');

  assert.equal(press('source', 'ArrowRight').defaultPrevented, true);
  assert.deepEqual(selected(), ['mapping']);
  assert.equal(ui.document.activeElement, ui.el('mapping'));
  assert.equal(ui.document.getElementById('evidence-mapping-view').hidden, false);
  press('mapping', 'ArrowLeft');
  assert.deepEqual(selected(), ['source']);
  press('source', 'End');
  assert.deepEqual(selected(), ['mapping']);
  press('mapping', 'Home');
  assert.deepEqual(selected(), ['source']);
  assert.equal(ui.el('source').tabIndex, 0);
  assert.equal(ui.el('mapping').tabIndex, -1);
  // Other keys keep their usual behaviour.
  assert.equal(press('source', 'a').defaultPrevented, false);
  assert.deepEqual(selected(), ['source']);
});

test('opening a source from another view lists just that source', async (t) => {
  const ui = await hub(t, {
    named: {
      types: [FORGEJO_TYPE, { source: 'loki', label: 'Loki', enabled: true }],
      connections: [forgejo(), forgejo({ id: 'logs', source: 'loki', name: 'Logs', inputs: {} })],
    },
  });
  ui.el('mapping').click();

  ui.window.dispatchEvent(
    new ui.window.CustomEvent('keen-manage-source', { detail: { source: 'loki' } }),
  );

  assert.equal(ui.el('search').value, 'loki');
  assert.deepEqual(
    [...ui.list.querySelectorAll('h3')].map((h) => h.textContent),
    ['Logs'],
  );
  assert.equal(ui.document.getElementById('evidence-management-view').hidden, false);
  assert.equal(ui.document.activeElement, ui.el('search'));
});

test('a newly defined source opens its first input, and a feed source offers collection', async (t) => {
  const ui = await hub(t, {
    named: {
      types: [
        FORGEJO_TYPE,
        { source: 'rss', label: 'RSS', enabled: true, fields: [], credential_fields: [] },
      ],
      connections: [],
    },
    post: async (url, body) => {
      assert.equal(url, ROOT + '/source-connections');
      return { ...body, id: body.source + '-new', version: 1, managed_by: 'database' };
    },
  });
  const define = async (source, fill) => {
    ui.el('start-setup').click();
    const dialog = [...ui.document.querySelectorAll('body > dialog')].at(-1);
    dialog.querySelector('select').value = source;
    dialog.querySelector('select').dispatchEvent(new ui.window.Event('change'));
    dialog.querySelector('input[required]').value = 'New ' + source;
    fill(dialog);
    await dialog.querySelector('form').onsubmit({ preventDefault() {} });
  };

  await define('forgejo', (dialog) => {
    dialog.querySelector('[data-key="base_url"]').value = 'https://new.example';
  });
  await settle(() => ui.el('collection').open);
  assert.equal(ui.el('collection-title').textContent, 'Add Forgejo input · New forgejo');
  ui.el('collection-cancel').click();
  assert.equal(ui.el('collection').open, false);

  await define('rss', (dialog) => {
    dialog.querySelector('[data-feed-url]').value = 'https://news.example/feed.xml';
  });
  await settle(() => /New rss/.test(ui.el('status').textContent));
  assert.equal(
    ui.el('status').querySelector('p').textContent,
    'Input saved for New rss. Collect evidence now, or add a mapping rule.',
  );
  assert.deepEqual(
    [...ui.el('status').querySelectorAll('button')].map((b) => b.textContent),
    ['Collect now', 'Add mapping rule'],
  );
  assert.equal(ui.el('collection').open, false);
  assert.ok(ui.events.some((e) => e.name === 'keen-connections-changed'));
});
