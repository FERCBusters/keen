import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
const html = await readFile(new URL('../public/admin.html', import.meta.url), 'utf8');
async function fixture(t, inputs = []) {
  const ui = await loadUi('pages/connection-hub.js', {
    html,
    globals: { structuredClone },
    vendor: {
      apiGet: async (url) => {
        if (url.endsWith('/evidence-definitions')) return { collectors: inputs, rules: [] };
        if (url.endsWith('/integrations')) return { connections: [], collectors: [] };
        if (url.endsWith('/managed-configurations'))
          return { items: [{ name: 'forgejo' }, { name: 'loki' }] };
        if (url.endsWith('/source-connections'))
          return {
            types: [
              { source: 'forgejo', enabled: true },
              { source: 'loki', enabled: false },
            ],
            connections: [
              {
                id: 'one',
                source: 'forgejo',
                name: 'My Forgejo',
                enabled: true,
                configuration: { base_url: 'https://git.example' },
                inputs: {},
                managed_by: 'database',
              },
              {
                id: 'disabled',
                source: 'loki',
                name: 'Hidden logs',
                configuration: {},
                inputs: {},
                managed_by: 'database',
              },
            ],
          };
        throw Error(url);
      },
    },
  });
  t.after(ui.close);
  for (let i = 0; i < 50 && !ui.document.getElementById('hub-list').textContent; i++)
    await new Promise((r) => setTimeout(r, 5));
  return ui;
}
test('sources start with named enabled configurations and require inputs before mapping', async (t) => {
  const { document } = await fixture(t);
  assert.equal(document.getElementById('hub-connection'), null);
  assert.equal(document.getElementById('evidence-management-view').hidden, false);
  const health = document.getElementById('evidence-source-health');
  assert.equal(health.closest('section').id, 'evidence-management-view');
  assert.equal(health.open, false);
  const list = document.getElementById('hub-list');
  assert.match(list.textContent, /My Forgejo/);
  assert.doesNotMatch(list.textContent, /Environment default|Hidden logs|Add mapping rule/);
  assert.ok([...list.querySelectorAll('button')].some((b) => b.textContent === 'Add input'));
});
test('mapping from an input carries its source identity and input', async (t) => {
  const input = {
    adapter: 'forgejo',
    connection_id: 'one',
    section: 'users',
    key: 'alice',
    collector: 'collector-alice',
    entry: { user: 'alice' },
  };
  const { document, window } = await fixture(t, [input]);
  let detail;
  window.addEventListener('keen-collection-create', (event) => (detail = event.detail));
  [...document.querySelectorAll('#hub-list button')]
    .find((b) => b.textContent === 'Add mapping rule')
    .click();
  assert.equal(detail.source, 'forgejo');
  assert.equal(detail.connection_id, 'one');
  assert.equal(detail.input.collector, 'collector-alice');
  assert.equal(document.getElementById('evidence-mapping-view').hidden, false);
});

test('Elasticsearch input editor saves its pattern and field mappings on the named connection', async (t) => {
  let saved;
  const connection = {
    id: 'elastic-one',
    source: 'elasticsearch',
    name: 'Production logs',
    enabled: true,
    configuration: { base_url: 'https://elastic.example' },
    inputs: {},
    version: 1,
    managed_by: 'database',
  };
  const ui = await loadUi('pages/connection-hub.js', {
    html,
    globals: { structuredClone },
    setup(w) {
      w.HTMLDialogElement.prototype.showModal = function () {
        this.open = true;
      };
      w.HTMLDialogElement.prototype.close = function () {
        this.open = false;
      };
    },
    vendor: {
      apiGet: async (url) => {
        if (url.endsWith('/evidence-definitions')) return { collectors: [], rules: [] };
        if (url.endsWith('/integrations')) return { connections: [], collectors: [] };
        if (url.endsWith('/managed-configurations')) return { items: [{ name: 'elasticsearch' }] };
        if (url.endsWith('/source-connections'))
          return {
            types: [{ source: 'elasticsearch', label: 'Elasticsearch', enabled: true }],
            connections: [connection],
          };
        throw new Error(url);
      },
      apiPut: async (url, payload) => {
        saved = { url, payload };
        return payload;
      },
    },
  });
  t.after(ui.close);
  for (let i = 0; i < 50 && !ui.document.getElementById('hub-list').textContent; i++)
    await new Promise((resolve) => setTimeout(resolve, 5));
  const add = [...ui.document.querySelectorAll('#hub-list button')].find(
    (b) => b.textContent === 'Add input',
  );
  await add.onclick();
  const fields = ui.document.getElementById('hub-collection-fields');
  fields.querySelector('[data-field="name"]').value = 'Authentication';
  fields.querySelector('[data-field="index_pattern"]').value = 'logs-*';
  fields.querySelector('[data-field="query"]').value = '"login failed"';
  assert.equal(fields.querySelector('[data-field="timestamp_field"]').value, '@timestamp');
  await ui.document.getElementById('hub-collection-save').onclick();
  assert.equal(saved.url, '/api/v1/admin/source-connections/elastic-one');
  assert.equal(saved.payload.inputs.queries[0].index_pattern, 'logs-*');
  assert.equal(saved.payload.inputs.queries[0].message_field, 'message');
  assert.equal(saved.payload.inputs.queries[0].query, '"login failed"');
  assert.equal(ui.document.getElementById('evidence-management-view').hidden, false);
});

test('IMAP folder connection offers collection and mapping without an extra input', async (t) => {
  const ui = await loadUi('pages/connection-hub.js', {
    html,
    globals: { structuredClone },
    vendor: {
      apiGet: async (url) => {
        if (url.endsWith('/evidence-definitions')) return { collectors: [], rules: [] };
        if (url.endsWith('/integrations')) return { connections: [], collectors: [] };
        if (url.endsWith('/managed-configurations')) return { items: [] };
        if (url.endsWith('/source-connections'))
          return {
            types: [{ source: 'imap', enabled: true }],
            connections: [
              {
                id: 'mail',
                source: 'imap',
                name: 'Mail evidence',
                enabled: true,
                configuration: { folder: '<img src=x>' },
                inputs: {},
                managed_by: 'database',
              },
            ],
          };
        throw Error(url);
      },
    },
  });
  t.after(ui.close);
  const list = ui.document.getElementById('hub-list');
  for (let i = 0; i < 50 && !list.textContent; i++) await new Promise((r) => setTimeout(r, 5));
  const buttons = [...list.querySelectorAll('button')].map((b) => b.textContent);
  assert.ok(buttons.includes('Collect now'));
  assert.ok(buttons.includes('Add mapping rule'));
  assert.ok(!buttons.includes('Add input'));
  assert.match(list.textContent, /Folder: <img src=x>/);
  assert.equal(list.querySelector('img'), null);
});
