import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

test('connection editor separates endpoint and secrets and preserves inputs on edit', async (t) => {
  let saved;
  const ui = await loadUi('pages/source-connection-editor.js', {
    setup(window) {
      window.HTMLDialogElement.prototype.showModal = function () {
        this.open = true;
      };
      window.HTMLDialogElement.prototype.close = function () {
        this.open = false;
      };
    },
    vendor: {
      apiPut: async (url, body) => {
        saved = { url, body };
        return { ...body, id: 'one' };
      },
    },
  });
  t.after(ui.close);
  const connection = {
    id: 'one',
    source: 'loki',
    name: 'Production',
    enabled: true,
    configuration: { base_url: 'https://logs.example' },
    inputs: { queries: [{ name: 'auth', logql: '{app="auth"}' }] },
    version: 3,
  };
  const dialog = ui.api.editSourceConnection({
    types: [
      {
        source: 'loki',
        enabled: true,
        fields: ['base_url', 'username'],
        credential_fields: ['password'],
      },
    ],
    connection,
    onSaved: async () => {},
  });
  assert.equal(dialog.querySelector('[data-key="password"]').value, '');
  dialog.querySelector('[data-key="base_url"]').value = 'https://other.example';
  dialog.querySelector('[data-key="password"]').value = 'new-secret';
  await dialog.querySelector('form').onsubmit({ preventDefault() {} });
  assert.equal(saved.url, '/api/v1/admin/source-connections/one');
  assert.equal(saved.body.configuration.base_url, 'https://other.example');
  assert.equal(saved.body.credentials.password, 'new-secret');
  assert.equal(saved.body.inputs.queries[0].name, 'auth');
  assert.equal(saved.body.version, 3);
});

const rssType = {
  source: 'rss',
  label: 'RSS',
  enabled: true,
  fields: ['base_url', 'username', 'header_name'],
  credential_fields: ['password', 'header_value'],
};
test('RSS source saves one feed and credentials together with an origin restriction', async (t) => {
  let saved;
  const ui = await loadUi('pages/source-connection-editor.js', {
    setup(w) {
      w.HTMLDialogElement.prototype.showModal = function () {
        this.open = true;
      };
      w.HTMLDialogElement.prototype.close = function () {
        this.open = false;
      };
    },
    vendor: {
      apiPost: async (url, body) => {
        saved = body;
        return { ...body, id: 'rss-one' };
      },
    },
  });
  t.after(ui.close);
  const dialog = ui.api.editSourceConnection({ types: [rssType], onSaved: async () => {} });
  assert.equal(dialog.querySelector('select').selectedOptions[0].textContent, 'RSS');
  assert.equal(dialog.querySelector('select').value, 'rss');
  assert.equal(dialog.querySelector('[data-key="base_url"]'), null);
  dialog.querySelector('[data-feed-url]').value =
    'https://news.example:8443/private/feed.xml?format=rss';
  dialog.querySelector('[data-key="password"]').value = 'secret';
  await dialog.querySelector('form').onsubmit({ preventDefault() {} });
  assert.equal(saved.configuration.base_url, 'https://news.example:8443');
  assert.equal(saved.inputs.feeds.length, 1);
  assert.equal(saved.inputs.feeds[0].url, 'https://news.example:8443/private/feed.xml?format=rss');
  assert.equal(saved.credentials.password, 'secret');
});

test('RSS edit preserves feed options and rejects credentials embedded in URL', async (t) => {
  let saved;
  const ui = await loadUi('pages/source-connection-editor.js', {
    setup(w) {
      w.HTMLDialogElement.prototype.showModal = function () {};
      w.HTMLDialogElement.prototype.close = function () {};
    },
    vendor: {
      apiPut: async (url, body) => {
        saved = body;
        return body;
      },
    },
  });
  t.after(ui.close);
  const connection = {
    id: 'rss',
    source: 'rss',
    name: 'News',
    configuration: { base_url: 'https://news.example' },
    inputs: { feeds: [{ url: 'https://news.example/rss', label: 'Security', max_items: 12 }] },
    version: 2,
  };
  const dialog = ui.api.editSourceConnection({
    types: [rssType],
    connection,
    onSaved: async () => {},
  });
  dialog.querySelector('[data-feed-url]').value = 'https://user:secret@news.example/rss';
  await dialog.querySelector('form').onsubmit({ preventDefault() {} });
  assert.equal(saved, undefined);
  dialog.querySelector('[data-feed-url]').value = 'https://other.example/rss';
  await dialog.querySelector('form').onsubmit({ preventDefault() {} });
  assert.equal(saved.inputs.feeds[0].max_items, 12);
  assert.equal(saved.inputs.feeds[0].label, 'Security');
  assert.equal(saved.configuration.base_url, 'https://other.example');
  assert.equal(saved.credentials.password, '');
});

test('existing multi-feed source retains all inputs', async (t) => {
  let saved;
  const ui = await loadUi('pages/source-connection-editor.js', {
    setup(w) {
      w.HTMLDialogElement.prototype.showModal = function () {};
      w.HTMLDialogElement.prototype.close = function () {};
    },
    vendor: {
      apiPut: async (url, body) => {
        saved = body;
        return body;
      },
    },
  });
  t.after(ui.close);
  const connection = {
    id: 'rss',
    source: 'rss',
    name: 'News',
    configuration: { base_url: 'https://news.example' },
    inputs: { feeds: [{ url: 'https://news.example/a' }, { url: 'https://news.example/b' }] },
    version: 2,
  };
  const dialog = ui.api.editSourceConnection({
    types: [rssType],
    connection,
    onSaved: async () => {},
  });
  assert.equal(dialog.querySelector('[data-feed-url]'), null);
  await dialog.querySelector('form').onsubmit({ preventDefault() {} });
  assert.equal(saved.inputs.feeds.length, 2);
});

test('RSS credentials and header fields have readable labels and adjacent groups', async (t) => {
  const ui = await loadUi('pages/source-connection-editor.js', {
    setup(w) {
      w.HTMLDialogElement.prototype.showModal = function () {};
    },
  });
  t.after(ui.close);
  const dialog = ui.api.editSourceConnection({ types: [rssType], onSaved: async () => {} });
  assert.deepEqual(
    Array.from(dialog.querySelectorAll('[data-key]'), (input) => input.dataset.key),
    ['username', 'password', 'header_name', 'header_value'],
  );
  const password = dialog.querySelector('[data-key="password"]');
  assert.equal(password.type, 'password');
  assert.equal(password.dataset.secret, 'true');
  assert.equal(
    password.closest('fieldset').querySelector('legend').textContent,
    'Account credentials',
  );
  assert.equal(
    dialog.querySelector('[data-key="header_name"]').closest('fieldset'),
    dialog.querySelector('[data-key="header_value"]').closest('fieldset'),
  );
  assert.equal(
    dialog.querySelector('[data-key="header_value"]').parentElement.firstChild.textContent,
    'Header value',
  );
});

test('related credentials stay together across source types without changing secret classification', async () => {
  const { sourceFieldGroups, sourceFieldLabel } =
    await import('../public/pages/source-connection-fields.js');
  for (const [ordinary, secrets, expected] of [
    [
      ['base_url', 'username'],
      ['token', 'password'],
      ['username', 'password'],
    ],
    [['base_url', 'username'], ['api_token'], ['username', 'api_token']],
    [['provider', 'secret_header'], ['secret'], ['secret_header', 'secret']],
    [['base_url'], ['token_id', 'token_secret'], ['token_id', 'token_secret']],
    [
      ['region'],
      ['aws_access_key_id', 'aws_secret_access_key', 'aws_session_token'],
      ['aws_access_key_id', 'aws_secret_access_key', 'aws_session_token'],
    ],
  ]) {
    const groups = sourceFieldGroups({ fields: ordinary, credential_fields: secrets });
    assert.ok(
      groups.some(
        (group) =>
          JSON.stringify(group.fields.map((field) => field.key)) === JSON.stringify(expected),
      ),
    );
    for (const field of groups.flatMap((group) => group.fields))
      assert.equal(field.secret, secrets.includes(field.key));
  }
  assert.equal(sourceFieldLabel('api_token'), 'API token');
  assert.equal(sourceFieldLabel('sa_json'), 'Service account JSON');
  assert.equal(sourceFieldLabel('future_field'), 'Future field');
});

test('IMAP defaults to headers only and reveals a 4000 character body limit on request', async (t) => {
  let saved;
  const ui = await loadUi('pages/source-connection-editor.js', {
    setup(w) {
      w.HTMLDialogElement.prototype.showModal = function () {};
      w.HTMLDialogElement.prototype.close = function () {};
    },
    vendor: {
      apiPost: async (url, body) => {
        saved = body;
        return { ...body, id: 'mail' };
      },
    },
  });
  t.after(ui.close);
  const dialog = ui.api.editSourceConnection({
    types: [
      {
        source: 'imap',
        label: 'IMAP mail',
        enabled: true,
        fields: ['host', 'port', 'username', 'folder', 'fetch_body', 'body_max_chars'],
        credential_fields: ['password'],
      },
    ],
    onSaved: async () => {},
  });
  const choice = dialog.querySelector('[data-key="fetch_body"]');
  const limit = dialog.querySelector('[data-key="body_max_chars"]');
  assert.equal(choice.value, 'false');
  assert.equal(limit.value, '4000');
  assert.equal(limit.closest('label').hidden, true);
  choice.value = 'true';
  choice.dispatchEvent(new ui.window.Event('change', { bubbles: true }));
  assert.equal(limit.closest('label').hidden, false);
  dialog.querySelector('[data-key="host"]').value = 'mail.example';
  dialog.querySelector('[data-key="username"]').value = 'reader';
  await dialog.querySelector('form').onsubmit({ preventDefault() {} });
  assert.equal(saved.configuration.fetch_body, 'true');
  assert.equal(saved.configuration.body_max_chars, '4000');
  assert.equal(saved.configuration.folder, 'INBOX');
  assert.equal(saved.configuration.port, '993');
  assert.deepEqual(JSON.parse(JSON.stringify(saved.inputs)), {});
});
