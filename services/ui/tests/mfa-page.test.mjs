import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const html = await readFile(new URL('../public/mfa.html', import.meta.url), 'utf8');
const b64u = (bytes) => Buffer.from(bytes).toString('base64url');

// The MFA page talks to the API with fetch directly. Each test supplies the
// server responses; unexpected endpoints fail the test.
async function mfaPage(
  t,
  { states, handlers = {}, search = '', storage = {}, globals = {}, setup } = {},
) {
  const posts = [];
  const navigations = [];
  const store = new Map(Object.entries(storage));
  let stateIndex = 0;
  const reply = (status, body) => ({ ok: status < 400, status, json: async () => body });
  const fetch = async (url, init) => {
    assert.equal(init.method, 'POST');
    assert.equal(init.credentials, 'same-origin');
    const path = url.replace('/api/v1/auth/mfa/', '');
    const body = JSON.parse(init.body);
    posts.push({ path, body });
    if (path === 'state') {
      const s = states[Math.min(stateIndex++, states.length - 1)];
      return reply(200, structuredClone(s));
    }
    const handler = handlers[path];
    if (!handler) throw new Error('Unexpected MFA call ' + path);
    const result = await handler(body);
    return Array.isArray(result) ? reply(...result) : reply(200, result);
  };
  const ui = await loadUi('pages/mfa.js', {
    html,
    url: 'https://keen.example/mfa.html' + search,
    setup,
    globals: {
      fetch,
      atob,
      btoa,
      Uint8Array,
      localStorage: {
        getItem: (k) => (store.has(k) ? store.get(k) : null),
        setItem: (k, v) => store.set(k, String(v)),
      },
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
  await new Promise((r) => setTimeout(r, 5));
  return { ...ui, el, posts, navigations, store };
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
const readyState = { ...verifyState(), stage: 'ready' };

test('a TOTP-only account is asked for its authenticator code and completes sign-in', async (t) => {
  const ui = await mfaPage(t, {
    states: [verifyState(), readyState],
    search: '?next=/events.html%3Fq%3Dx',
    handlers: { 'verify-code': () => ({ ok: true }), finish: () => ({ ok: true }) },
  });
  assert.equal(ui.el('verify').hidden, false);
  assert.equal(ui.el('manage').hidden, true);
  assert.equal(ui.el('codeForm').hidden, false);
  assert.equal(ui.el('keyPrompt').hidden, true);
  assert.equal(ui.el('codeLabel').textContent, 'Six-digit authenticator code');
  assert.equal(ui.el('code').inputMode, 'numeric');
  assert.equal(ui.el('chooseKey').hidden, true);
  ui.el('code').value = '123456';
  ui.el('codeForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.navigations.length);
  assert.deepEqual(
    ui.posts.map((p) => p.path),
    ['state', 'verify-code', 'state', 'finish'],
  );
  assert.deepEqual(ui.posts[1].body, { kind: 'totp', code: '123456' });
  // The successful method is remembered for this browser and user.
  assert.equal(ui.store.get('keen.mfa.method:alice'), 'totp');
  assert.deepEqual(ui.navigations, [['replace', '/events.html?q=x']]);
});

for (const next of ['https://evil.example/', '//evil.example/x', '/mfa.html', '/\\evil.example'])
  test('post-MFA navigation ignores unsafe next value ' + JSON.stringify(next), async (t) => {
    const ui = await mfaPage(t, {
      states: [verifyState(), readyState],
      search: '?next=' + encodeURIComponent(next),
      handlers: { 'verify-code': () => ({}), finish: () => ({}) },
    });
    ui.el('code').value = '000000';
    ui.el('codeForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
    await settle(() => ui.navigations.length);
    assert.deepEqual(ui.navigations, [['replace', '/']]);
  });

test('rejected codes show the server message, offer alternatives and do not finish', async (t) => {
  let attempts = 0;
  const ui = await mfaPage(t, {
    states: [verifyState()],
    handlers: {
      'verify-code': () =>
        ++attempts === 1 ? [400, { detail: 'Invalid code' }] : [400, { detail: [{ msg: 'x' }] }],
    },
  });
  ui.el('code').value = '111111';
  ui.el('codeForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('message').textContent === 'Invalid code');
  assert.match(ui.el('message').className, /alert-danger/);
  assert.equal(ui.el('alternatives').hidden, false);
  assert.equal(ui.el('code').value, '111111');
  // Structured validation details are replaced with a generic message.
  ui.el('codeForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('message').textContent === 'Verification failed. Please start again.');
  assert.ok(!ui.posts.some((p) => p.path === 'finish'));
  assert.deepEqual(ui.navigations, []);
  assert.equal(ui.store.size, 0);
});

test('recovery codes complete verification but are never remembered as the preferred method', async (t) => {
  const ui = await mfaPage(t, {
    states: [verifyState({ totp: false }), readyState],
    handlers: { 'verify-code': () => ({}), finish: () => ({}) },
  });
  assert.equal(ui.el('codeLabel').textContent, 'Recovery code');
  assert.equal(ui.el('code').inputMode, 'text');
  ui.el('code').value = 'abcd-efgh';
  ui.el('codeForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.navigations.length);
  assert.deepEqual(ui.posts[1].body, { kind: 'recovery', code: 'abcd-efgh' });
  assert.equal(ui.store.size, 0);
});

function fakeCredential() {
  return {
    id: 'cred-1',
    rawId: new Uint8Array([1, 2, 3]).buffer,
    type: 'public-key',
    response: {
      clientDataJSON: new Uint8Array([123, 125]).buffer,
      authenticatorData: new Uint8Array([9, 9]).buffer,
      signature: new Uint8Array([7]).buffer,
      userHandle: null,
    },
    getClientExtensionResults: () => ({}),
  };
}

test('security keys are tried first and their assertion is sent base64url-encoded', async (t) => {
  let requested;
  const ui = await mfaPage(t, {
    states: [verifyState({ credentials: [{ id: 'k1', name: 'YubiKey' }] }), readyState],
    handlers: {
      'webauthn/options': () => ({
        challenge: b64u([0xfb, 0xff]),
        allowCredentials: [{ id: b64u([1, 2, 3]), type: 'public-key' }],
      }),
      'webauthn/verify': () => ({ ok: true }),
      finish: () => ({}),
    },
    setup(window) {
      window.PublicKeyCredential = function () {};
    },
    globals: {
      navigator: {
        credentials: {
          get: async (opts) => {
            requested = opts;
            return fakeCredential();
          },
        },
      },
    },
  });
  await settle(() => ui.navigations.length);
  assert.deepEqual([...requested.publicKey.challenge], [0xfb, 0xff]);
  assert.deepEqual([...requested.publicKey.allowCredentials[0].id], [1, 2, 3]);
  const sent = ui.posts.find((p) => p.path === 'webauthn/verify').body.credential;
  assert.deepEqual(sent, {
    id: 'cred-1',
    rawId: 'AQID',
    type: 'public-key',
    response: { clientDataJSON: 'e30', authenticatorData: 'CQk', signature: 'Bw' },
    clientExtensionResults: {},
  });
  assert.equal(ui.store.get('keen.mfa.method:alice'), 'webauthn');
});

test('a remembered authenticator-app preference is used before a registered key', async (t) => {
  const ui = await mfaPage(t, {
    states: [verifyState({ credentials: [{ id: 'k1', name: 'Key' }] })],
    storage: { 'keen.mfa.method:alice': 'totp' },
  });
  assert.equal(ui.el('codeForm').hidden, false);
  assert.ok(!ui.posts.some((p) => p.path === 'webauthn/options'));
});

test('cancelled or unsupported security keys fall back to alternative methods', async (t) => {
  const cancelled = await mfaPage(t, {
    states: [verifyState({ credentials: [{ id: 'k1', name: 'Key' }] })],
    handlers: { 'webauthn/options': () => ({ challenge: b64u([1]) }) },
    setup(window) {
      window.PublicKeyCredential = function () {};
    },
    globals: {
      navigator: {
        credentials: {
          get: async () => {
            const e = new Error('denied');
            e.name = 'NotAllowedError';
            throw e;
          },
        },
      },
    },
  });
  await settle(() => /cancelled or timed out/.test(cancelled.el('message').textContent));
  assert.equal(cancelled.el('alternatives').hidden, false);
  assert.ok(!cancelled.posts.some((p) => p.path === 'webauthn/verify'));
  // Choosing the authenticator app switches the form.
  cancelled.el('chooseTotp').click();
  assert.equal(cancelled.el('codeForm').hidden, false);
  assert.equal(cancelled.el('alternatives').hidden, true);
  cancelled.el('showAlternatives').click();
  assert.equal(cancelled.el('alternatives').hidden, false);
  cancelled.el('chooseRecovery').click();
  assert.equal(cancelled.el('codeLabel').textContent, 'Recovery code');

  const unsupported = await mfaPage(t, {
    states: [verifyState({ credentials: [{ id: 'k1', name: 'Key' }] })],
  });
  await settle(() => /cannot use security keys/.test(unsupported.el('message').textContent));
  const empty = await mfaPage(t, {
    states: [verifyState({ credentials: [{ id: 'k1', name: 'Key' }] })],
    handlers: { 'webauthn/options': () => ({ challenge: b64u([1]) }) },
    setup(window) {
      window.PublicKeyCredential = function () {};
    },
    globals: { navigator: { credentials: { get: async () => null } } },
  });
  await settle(() => /Verification was cancelled/.test(empty.el('message').textContent));
  empty.el('useKey').click();
  await settle(() => empty.posts.filter((p) => p.path === 'webauthn/options').length === 2);
});

const manageState = (overrides = {}) => ({
  stage: 'manage',
  purpose: 'manage',
  username: 'alice',
  credentials: [{ id: 'k1', name: '<b>YubiKey</b>' }],
  totp: true,
  recovery_codes_remaining: 3,
  required: true,
  ...overrides,
});

test('factor management lists authenticators as text and removes one after confirmation', async (t) => {
  const prompts = [];
  let current = manageState();
  const ui = await mfaPage(t, {
    states: [current, manageState({ totp: false })],
    handlers: { remove: () => ({ ok: true }) },
    globals: { confirm: (m) => (prompts.push(m), true) },
  });
  assert.equal(ui.el('manage').hidden, false);
  assert.equal(ui.el('verify').hidden, true);
  assert.equal(ui.el('intro').textContent, 'Choose an authenticator for your local Keen account.');
  assert.equal(ui.el('remaining').textContent, '3 recovery codes remaining.');
  assert.equal(ui.el('addTotp').hidden, true);
  const labels = [...ui.el('factors').querySelectorAll('span')].map((s) => s.textContent);
  assert.deepEqual(labels, ['Authenticator app', '<b>YubiKey</b>']);
  assert.equal(ui.el('factors').querySelector('b'), null);
  assert.equal(ui.el('finish').hidden, false);
  ui.el('factors').querySelector('button').click();
  await settle(() => ui.posts.some((p) => p.path === 'remove'));
  assert.deepEqual(ui.posts.find((p) => p.path === 'remove').body, { id: 'totp' });
  assert.equal(prompts[0], 'Remove Authenticator app?');
  await settle(() => ui.el('addTotp').hidden === false);
});

test('declining factor removal sends nothing', async (t) => {
  const ui = await mfaPage(t, { states: [manageState()], globals: { confirm: () => false } });
  ui.el('factors').querySelector('button').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.ok(!ui.posts.some((p) => p.path === 'remove'));
});

test('authenticator app enrolment shows the setup key, then requires saving recovery codes before finishing', async (t) => {
  const ui = await mfaPage(t, {
    states: [
      manageState({ stage: 'enrol', totp: false, credentials: [], recovery_codes_remaining: 0 }),
      manageState({ credentials: [] }),
    ],
    search: '?next=/account.html%23security',
    handlers: {
      'totp/start': () => ({ qr: 'data:image/png;base64,AAAA', secret: 'JBSWY3DP' }),
      'totp/confirm': (b) => {
        assert.deepEqual(b, { code: '654321' });
        return { recovery_codes: ['aaaa-bbbb', 'cccc-dddd'] };
      },
      finish: () => ({}),
    },
    globals: { navigator: { clipboard: { writeText: async () => {} } } },
  });
  // A required account with no factors cannot finish yet.
  assert.equal(ui.el('finish').hidden, true);
  assert.equal(ui.el('regenerate').hidden, true);
  ui.el('addTotp').click();
  await settle(() => ui.el('totpSetup').hidden === false);
  assert.equal(ui.el('secret').textContent, 'JBSWY3DP');
  assert.equal(ui.el('qr').getAttribute('src'), 'data:image/png;base64,AAAA');
  ui.el('confirmCode').value = '654321';
  ui.el('confirmForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('codesPanel').hidden === false);
  // The setup secret is removed from the page after confirmation.
  assert.equal(ui.el('secret').textContent, '');
  assert.equal(ui.el('qr').hasAttribute('src'), false);
  assert.equal(ui.el('totpSetup').hidden, true);
  assert.equal(ui.el('recoveryCodes').textContent, 'aaaa-bbbb\ncccc-dddd');
  await settle(() => ui.el('finish').hidden === false);
  assert.equal(ui.el('finish').disabled, true);
  ui.el('copyCodes').click();
  await settle(
    () => ui.el('message').textContent === 'Recovery codes copied. Store them securely.',
  );
  assert.equal(ui.el('finish').disabled, true);
  ui.el('savedCodes').checked = true;
  ui.el('savedCodes').dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.el('finish').disabled, false);
  ui.el('finish').click();
  await settle(() => ui.navigations.length);
  assert.deepEqual(ui.navigations, [['replace', '/account.html#security']]);
});

test('managing factors returns to account security by default', async (t) => {
  const ui = await mfaPage(t, { states: [manageState()], handlers: { finish: () => ({}) } });
  ui.el('finish').click();
  await settle(() => ui.navigations.length);
  assert.deepEqual(ui.navigations, [['replace', '/account.html#security']]);
});

test('security keys can be registered with a name and replacement recovery codes regenerated', async (t) => {
  let created;
  const ui = await mfaPage(t, {
    states: [manageState({ credentials: [] })],
    handlers: {
      'webauthn/register-options': () => ({
        challenge: b64u([5]),
        user: { id: b64u([6, 7]), name: 'alice' },
        excludeCredentials: [],
      }),
      'webauthn/register': (b) => {
        assert.equal(b.name, 'Work key');
        assert.equal(b.credential.response.attestationObject, 'AQ');
        assert.deepEqual(b.credential.response.transports, ['usb']);
        return {};
      },
      'recovery/regenerate': () => ({ recovery_codes: ['new-1'] }),
    },
    setup(window) {
      window.PublicKeyCredential = function () {};
    },
    globals: {
      confirm: () => true,
      navigator: {
        credentials: {
          create: async (opts) => {
            created = opts;
            const c = fakeCredential();
            c.response = {
              clientDataJSON: new Uint8Array([1]).buffer,
              attestationObject: new Uint8Array([1]).buffer,
              getTransports: () => ['usb'],
            };
            return c;
          },
        },
      },
    },
  });
  ui.el('addKey').click();
  await settle(() => ui.posts.some((p) => p.path === 'webauthn/register'));
  assert.deepEqual([...created.publicKey.user.id], [6, 7]);
  ui.el('regenerate').click();
  await settle(() => ui.el('recoveryCodes').textContent === 'new-1');
  assert.equal(ui.el('codesPanel').hidden, false);
});

test('registration without WebAuthn support or a name does nothing harmful', async (t) => {
  const noSupport = await mfaPage(t, { states: [manageState()] });
  noSupport.el('addKey').click();
  await settle(() => /Use a browser supporting WebAuthn/.test(noSupport.el('message').textContent));
  const unnamed = await mfaPage(t, {
    states: [manageState()],
    setup(window) {
      window.PublicKeyCredential = function () {};
    },
    globals: { prompt: () => '', confirm: () => false },
  });
  unnamed.el('addKey').click();
  unnamed.el('regenerate').click();
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(
    unnamed.posts.map((p) => p.path),
    ['state'],
  );
});

test('cancelling verification ends the ceremony and returns to sign in', async (t) => {
  const ui = await mfaPage(t, { states: [verifyState()], handlers: { cancel: () => ({}) } });
  ui.el('cancel').click();
  await settle(() => ui.navigations.length);
  assert.deepEqual(ui.navigations, [['assign', '/login.html']]);
  assert.equal(ui.posts.at(-1).path, 'cancel');
});

test('a completed ceremony shows completion text without verification inputs', async (t) => {
  const ui = await mfaPage(t, { states: [{ ...readyState, purpose: 'manage' }] });
  assert.equal(ui.el('intro').textContent, 'Verification complete.');
  assert.equal(ui.el('verify').hidden, true);
  assert.equal(ui.el('manage').hidden, true);
});
