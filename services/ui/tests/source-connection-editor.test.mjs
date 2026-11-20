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
  // The API accepts a source name of up to 128 characters.
  const name = [...dialog.querySelectorAll('label')]
    .find((l) => l.textContent.startsWith('Source name'))
    .querySelector('input');
  assert.deepEqual([name.value, name.required, name.maxLength], ['Production', true, 128]);
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
    [['base_url', 'identity_url', 'client_id'], ['client_secret'], ['client_id', 'client_secret']],
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
  assert.equal(sourceFieldLabel('identity_url'), 'Identity URL');
});

test("a new password manager source starts from the vendor's own endpoints", async (t) => {
  const ui = await loadUi('pages/source-connection-editor.js', {
    setup(w) {
      w.HTMLDialogElement.prototype.showModal = function () {};
    },
  });
  t.after(ui.close);
  const types = [
    {
      source: 'bitwarden',
      label: 'Bitwarden',
      enabled: true,
      fields: ['base_url', 'identity_url', 'client_id'],
      credential_fields: ['client_secret'],
    },
    {
      source: 'op_events',
      label: '1Password',
      enabled: true,
      fields: ['base_url'],
      credential_fields: ['token'],
    },
  ];
  const value = (dialog, key) => dialog.querySelector(`[data-key="${key}"]`).value;
  const created = ui.api.editSourceConnection({ types, onSaved: async () => {} });
  assert.equal(value(created, 'base_url'), 'https://api.bitwarden.com');
  assert.equal(value(created, 'identity_url'), 'https://identity.bitwarden.com');
  // Neither the client ID nor a secret is ever filled in.
  assert.equal(value(created, 'client_id'), '');
  assert.equal(value(created, 'client_secret'), '');
  assert.equal(
    created.querySelector('[data-key="client_secret"]').closest('fieldset').querySelector('legend')
      .textContent,
    'Organisation API key',
  );
  const source = created.querySelector('select');
  source.value = 'op_events';
  source.dispatchEvent(new ui.window.Event('change'));
  assert.equal(value(created, 'base_url'), 'https://events.1password.com');
  assert.equal(value(created, 'token'), '');
  // A saved source shows the endpoints it was saved with.
  const saved = ui.api.editSourceConnection({
    types,
    connection: {
      id: 'bw',
      source: 'bitwarden',
      name: 'Self-hosted',
      configuration: {
        base_url: 'https://vault.example/api',
        identity_url: 'https://vault.example/identity',
        client_id: 'organization.1',
      },
      inputs: {},
      version: 1,
    },
    onSaved: async () => {},
  });
  assert.equal(value(saved, 'base_url'), 'https://vault.example/api');
  assert.equal(value(saved, 'identity_url'), 'https://vault.example/identity');
});

test('a Mosyle source groups its sign-in beside its access token, and fills none in', async (t) => {
  const ui = await loadUi('pages/source-connection-editor.js', {
    setup(w) {
      w.HTMLDialogElement.prototype.showModal = function () {};
    },
  });
  t.after(ui.close);
  const types = [
    {
      source: 'mosyle',
      label: 'Mosyle',
      enabled: true,
      fields: ['email'],
      credential_fields: ['access_token', 'password'],
    },
  ];
  const created = ui.api.editSourceConnection({ types, onSaved: async () => {} });
  const field = (key) => created.querySelector(`[data-key="${key}"]`);
  const legend = (key) => field(key).closest('fieldset').querySelector('legend').textContent;
  const label = (key) => field(key).closest('label').textContent;
  for (const key of ['email', 'password', 'access_token']) assert.equal(field(key).value, '', key);
  assert.equal(legend('email'), 'Administrator sign-in');
  assert.equal(legend('password'), 'Administrator sign-in');
  assert.equal(field('access_token').closest('fieldset'), null);
  assert.equal(label('email'), 'Email address');
  assert.equal(label('access_token'), 'API access token');
  // The servers are Mosyle's own: the editor offers no endpoint to change.
  assert.equal(field('base_url'), null);
  // The password and tokens are entered as secrets; the address is not.
  assert.equal(field('password').type, 'password');
  assert.equal(field('access_token').type, 'password');
  assert.notEqual(field('email').type, 'password');
});

test('a Cloudflare source asks for its account ID and API token, and fills neither in', async (t) => {
  const ui = await loadUi('pages/source-connection-editor.js', {
    setup(w) {
      w.HTMLDialogElement.prototype.showModal = function () {};
    },
  });
  t.after(ui.close);
  const types = [
    {
      source: 'cloudflare',
      label: 'Cloudflare',
      enabled: true,
      fields: ['account_id'],
      credential_fields: ['api_token'],
    },
  ];
  const created = ui.api.editSourceConnection({ types, onSaved: async () => {} });
  const field = (key) => created.querySelector(`[data-key="${key}"]`);
  const label = (key) => field(key).closest('label').textContent;
  for (const key of ['account_id', 'api_token']) assert.equal(field(key).value, '', key);
  assert.equal(label('account_id'), 'Account ID');
  assert.equal(label('api_token'), 'API token');
  // The server is Cloudflare's own: the editor offers no endpoint to change.
  assert.equal(field('base_url'), null);
  // The token is entered as a secret; the account ID is not.
  assert.equal(field('api_token').type, 'password');
  assert.notEqual(field('account_id').type, 'password');
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
        fields: [
          'host',
          'port',
          'username',
          'folder',
          'fetch_body',
          'body_max_chars',
          'poll_interval_minutes',
        ],
        credential_fields: ['password'],
      },
    ],
    onSaved: async () => {},
  });
  const choice = dialog.querySelector('[data-key="fetch_body"]');
  const limit = dialog.querySelector('[data-key="body_max_chars"]');
  const interval = dialog.querySelector('[data-key="poll_interval_minutes"]');
  assert.equal(interval.min, '5');
  assert.equal(interval.max, '1440');
  assert.equal(interval.step, '5');
  assert.equal(interval.value, '5');
  interval.value = '30';
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
  assert.equal(saved.configuration.poll_interval_minutes, '30');
  assert.equal(saved.configuration.folder, 'INBOX');
  assert.equal(saved.configuration.port, '993');
  assert.deepEqual(JSON.parse(JSON.stringify(saved.inputs)), {});
});
