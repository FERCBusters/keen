import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { loadUi } from './support/load-ui.mjs';
import { page, settle } from './support/page-fixture.mjs';

const accountHtml = await readFile(new URL('../public/account.html', import.meta.url), 'utf8');

async function securityPanel(t, { mfa, start } = {}) {
  const requests = [];
  const navigations = [];
  const fetch = async (url, init = {}) => {
    requests.push({ url, init });
    if (url === '/api/v1/me/mfa') return mfa();
    if (url === '/api/v1/auth/mfa/manage/start') return start(JSON.parse(init.body));
    throw new Error('Unexpected fetch ' + url);
  };
  const ui = await loadUi('pages/account-security.js', {
    html: accountHtml,
    url: 'https://keen.example/account.html',
    globals: { fetch, location: { assign: (u) => navigations.push(u) } },
  });
  t.after(ui.close);
  const el = (id) => ui.document.getElementById(id);
  await settle(() => requests.length && !/Loading/i.test(el('securitySummary').textContent));
  await new Promise((r) => setTimeout(r, 5));
  return { ui, el, requests, navigations };
}
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });

test('account security summarises enabled factors and the administrator requirement', async (t) => {
  const { el, requests } = await securityPanel(t, {
    mfa: () =>
      json(200, {
        local_enabled: true,
        enabled: true,
        totp: true,
        credentials: [{}, {}],
        recovery_codes_remaining: 7,
        required: true,
      }),
  });
  assert.equal(
    el('securitySummary').textContent,
    'Two-factor authentication is enabled. Authenticator app; 2 security keys/passkeys; 7 recovery codes remaining. Your administrator requires a second factor.',
  );
  assert.equal(requests[0].init.credentials, 'same-origin');
  assert.equal(el('securityForm').hidden, false);
});

test('account security explains disabled password sign-in and load failures', async (t) => {
  const sso = await securityPanel(t, { mfa: () => json(200, { local_enabled: false }) });
  assert.match(sso.el('securitySummary').textContent, /Password sign-in is disabled/);
  assert.equal(sso.el('securityForm').hidden, true);
  const off = await securityPanel(t, {
    mfa: () => json(200, { local_enabled: true, enabled: false, credentials: [] }),
  });
  assert.equal(
    off.el('securitySummary').textContent,
    'Two-factor authentication is not configured for password sign-in.',
  );
  const failed = await securityPanel(t, { mfa: () => json(500, {}) });
  assert.equal(failed.el('securitySummary').textContent, 'Could not load security settings.');
  assert.equal(failed.el('securityForm').hidden, true);
});

test('factor management reauthenticates with the password in the request body and clears it', async (t) => {
  let attempt = 0;
  const { ui, el, requests, navigations } = await securityPanel(t, {
    mfa: () => json(200, { local_enabled: true, enabled: false, credentials: [] }),
    start: (body) =>
      ++attempt === 1
        ? json(401, { detail: 'Incorrect password' })
        : attempt === 2
          ? json(400, { detail: [{}] })
          : json(200, { ok: true, body }),
  });
  el('securityPassword').value = 'wrong-password';
  el('securityForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => el('securitySummary').textContent === 'Incorrect password');
  // The password is never left in the field or placed in a URL.
  assert.equal(el('securityPassword').value, '');
  assert.equal(requests[1].url, '/api/v1/auth/mfa/manage/start');
  assert.equal(requests[1].init.method, 'POST');
  assert.deepEqual(JSON.parse(requests[1].init.body), { password: 'wrong-password' });
  assert.equal(navigations.length, 0);
  el('securityPassword').value = 'x';
  el('securityForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => el('securitySummary').textContent === 'Verification failed.');
  el('securityPassword').value = 'right-password';
  el('securityForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => navigations.length);
  assert.deepEqual(navigations, ['/mfa.html?next=%2Faccount.html%23security']);
  assert.equal(el('securityPassword').value, '');
  assert.equal(el('securityForm').querySelector('button').disabled, false);
});

async function ssoEmails(t, options = {}) {
  const state = {
    addresses: options.addresses ?? [{ email: '<b>alice</b>@example.test' }],
    posted: [],
  };
  const ui = await page(t, 'sso-emails', {
    htmlName: 'account',
    globals: options.globals,
    get: async (u) => {
      if (u.pathname === '/api/v1/me/sso-emails') {
        if (options.fail) throw new Error('Session expired');
        return {
          smtp_configured: options.smtp ?? true,
          addresses: structuredClone(state.addresses),
        };
      }
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      state.posted.push({ path: u.pathname, body: JSON.parse(JSON.stringify(b)) });
      if (options.postFail) throw new Error('Reauthenticate first');
      if (u.pathname.endsWith('/remove')) state.addresses = [];
      return { already_verified: b.email === 'known@example.test' };
    },
  });
  await settle(() => ui.el('ssoEmailList').children.length || ui.el('ssoEmailStatus').textContent);
  return { ui, state };
}

test('verified SSO addresses are listed as text and removed after confirmation', async (t) => {
  const prompts = [];
  const { ui, state } = await ssoEmails(t, {
    globals: { confirm: (m) => (prompts.push(m), true) },
  });
  assert.equal(ui.el('ssoEmailForm').hidden, false);
  assert.match(
    ui.el('ssoEmailStatus').textContent,
    /requires a sign-in within the last five minutes/,
  );
  const item = ui.el('ssoEmailList').querySelector('li');
  assert.equal(item.querySelector('span').textContent, '<b>alice</b>@example.test - verified');
  assert.equal(item.querySelector('b'), null);
  item.querySelector('button').click();
  await settle(() => /No verified SSO email addresses/.test(ui.el('ssoEmailList').textContent));
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/me/sso-emails/remove',
    body: { email: '<b>alice</b>@example.test' },
  });
  assert.match(prompts[0], /Existing linked sign-in identities will remain active/);
});

test('SSO email verification requests report the outcome', async (t) => {
  const { ui, state } = await ssoEmails(t, { addresses: [] });
  ui.el('ssoEmail').value = 'new@example.test';
  ui.el('ssoEmailForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => /Verification email sent/.test(ui.el('ssoEmailStatus').textContent));
  assert.deepEqual(state.posted[0], {
    path: '/api/v1/me/sso-emails/request',
    body: { email: 'new@example.test' },
  });
  ui.el('ssoEmail').value = 'known@example.test';
  ui.el('ssoEmailForm').dispatchEvent(new ui.window.Event('submit', { cancelable: true }));
  await settle(() => ui.el('ssoEmailStatus').textContent === 'This address is already verified.');
  assert.equal(ui.el('ssoEmailForm').querySelector('button').disabled, false);
});

test('SSO email management requires SMTP and reports failures', async (t) => {
  const noSmtp = await ssoEmails(t, { smtp: false });
  assert.equal(noSmtp.ui.el('ssoEmailForm').hidden, true);
  assert.match(noSmtp.ui.el('ssoEmailStatus').textContent, /must configure SMTP/);
  const failed = await ssoEmails(t, { fail: true });
  assert.equal(failed.ui.el('ssoEmailStatus').textContent, 'Session expired');
  const declined = await ssoEmails(t, { globals: { confirm: () => false } });
  declined.ui.el('ssoEmailList').querySelector('button').click();
  assert.equal(declined.state.posted.length, 0);
  const rejected = await ssoEmails(t, { postFail: true, globals: { confirm: () => true } });
  const button = rejected.ui.el('ssoEmailList').querySelector('button');
  button.click();
  await settle(() => rejected.ui.el('ssoEmailStatus').textContent === 'Reauthenticate first');
  assert.equal(button.disabled, false);
  rejected.ui
    .el('ssoEmailForm')
    .dispatchEvent(new rejected.ui.window.Event('submit', { cancelable: true }));
  await settle(() => rejected.state.posted.length === 2);
});

async function confirmPage(t, fragment, options = {}) {
  const posted = [];
  const ui = await page(t, 'sso-email-confirm', {
    htmlName: 'sso-email',
    query: fragment,
    get: async (u) => {
      throw new Error('Unexpected GET ' + u);
    },
    post: async (u, b) => {
      posted.push({ path: u.pathname, body: JSON.parse(JSON.stringify(b)) });
      if (options.fail) throw new Error('Link expired.');
      return { email: '<i>alice</i>@example.test' };
    },
  });
  return { ui, posted };
}

test('the SSO email proof token is read from the fragment and removed from the address bar', async (t) => {
  const { ui, posted } = await confirmPage(t, '#token=proof-123');
  // The card sits in the middle of the window, as on the sign-in page.
  assert.ok(ui.document.querySelector('main').classList.contains('my-auto'));
  // The one-time token leaves browser history before any request is made.
  assert.equal(ui.window.location.hash, '');
  assert.doesNotMatch(ui.window.location.href, /proof-123/);
  assert.equal(ui.el('confirmEmail').hidden, false);
  ui.el('confirmEmail').click();
  await settle(() => posted.length && ui.el('confirmEmail').hidden);
  assert.deepEqual(posted[0], {
    path: '/api/v1/me/sso-emails/confirm',
    body: { token: 'proof-123' },
  });
  assert.equal(
    ui.el('emailProofStatus').textContent,
    '<i>alice</i>@example.test is verified and available for SSO linking.',
  );
  assert.equal(ui.el('emailProofStatus').querySelector('i'), null);
});

test('the SSO email confirmation page explains missing and rejected tokens', async (t) => {
  const missing = await confirmPage(t, '');
  assert.equal(missing.ui.el('confirmEmail').hidden, true);
  assert.match(
    missing.ui.el('emailProofStatus').textContent,
    /Open the verification link from your email/,
  );
  const query = await confirmPage(t, '&token=in-query');
  assert.equal(query.ui.el('confirmEmail').hidden, true);
  const rejected = await confirmPage(t, '#token=old', { fail: true });
  rejected.ui.el('confirmEmail').click();
  await settle(() =>
    /Link expired\. Sign into the requesting account/.test(
      rejected.ui.el('emailProofStatus').textContent,
    ),
  );
  assert.equal(rejected.ui.el('confirmEmail').disabled, false);
});
