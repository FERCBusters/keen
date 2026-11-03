// Sign-in page decisions: which methods are offered, where each one leads, and what
// happens for visitors who are already signed in or need a second factor.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { settle } from './support/page-fixture.mjs';

const html = await readFile(new URL('../public/login.html', import.meta.url), 'utf8');

async function login(t, { methods, me = null, signIn = null, path = '/', next = null } = {}) {
  const requests = [];
  const navigated = [];
  const url = new URL(path, 'https://keen.example');
  // The page reads its address and leaves by assign/replace; record both.
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
    html,
    url: url.href,
    globals: {
      location,
      fetch: async (target, options = {}) => {
        requests.push({ target, options });
        const reply = (status, body) => ({ ok: status < 400, status, json: async () => body });
        if (target === '/api/v1/me') {
          const current = typeof me === 'function' ? me() : me;
          return current ? reply(200, current) : reply(401, {});
        }
        if (target === '/api/v1/auth/methods') {
          if (methods instanceof Error) throw methods;
          return reply(200, methods);
        }
        if (target === '/api/v1/auth/login') return signIn ? signIn() : reply(401, {});
        throw new Error('Unexpected fetch ' + target);
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

const providers = [
  { key: 'github', label: 'GitHub', start_url: '/api/v1/auth/sso/github/start' },
  { key: 'oidc', label: 'Example <IdP>', start_url: '/api/v1/auth/sso/oidc/start' },
];

test('providers are offered as buttons that start sign-in and return to the page', async (t) => {
  const ui = await login(t, {
    methods: { local_enabled: true, sso_providers: providers },
    path: '/controls.html?framework=A',
  });
  await settle(() => ui.el('ssoButtons').children.length === 2);
  assert.equal(ui.el('ssoSection').classList.contains('d-none'), false);
  const buttons = [...ui.el('ssoButtons').children];
  assert.deepEqual(
    buttons.map((b) => [b.textContent, b.querySelector('i').className]),
    [
      ['Continue with GitHub', 'bi bi-github me-1'],
      ['Continue with Example <IdP>', 'bi bi-shield-lock me-1'],
    ],
  );
  assert.equal(ui.el('ssoButtons').querySelector('b, img'), null);

  buttons[0].click();
  // A second click does not start another sign-in.
  buttons[0].click();
  assert.deepEqual(ui.navigated, [
    ['assign', '/api/v1/auth/sso/github/start?next=%2Fcontrols.html%3Fframework%3DA'],
  ]);
  assert.equal(ui.el('loginForm').classList.contains('d-none'), false);
});

test('a directory sign-in offers the method choice and sends the chosen method', async (t) => {
  const ui = await login(t, {
    methods: { local_enabled: true, ldap_enabled: true, sso_providers: [] },
  });
  await settle(() => ui.el('login-method'));
  assert.deepEqual(
    [...ui.el('login-method').options].map((o) => [o.value, o.textContent]),
    [
      ['ldap', 'LDAP directory'],
      ['local', 'Local Keen account'],
    ],
  );
  assert.equal(ui.el('ssoSection').classList.contains('d-none'), true);
  ui.submit('ada', 'synthetic-password');
  await settle(() => ui.requests.some((r) => r.target === '/api/v1/auth/login'));
  const sent = ui.requests.find((r) => r.target === '/api/v1/auth/login');
  assert.equal(JSON.parse(sent.options.body).method, 'ldap');
});

test('without an interactive method the form is hidden and the page says so', async (t) => {
  const none = await login(t, {
    methods: { local_enabled: false, ldap_enabled: false, sso_providers: [] },
  });
  await settle(() => none.el('status').textContent !== '');
  assert.equal(none.el('status').textContent, 'No interactive login method is enabled.');
  assert.equal(none.el('status').dataset.tone, 'danger');
  assert.equal(none.el('loginForm').classList.contains('d-none'), true);

  // Provider sign-in alone hides the form without an error.
  const ssoOnly = await login(t, {
    methods: { local_enabled: false, ldap_enabled: false, sso_providers: providers },
  });
  await settle(() => ssoOnly.el('loginForm').classList.contains('d-none'));
  assert.equal(ssoOnly.el('status').textContent, '');
  assert.equal(ssoOnly.el('ssoButtons').children.length, 2);
});

test('an unavailable methods list keeps the local form and offers no providers', async (t) => {
  const ui = await login(t, { methods: new Error('offline') });
  await settle(() => ui.requests.some((r) => r.target === '/api/v1/auth/methods'));
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(ui.el('ssoSection').classList.contains('d-none'), true);
  assert.equal(ui.el('loginForm').classList.contains('d-none'), false);
  assert.equal(ui.el('loginFields').disabled, false);
});

test('a visitor already signed in goes to the requested page or their landing page', async (t) => {
  const landing = await login(t, {
    me: { preferences: { landing_page: '/events.html?q=ssh' } },
    methods: { local_enabled: true },
  });
  await settle(() => landing.navigated.length === 1);
  assert.deepEqual(landing.navigated, [['replace', '/events.html?q=ssh']]);
  assert.ok(!landing.requests.some((r) => r.target === '/api/v1/auth/methods'));

  const requested = await login(t, {
    me: { preferences: { landing_page: '/events.html' } },
    next: '/audits.html?id=1',
  });
  await settle(() => requested.navigated.length === 1);
  assert.deepEqual(requested.navigated, [['replace', '/audits.html?id=1']]);

  // A landing page elsewhere is never followed.
  const offsite = await login(t, {
    me: { preferences: { landing_page: 'https://elsewhere.example/' } },
  });
  await settle(() => offsite.navigated.length === 1);
  assert.deepEqual(offsite.navigated, [['replace', '/']]);
});

// Each next value resolves to the path "//evil.example/phish", which a browser
// reads as another host, so sign-in falls back to the home page.
const dotSegmentNext = [
  '/.//evil.example/phish',
  '/%2e//evil.example/phish',
  '/x/..//evil.example/phish',
];

test('a signed-in visitor is never sent off-site by a dot-segment next value', async (t) => {
  for (const next of dotSegmentNext) {
    const ui = await login(t, { me: { preferences: {} }, next });
    await settle(() => ui.navigated.length === 1);
    assert.deepEqual(ui.navigated, [['replace', '/']], next);
  }
});

test('password sign-in, the second factor and SSO keep a dot-segment next value on Keen', async (t) => {
  const reply = (body) => () => ({ ok: true, status: 200, json: async () => body });
  for (const next of dotSegmentNext) {
    let signedIn = false;
    const done = await login(t, {
      methods: { local_enabled: true, sso_providers: providers },
      me: () => (signedIn ? { preferences: {} } : null),
      signIn: () => {
        signedIn = true;
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      },
      next,
    });
    await settle(() => done.el('ssoButtons').children.length === 2);
    assert.equal(done.el('next').value, '/');
    done.el('ssoButtons').children[0].click();
    done.submit('ada', 'synthetic-password');
    await settle(() => done.navigated.length === 2);
    assert.deepEqual(
      done.navigated,
      [
        ['assign', '/api/v1/auth/sso/github/start?next=%2F'],
        ['replace', '/'],
      ],
      next,
    );

    const mfa = await login(t, {
      methods: { local_enabled: true },
      signIn: reply({ mfa_required: true }),
      next,
    });
    await settle(() => mfa.el('loginFields').disabled === false);
    mfa.submit('ada', 'synthetic-password');
    await settle(() => mfa.navigated.length === 1);
    assert.deepEqual(mfa.navigated, [['assign', '/mfa.html?next=%2F']], next);
  }
});

test('a provider start address that resolves off-site is never followed', async (t) => {
  const ui = await login(t, {
    methods: {
      local_enabled: true,
      sso_providers: [
        { key: 'oidc', label: 'IdP', start_url: '/.//evil.example/start' },
        { key: 'broken', label: 'Broken <IdP>', start_url: 'https://[' },
        providers[0],
      ],
    },
  });
  await settle(() => ui.el('ssoButtons').children.length === 3);
  const [offsite, broken, github] = ui.el('ssoButtons').children;
  const refusal = (label) =>
    `Sign-in with ${label} cannot start. Ask an administrator to check the provider's settings.`;
  // Each refusal says so, every time, and leaves the buttons usable.
  for (let attempt = 0; attempt < 2; attempt += 1) {
    ui.el('status').textContent = '';
    offsite.click();
    assert.equal(ui.el('status').textContent, refusal('IdP'));
    assert.equal(ui.el('status').dataset.tone, 'danger');
    broken.click();
    assert.equal(ui.el('status').textContent, refusal('Broken <IdP>'));
    assert.equal(ui.el('status').querySelector('*'), null);
  }
  assert.deepEqual(ui.navigated, []);
  assert.equal(offsite.disabled || broken.disabled || github.disabled, false);
  // A usable provider still starts sign-in, and only once.
  github.click();
  offsite.click();
  github.click();
  assert.deepEqual(ui.navigated, [['assign', '/api/v1/auth/sso/github/start?next=%2F']]);
});

test('sign-in continues to the second factor or the landing page', async (t) => {
  const reply = (body) => () => ({ ok: true, status: 200, json: async () => body });
  const mfa = await login(t, {
    methods: { local_enabled: true },
    signIn: reply({ mfa_required: true }),
    next: '/controls.html',
  });
  await settle(() => mfa.el('loginFields').disabled === false);
  mfa.submit('ada', 'synthetic-password');
  await settle(() => mfa.navigated.length === 1);
  assert.deepEqual(mfa.navigated, [['assign', '/mfa.html?next=%2Fcontrols.html']]);
  assert.equal(mfa.el('password').value, '');

  // Once signed in, the account's landing page is where the home page leads.
  let signedIn = false;
  const done = await login(t, {
    methods: { local_enabled: true },
    me: () => (signedIn ? { preferences: { landing_page: '/risks.html' } } : null),
    signIn: () => {
      signedIn = true;
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    },
  });
  await settle(() => done.el('loginFields').disabled === false);
  done.submit('ada', 'synthetic-password');
  await settle(() => done.navigated.length === 1);
  assert.deepEqual(done.navigated, [['replace', '/risks.html']]);
});

test('missing credentials and refused sign-ins are reported on the page', async (t) => {
  const ui = await login(t, {
    methods: { local_enabled: true },
    signIn: () => ({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Invalid username or password' }),
    }),
  });
  await settle(() => ui.el('loginFields').disabled === false);
  ui.submit('ada', '');
  assert.equal(ui.el('status').textContent, 'Username and password are required');
  assert.ok(!ui.requests.some((r) => r.target === '/api/v1/auth/login'));

  ui.submit('ada', 'wrong-password');
  await settle(() => /Login failed/.test(ui.el('status').textContent));
  assert.equal(ui.el('status').textContent, 'Login failed: Invalid username or password');
  assert.equal(ui.el('loginBtn').disabled, false);
  assert.deepEqual(ui.navigated, []);
});
