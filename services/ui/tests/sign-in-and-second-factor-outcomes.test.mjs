// Sign-in, the second factor and account security: where each outcome leads,
// what is refused and what the page says, including when the network or the
// browser fails part-way.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const loginHtml = await readFile(new URL('../public/login.html', import.meta.url), 'utf8');
const mfaHtml = await readFile(new URL('../public/mfa.html', import.meta.url), 'utf8');
const accountHtml = await readFile(new URL('../public/account.html', import.meta.url), 'utf8');
const reply = (status, body) => ({ ok: status < 400, status, json: async () => body });
const pause = (ms = 10) => new Promise((resolve) => setTimeout(resolve, ms));

// The sign-in page with its fetch answered by `answer(target, options)`.
async function login(t, { answer, path = '/', next = null }) {
  const requests = [];
  const navigated = [];
  const url = new URL(path, 'https://keen.example');
  const location = {
    href: url.href,
    origin: url.origin,
    pathname: url.pathname,
    search: url.search,
    hash: url.hash,
    assign: (target) => navigated.push(['assign', target]),
    replace: (target) => navigated.push(['replace', target]),
  };
  const ui = await loadUi('pages/login.js', {
    html: loginHtml,
    url: url.href,
    globals: {
      location,
      fetch: async (target, options = {}) => {
        requests.push({ target, options });
        return answer(target, options, requests);
      },
    },
    vendor: {
      qs: (key, fallback) => (key === 'next' && next !== null ? next : fallback),
      toast: (el, message, tone) => {
        el.textContent = message;
        el.dataset.tone = tone;
      },
    },
  });
  t.after(() => ui.close());
  const el = (id) => ui.document.getElementById(id);
  const submit = (username, password) => {
    el('username').value = username;
    el('password').value = password;
    el('loginForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  };
  return { ...ui, el, requests, navigated, submit };
}

const meCalls = (ui) => ui.requests.filter((r) => r.target === '/api/v1/me').length;

test('a signed-in visitor opening the home page goes to their landing page', async (t) => {
  const ui = await login(t, {
    path: '/index.html',
    answer: (target) =>
      target === '/api/v1/me'
        ? reply(200, { preferences: { landing_page: '/risks.html?view=mine' } })
        : assert.fail('Unexpected fetch ' + target),
  });
  await settle(() => ui.navigated.length === 1);
  assert.deepEqual(ui.navigated, [['replace', '/risks.html?view=mine']]);
  assert.equal(meCalls(ui), 2);
});

test('a session check that cannot reach the server still offers sign-in', async (t) => {
  const ui = await login(t, {
    answer: (target) => {
      if (target === '/api/v1/me') throw new TypeError('Failed to fetch');
      if (target === '/api/v1/auth/methods')
        return reply(200, {
          local_enabled: true,
          sso_providers: [
            { key: 'google', label: 'Google', start_url: '/api/v1/auth/sso/google/start' },
          ],
        });
      return assert.fail('Unexpected fetch ' + target);
    },
  });
  await settle(() => ui.el('ssoButtons').children.length === 1);
  assert.deepEqual(ui.navigated, []);
  assert.equal(ui.el('ssoSection').classList.contains('d-none'), false);
  const button = ui.el('ssoButtons').firstElementChild;
  assert.equal(button.textContent, 'Continue with Google');
  assert.equal(button.querySelector('i').className, 'bi bi-google me-1');
});

test('providers without a usable label are offered as single sign-on', async (t) => {
  const ui = await login(t, {
    answer: (target) => {
      if (target === '/api/v1/me') return reply(401, {});
      return reply(200, {
        local_enabled: true,
        sso_providers: [
          { key: ' GitHub ', start_url: '/api/v1/auth/sso/github/start' },
          { label: '   ', start_url: '/api/v1/auth/sso/oidc/start' },
        ],
      });
    },
  });
  await settle(() => ui.el('ssoButtons').children.length === 2);
  assert.deepEqual(
    [...ui.el('ssoButtons').children].map((b) => [b.textContent, b.querySelector('i').className]),
    [
      ['Continue with single sign-on', 'bi bi-github me-1'],
      ['Continue with single sign-on', 'bi bi-shield-lock me-1'],
    ],
  );
});

test('after signing in, the landing page lookup falls back to home when the session cannot be read', async (t) => {
  const outcomes = [
    // The refused session's body is never read as an account.
    () => reply(401, { preferences: { landing_page: '/risks.html' } }),
    () => {
      throw new TypeError('Failed to fetch');
    },
  ];
  for (const landing of outcomes) {
    let signedIn = false;
    const ui = await login(t, {
      path: '/index.html',
      answer: (target) => {
        if (target === '/api/v1/me') return signedIn ? landing() : reply(401, {});
        if (target === '/api/v1/auth/methods') return reply(200, { local_enabled: true });
        if (target === '/api/v1/auth/login') {
          signedIn = true;
          return reply(200, { ok: true });
        }
        return assert.fail('Unexpected fetch ' + target);
      },
    });
    await settle(() => ui.el('loginFields').disabled === false && ui.requests.length === 2);
    ui.submit('ada', 'synthetic-password');
    await settle(() => ui.navigated.length === 1);
    assert.deepEqual(ui.navigated, [['replace', '/']]);
    assert.equal(ui.el('status').textContent, '');
    assert.equal(meCalls(ui), 2);
  }
});

test('a refused sign-in without a JSON reply reports its HTTP status', async (t) => {
  const ui = await login(t, {
    answer: (target) => {
      if (target === '/api/v1/me') return reply(401, {});
      if (target === '/api/v1/auth/methods') return reply(200, { local_enabled: true });
      return {
        ok: false,
        status: 502,
        json: async () => {
          throw new SyntaxError('Unexpected token < in JSON');
        },
      };
    },
  });
  await settle(() => ui.el('loginFields').disabled === false && ui.requests.length === 2);
  ui.submit('ada', 'synthetic-password');
  await settle(() => /Login failed/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Login failed: HTTP 502');
  assert.equal(ui.el('status').dataset.tone, 'danger');
  assert.deepEqual(ui.navigated, []);
  // The password stays only in the request body.
  assert.ok(!ui.el('status').textContent.includes('synthetic-password'));
});

test('a username of spaces is refused before anything is sent', async (t) => {
  const ui = await login(t, {
    answer: (target) =>
      target === '/api/v1/me' ? reply(401, {}) : reply(200, { local_enabled: true }),
  });
  await settle(() => ui.el('loginFields').disabled === false && ui.requests.length === 2);
  ui.submit('   ', 'synthetic-password');
  assert.equal(ui.el('status').textContent, 'Username and password are required');
  assert.equal(ui.el('status').dataset.tone, 'warning');
  assert.ok(!ui.requests.some((r) => r.target === '/api/v1/auth/login'));
});

// The second-factor page, answering each POST /api/v1/auth/mfa/<path>.
async function mfaPage(t, { states, handlers = {}, search = '', storage, globals = {}, setup }) {
  const posts = [];
  const navigations = [];
  const confirmations = [];
  let stateIndex = 0;
  const ui = await loadUi('pages/mfa.js', {
    html: mfaHtml,
    url: 'https://keen.example/mfa.html' + search,
    setup,
    modules: {
      'pages/confirm-modal.js': {
        confirmAction: async (options) => {
          confirmations.push(options);
          return handlers.confirm ? handlers.confirm(options) : true;
        },
      },
    },
    globals: {
      fetch: async (url, init) => {
        const path = url.replace('/api/v1/auth/mfa/', '');
        const body = JSON.parse(init.body);
        posts.push({ path, body });
        if (path === 'state')
          return reply(200, structuredClone(states[Math.min(stateIndex++, states.length - 1)]));
        const handler = handlers[path];
        if (!handler) throw new Error('Unexpected MFA call ' + path);
        const result = await handler(body);
        return Array.isArray(result) ? reply(...result) : reply(200, result);
      },
      atob,
      btoa,
      Uint8Array,
      localStorage: storage,
      location: {
        origin: 'https://keen.example',
        search,
        replace: (u) => navigations.push(['replace', u]),
        assign: (u) => navigations.push(['assign', u]),
      },
      prompt: () => 'Work key',
      ...globals,
    },
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById(id);
  await settle(() => posts.length && !el('intro').textContent.startsWith('Loading'));
  await pause(5);
  return { ...ui, el, posts, navigations, confirmations };
}

const verifyState = (overrides = {}) => ({
  stage: 'verify',
  purpose: 'login',
  username: 'alice',
  credentials: [],
  totp: true,
  recovery_codes_remaining: 4,
  required: true,
  ...overrides,
});
const manageState = (overrides = {}) => ({
  stage: 'manage',
  purpose: 'manage',
  username: 'alice',
  credentials: [{ id: 'k1', name: 'YubiKey' }],
  totp: true,
  recovery_codes_remaining: 3,
  required: true,
  ...overrides,
});
const blockedStorage = {
  getItem() {
    throw new DOMException('The operation is insecure.', 'SecurityError');
  },
  setItem() {
    throw new DOMException('The operation is insecure.', 'SecurityError');
  },
};
const b64u = (bytes) => Buffer.from(bytes).toString('base64url');
function keyCredential() {
  return {
    id: 'cred-1',
    rawId: new Uint8Array([1, 2, 3]).buffer,
    type: 'public-key',
    response: {
      clientDataJSON: new Uint8Array([123, 125]).buffer,
      authenticatorData: new Uint8Array([9]).buffer,
      signature: new Uint8Array([7]).buffer,
    },
    getClientExtensionResults: () => ({}),
  };
}

test('blocked browser storage neither stops verification nor finishing sign-in', async (t) => {
  const ui = await mfaPage(t, {
    states: [verifyState(), { ...verifyState(), stage: 'ready' }],
    storage: blockedStorage,
    search: '?next=/controls.html',
    handlers: { 'verify-code': () => ({ ok: true }), finish: () => ({ ok: true }) },
  });
  // Without a remembered preference the authenticator app is asked for.
  assert.equal(ui.el('codeForm').hidden, false);
  assert.equal(ui.el('codeLabel').textContent, 'Six-digit authenticator code');
  assert.equal(ui.el('message').className, 'alert d-none');
  ui.el('code').value = '123456';
  ui.el('codeForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.navigations.length);
  assert.deepEqual(ui.navigations, [['replace', '/controls.html']]);
  assert.deepEqual(
    ui.posts.map((p) => p.path),
    ['state', 'verify-code', 'state', 'finish'],
  );
});

test('cancel is ignored while a code is being checked, so the two never race', async (t) => {
  let release;
  const ui = await mfaPage(t, {
    states: [verifyState(), { ...verifyState(), stage: 'ready' }],
    handlers: {
      'verify-code': () => new Promise((resolve) => (release = () => resolve({ ok: true }))),
      finish: () => ({ ok: true }),
      cancel: () => assert.fail('Cancel must wait for the check'),
    },
  });
  ui.el('code').value = '123456';
  ui.el('codeForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => typeof release === 'function');
  const click = new ui.window.MouseEvent('click', { cancelable: true, bubbles: true });
  ui.el('cancel').dispatchEvent(click);
  // The link does not leave the page either.
  assert.equal(click.defaultPrevented, true);
  await pause();
  assert.deepEqual(
    ui.posts.map((p) => p.path),
    ['state', 'verify-code'],
  );
  release();
  await settle(() => ui.navigations.length);
  assert.deepEqual(ui.navigations, [['replace', '/']]);
});

test('choosing a security key from the alternatives verifies with it', async (t) => {
  const ui = await mfaPage(t, {
    states: [
      verifyState({ credentials: [{ id: 'k1', name: 'Key' }] }),
      { ...verifyState(), stage: 'ready' },
    ],
    // The authenticator app was used last time, so it is asked for first.
    storage: { getItem: () => 'totp', setItem() {} },
    handlers: {
      'webauthn/options': () => ({ challenge: b64u([4, 2]) }),
      'webauthn/verify': () => ({ ok: true }),
      finish: () => ({ ok: true }),
    },
    setup(window) {
      window.PublicKeyCredential = function () {};
    },
    globals: { navigator: { credentials: { get: async () => keyCredential() } } },
  });
  assert.equal(ui.el('codeForm').hidden, false);
  assert.ok(!ui.posts.some((p) => p.path === 'webauthn/options'));
  ui.el('showAlternatives').click();
  ui.el('chooseKey').click();
  await settle(() => ui.navigations.length);
  assert.equal(ui.el('keyPrompt').hidden, false);
  assert.equal(ui.el('codeForm').hidden, true);
  assert.deepEqual(
    ui.posts.map((p) => p.path),
    ['state', 'webauthn/options', 'webauthn/verify', 'state', 'finish'],
  );
  assert.equal(ui.posts[2].body.credential.id, 'cred-1');
  assert.deepEqual(ui.navigations, [['replace', '/']]);
});

test('a key registration the browser abandons is reported and registers nothing', async (t) => {
  const ui = await mfaPage(t, {
    states: [manageState()],
    handlers: { 'webauthn/register-options': () => ({ challenge: b64u([5]) }) },
    setup(window) {
      window.PublicKeyCredential = function () {};
    },
    globals: { navigator: { credentials: { create: async () => null } } },
  });
  ui.el('addKey').click();
  await settle(() => ui.el('message').textContent === 'Registration was cancelled.');
  assert.match(ui.el('message').className, /alert-danger/);
  assert.ok(!ui.posts.some((p) => p.path === 'webauthn/register'));
  assert.equal(ui.el('codesPanel').hidden, true);
  assert.equal(ui.el('addKey').disabled, false);
});

test('declining to replace recovery codes keeps the current ones', async (t) => {
  const ui = await mfaPage(t, {
    states: [manageState()],
    handlers: {
      confirm: () => false,
      'recovery/regenerate': () => assert.fail('No codes may be replaced'),
    },
  });
  ui.el('regenerate').click();
  await settle(() => ui.confirmations.length === 1);
  await pause();
  assert.equal(ui.confirmations[0].typed, 'Replace');
  assert.deepEqual(
    ui.posts.map((p) => p.path),
    ['state'],
  );
  assert.equal(ui.el('codesPanel').hidden, true);
  assert.equal(ui.el('regenerate').disabled, false);
});

test('Continue stays refused until replacement recovery codes are marked as saved', async (t) => {
  const ui = await mfaPage(t, {
    states: [manageState()],
    handlers: {
      'recovery/regenerate': () => ({ recovery_codes: ['code-1', 'code-2'] }),
      finish: () => ({ ok: true }),
    },
  });
  ui.el('regenerate').click();
  await settle(() => ui.el('recoveryCodes').textContent === 'code-1\ncode-2');
  await settle(() => ui.el('regenerate').disabled === false);
  assert.equal(ui.el('finish').disabled, true);
  // Even a click that reaches the disabled button finishes nothing.
  ui.el('finish').dispatchEvent(new ui.window.MouseEvent('click', { bubbles: true }));
  await pause();
  assert.ok(!ui.posts.some((p) => p.path === 'finish'));
  assert.deepEqual(ui.navigations, []);
  ui.el('savedCodes').checked = true;
  ui.el('savedCodes').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('finish').disabled, false);
  ui.el('finish').click();
  await settle(() => ui.navigations.length);
  assert.deepEqual(ui.navigations, [['replace', '/account.html#security']]);
});

async function securityPanel(t, state) {
  const ui = await loadUi('pages/account-security.js', {
    html: accountHtml,
    url: 'https://keen.example/account.html',
    globals: {
      fetch: async (url) =>
        url === '/api/v1/me/mfa' ? reply(200, state) : assert.fail('Unexpected fetch ' + url),
      location: { assign: () => assert.fail('No navigation expected') },
    },
  });
  t.after(ui.close);
  const summary = ui.document.getElementById('securitySummary');
  await settle(() => !/Loading/i.test(summary.textContent) && summary.textContent);
  return summary;
}

test('account security leaves out the authenticator app when only keys are enrolled', async (t) => {
  const summary = await securityPanel(t, {
    local_enabled: true,
    enabled: true,
    totp: false,
    credentials: [{ id: 'k1' }],
    recovery_codes_remaining: 5,
    required: false,
  });
  assert.equal(
    summary.textContent,
    'Two-factor authentication is enabled. 1 security keys/passkeys; 5 recovery codes remaining.',
  );
});
