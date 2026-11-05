// The source connection editor: saved settings shown back without their
// credentials, removing a saved credential, refused saves, and deployments
// without an enabled source type.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadUi } from './support/load-ui.mjs';

// The design system's errorText reads the public detail of a JSON error body.
function errorText(error) {
  const raw = String(error?.message ?? error);
  try {
    return JSON.parse(raw).detail || raw;
  } catch {
    return raw;
  }
}

async function editor(t, { put, post } = {}) {
  const saved = [];
  const ui = await loadUi('pages/source-connection-editor.js', {
    setup(window) {
      window.HTMLDialogElement.prototype.showModal = function () {
        this.open = true;
      };
      window.HTMLDialogElement.prototype.close = function () {
        this.open = false;
        this.dispatchEvent(new window.Event('close'));
      };
    },
    vendor: {
      errorText,
      apiPut: async (url, body) => {
        saved.push({ url, body: JSON.parse(JSON.stringify(body)) });
        return put ? put(body) : { ...body, id: 'one' };
      },
      apiPost: async (url, body) => {
        saved.push({ url, body: JSON.parse(JSON.stringify(body)) });
        return post ? post(body) : { ...body, id: 'new' };
      },
    },
  });
  t.after(ui.close);
  return { ui, saved };
}

const imapType = {
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
};
const imapConnection = {
  id: 'one',
  source: 'imap',
  name: 'Security mailbox',
  enabled: false,
  configuration: {
    host: 'mail.example',
    port: '1993',
    username: 'reader',
    folder: 'Alerts',
    fetch_body: 'true',
    body_max_chars: '8000',
    poll_interval_minutes: '30',
  },
  inputs: {},
  version: 4,
};

test('editing a mailbox shows its saved settings and never its password', async (t) => {
  const { ui, saved } = await editor(t);
  const dialog = ui.api.editSourceConnection({
    types: [imapType],
    connection: imapConnection,
    onSaved: async () => {},
  });
  const value = (key) => dialog.querySelector(`[data-key="${key}"]`).value;
  assert.equal(value('fetch_body'), 'true');
  assert.equal(value('poll_interval_minutes'), '30');
  assert.equal(value('port'), '1993');
  assert.equal(value('body_max_chars'), '8000');
  assert.equal(value('folder'), 'Alerts');
  // The body limit applies because bodies are fetched.
  assert.equal(dialog.querySelector('[data-key="body_max_chars"]').closest('label').hidden, false);
  assert.equal(dialog.querySelector('[data-key="password"]').value, '');
  assert.equal(
    dialog.querySelector('[data-key="password"]').closest('label').firstChild.textContent,
    'Password (blank keeps saved value)',
  );
  assert.equal(dialog.querySelector('select').disabled, true);
  assert.equal(dialog.querySelector('input[type="checkbox"]').checked, false);
  await dialog.querySelector('form').onsubmit({ preventDefault() {} });
  assert.equal(saved[0].url, '/api/v1/admin/source-connections/one');
  assert.equal(saved[0].body.enabled, false);
  assert.equal(saved[0].body.version, 4);
  assert.deepEqual(saved[0].body.clear_credentials, []);
  assert.deepEqual(saved[0].body.credentials, { password: '' });
});

test('ticking Remove saved password asks the server to delete that credential', async (t) => {
  const { ui, saved } = await editor(t);
  const dialog = ui.api.editSourceConnection({
    types: [imapType],
    connection: imapConnection,
    onSaved: async () => {},
  });
  const remove = dialog.querySelector('[data-clear="password"]');
  assert.equal(remove.closest('label').textContent, 'Remove saved Password');
  remove.checked = true;
  await dialog.querySelector('form').onsubmit({ preventDefault() {} });
  assert.deepEqual(saved[0].body.clear_credentials, ['password']);
  assert.equal(saved[0].body.credentials.password, '');
});

test('a connection keeps its saved authentication method and type after the type is disabled', async (t) => {
  const { ui } = await editor(t);
  const types = [
    {
      source: 'jenkins',
      label: 'Jenkins',
      enabled: false,
      fields: ['base_url', 'auth_mode'],
      credential_fields: ['token'],
    },
    { source: 'loki', label: 'Loki', enabled: true, fields: ['base_url'], credential_fields: [] },
  ];
  const dialog = ui.api.editSourceConnection({
    types,
    connection: {
      id: 'ci',
      source: 'jenkins',
      name: 'Build server',
      enabled: true,
      configuration: { base_url: 'https://ci.example', auth_mode: 'basic' },
      inputs: {},
      version: 1,
    },
    onSaved: async () => {},
  });
  const type = dialog.querySelector('select');
  assert.deepEqual(
    [...type.options].map((o) => o.value),
    ['jenkins', 'loki'],
  );
  assert.equal(type.value, 'jenkins');
  assert.equal(dialog.querySelector('[data-key="auth_mode"]').value, 'basic');
  // A new source offers enabled types only, starting from token authentication.
  const fresh = ui.api.editSourceConnection({ types, onSaved: async () => {} });
  assert.deepEqual(
    [...fresh.querySelector('select').options].map((o) => o.value),
    ['loki'],
  );
  const created = ui.api.editSourceConnection({
    types: [{ ...types[0], enabled: true }],
    onSaved: async () => {},
  });
  assert.equal(created.querySelector('[data-key="auth_mode"]').value, 'token');
});

test('without an enabled source type the editor explains why it cannot create one', async (t) => {
  const { ui, saved } = await editor(t);
  const dialog = ui.api.editSourceConnection({
    types: [{ source: 'loki', label: 'Loki', enabled: false, fields: ['base_url'] }],
    onSaved: async () => {},
  });
  assert.equal(dialog.querySelector('select').options.length, 0);
  assert.equal(dialog.querySelectorAll('[data-key]').length, 0);
  assert.equal(
    dialog.querySelector('[role="alert"]').textContent,
    'Enable a source type in the deployment configuration to set up a connection.',
  );
  assert.equal(dialog.querySelector('button[type="submit"]').disabled, true);
  assert.deepEqual(saved, []);
});

test('Cancel closes the editor without saving', async (t) => {
  const { ui, saved } = await editor(t);
  const dialog = ui.api.editSourceConnection({ types: [imapType], onSaved: async () => {} });
  assert.equal(dialog.isConnected, true);
  [...dialog.querySelectorAll('button')].find((b) => b.textContent === 'Cancel').click();
  assert.equal(dialog.open, false);
  assert.equal(dialog.isConnected, false);
  assert.deepEqual(saved, []);
});

test("a refused save shows the server's reason and keeps the typed password out of view", async (t) => {
  let saves = 0;
  const { ui, saved } = await editor(t, {
    post: () => {
      saves++;
      throw new Error(JSON.stringify({ detail: 'A source named Production already exists' }));
    },
  });
  const onSaved = [];
  const dialog = ui.api.editSourceConnection({
    types: [imapType],
    onSaved: async (result) => onSaved.push(result),
  });
  dialog.querySelector('input[type="text"]').value = 'Production';
  dialog.querySelector('[data-key="password"]').value = 'synthetic-mail-password';
  await dialog.querySelector('form').onsubmit({ preventDefault() {} });
  assert.equal(saves, 1);
  assert.equal(saved[0].body.credentials.password, 'synthetic-mail-password');
  assert.equal(
    dialog.querySelector('[role="alert"]').textContent,
    'A source named Production already exists',
  );
  assert.doesNotMatch(dialog.textContent, /synthetic-mail-password/);
  assert.equal(dialog.open, true);
  assert.equal(dialog.querySelector('button[type="submit"]').disabled, false);
  assert.deepEqual(onSaved, []);
});
