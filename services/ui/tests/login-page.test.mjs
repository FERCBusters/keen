import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const html = await readFile(new URL('../public/login.html', import.meta.url), 'utf8');

test('login cannot submit credentials natively when JavaScript fails', () => {
  const dom = new JSDOM(html, { url: 'https://keen.example/' });
  const doc = dom.window.document;
  doc.getElementById('username').value = 'synthetic-user';
  doc.getElementById('password').value = 'synthetic-test-password';
  const form = doc.getElementById('loginForm');
  assert.equal(form.method, 'post');
  assert.equal(new URL(form.action).pathname, '/api/v1/auth/login');
  assert.equal(doc.getElementById('loginFields').disabled, true);
  assert.equal(doc.getElementById('username').name, '');
  assert.equal(doc.getElementById('password').name, '');
  assert.equal(new dom.window.FormData(form).has('password'), false);
  // Even a forced native submission after the controls are enabled has no credentials.
  doc.getElementById('loginFields').disabled = false;
  const values = new dom.window.FormData(form);
  assert.equal(values.has('username'), false);
  assert.equal(values.has('password'), false);
  dom.window.close();
});

test('login handler sends credentials only in a JSON POST body', async (t) => {
  const calls = [];
  const ui = await loadUi('pages/login.js', {
    html,
    url: 'https://keen.example/',
    globals: {
      fetch: async (url, options = {}) => {
        calls.push({ url, options });
        if (url === '/api/v1/auth/methods')
          return { ok: true, json: async () => ({ local_enabled: true }) };
        return {
          ok: false,
          status: 401,
          json: async () => ({ detail: 'Invalid username or password' }),
        };
      },
    },
    vendor: {
      qs: () => null,
      toast: (el, message) => {
        el.textContent = message;
      },
    },
  });
  t.after(() => ui.close());
  assert.equal(ui.document.getElementById('loginFields').disabled, false);
  ui.document.getElementById('username').value = 'synthetic-user';
  ui.document.getElementById('password').value = 'synthetic-test-password';
  const event = new ui.window.Event('submit', { cancelable: true, bubbles: true });
  ui.document.getElementById('loginForm').dispatchEvent(event);
  assert.equal(event.defaultPrevented, true);
  await settle(() => calls.some((c) => c.options.method === 'POST'));
  const request = calls.find((c) => c.options.method === 'POST');
  assert.equal(request.url, '/api/v1/auth/login');
  assert.equal(request.options.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(request.options.body), {
    username: 'synthetic-user',
    password: 'synthetic-test-password',
    method: 'local',
  });
  assert.ok(calls.every((c) => !c.url.includes('synthetic-test-password')));
});

test('an accidentally exposed password is removed before login return URLs are built', async (t) => {
  const ui = await loadUi('pages/login.js', {
    html,
    url: 'https://keen.example/?next=&username=synthetic-user&password=synthetic-test-password',
    globals: { fetch: async () => ({ ok: false, status: 401 }) },
    vendor: { qs: () => null, toast: () => {} },
  });
  t.after(() => ui.close());
  assert.equal(new URL(ui.window.location.href).searchParams.has('password'), false);
  assert.equal(new URL(ui.window.location.href).searchParams.has('username'), false);
  assert.ok(!ui.document.getElementById('next').value.includes('synthetic-test-password'));
});

test('pre-authentication asset routes cover the login page and shared module dependency', async () => {
  const conf = await readFile(new URL('../nginx/default.conf', import.meta.url), 'utf8');
  for (const asset of ['/help-launcher.js', '/demo-notice.js', '/favicon.svg']) {
    const block = conf.match(
      new RegExp('location\\s*=\\s*' + asset.replaceAll('.', '\\.') + '\\s*\\{([^}]+)\\}'),
    );
    assert.ok(block, `Missing public static asset route: ${asset}`);
    assert.ok(!block[1].includes('auth_request'));
    assert.match(block[1], /try_files \$uri =404/);
  }
  const login = await readFile(new URL('../public/pages/login.js', import.meta.url), 'utf8');
  assert.match(login, /from '\/vendor\/mosp-design-system\/app\.js'/);
});
